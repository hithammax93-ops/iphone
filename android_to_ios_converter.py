"""Strict Android -> iOS candidate database conversion.

This deliberately creates an *experimental candidate* using the small local
schema below.  It is not a certified WhatsApp Core Data database and never
modifies an iOS backup or its Manifest.db.
"""
import logging
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from device_manager import DeviceManager, IOS_MEDIA_DIR

logger = logging.getLogger(__name__)
APPLE_EPOCH_OFFSET = 978307200


class AndroidToIOSConverter:
    """Build a deterministic, integrity-checked experimental ChatStorage candidate."""

    # Android message types for which this experimental schema can carry a file.
    # Location/contact/etc. are deliberately rejected rather than misclassified.
    FILE_BEARING_TYPES = frozenset({1, 2, 3, 6, 7})

    def __init__(self, device_manager: DeviceManager, hex_key: str):
        self.dm = device_manager
        self.hex_key = hex_key
        self.temp_dir: Optional[str] = None
        self.media_dir: Optional[str] = None
        self.stats: Dict[str, int] = {}

    def convert(self, android_db_path: str, android_media_path: str,
                ios_backup_dir: str, is_business: bool = False,
                output_dir: Optional[str] = None) -> str:
        """Convert into a fresh candidate; ``ios_backup_dir`` is intentionally read-only."""
        del is_business  # retained for the public API
        if not os.path.isfile(android_db_path):
            raise FileNotFoundError(f"Android database not found: {android_db_path}")
        if output_dir:
            out_real = os.path.realpath(os.path.abspath(output_dir))
            db_parent = os.path.realpath(os.path.dirname(os.path.abspath(android_db_path)))
            db_real = os.path.realpath(os.path.abspath(android_db_path))
            if os.path.commonpath([out_real, db_real]) == out_real:
                raise ValueError("output_dir must not be the Android input directory")
            for protected in (ios_backup_dir, android_media_path):
                protected_real = os.path.realpath(os.path.abspath(protected))
                if os.path.commonpath([out_real, protected_real]) in (protected_real, out_real):
                    raise ValueError("output_dir must not be inside an input or iOS backup path")
            if os.path.isdir(out_real):
                unexpected = set(os.listdir(out_real)) - {"ChatStorage.sqlite", "Media", "migration_report.json"}
                if unexpected:
                    raise ValueError("output_dir contains unrelated files; refusing replacement")
        self.temp_dir = tempfile.mkdtemp(prefix="temp_convert_")
        self.media_dir = os.path.join(self.temp_dir, "Media")
        staged = os.path.join(self.temp_dir, "ChatStorage.sqlite.staged")
        try:
            source_db = android_db_path
            if str(android_db_path).lower().endswith(".crypt15"):
                source_db = os.path.join(self.temp_dir, "decrypted.sqlite")
                self.dm.decrypt_crypt15(android_db_path, source_db, self.hex_key)
            schema = self._analyze_android_schema(source_db)
            self._validate_schema(schema)
            chat_map, chats = self._read_chats(source_db, schema)
            messages = self._read_messages(source_db, schema, chat_map)
            media_links = self._read_media_links(source_db, schema, messages)
            self._create_ios_database(staged)
            self._populate(staged, chats, messages, media_links, android_media_path)
            self._validate_candidate(staged, len(chats), len(messages), len(media_links))
            os.replace(staged, os.path.join(self.temp_dir, "ChatStorage.sqlite"))
            candidate = os.path.join(self.temp_dir, "ChatStorage.sqlite")
            if output_dir:
                candidate = self._publish_candidate(candidate, output_dir)
            self.stats = {"chats": len(chats), "messages": len(messages),
                          "media": len(media_links)}
            return candidate
        except Exception:
            shutil.rmtree(self.temp_dir, ignore_errors=True)
            raise
        finally:
            decrypted = os.path.join(self.temp_dir, "decrypted.sqlite")
            if os.path.exists(decrypted):
                os.unlink(decrypted)
            if output_dir:
                shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _analyze_android_schema(self, db_path: str) -> Dict[str, List[Dict[str, Any]]]:
        with sqlite3.connect(db_path) as conn:
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            return {t: [{"name": r[1], "type": r[2], "notnull": bool(r[3]), "pk": bool(r[5])}
                        for r in conn.execute(f'PRAGMA table_info({self._quote(t)})')] for t in tables}

    @staticmethod
    def _quote(identifier):
        return '"' + str(identifier).replace('"', '""') + '"'

    @staticmethod
    def _cols(schema, table):
        return {c["name"] for c in schema.get(table, [])}

    def _validate_schema(self, schema):
        required = {
            "chat": {"_id"},
            "message": {"_id", "chat_row_id", "from_me", "timestamp", "text_data", "message_type"},
        }
        for table, cols in required.items():
            if table not in schema:
                raise ValueError(f"Unsupported Android schema: missing table {table}")
            missing = cols - self._cols(schema, table)
            if missing:
                raise ValueError(f"Unsupported Android schema: {table} missing columns {sorted(missing)}")
        chat_cols = self._cols(schema, "chat")
        if not ({"jid", "raw_string", "jid_row_id"} & chat_cols):
            raise ValueError("Unsupported chat schema: expected chat.jid, chat.raw_string, or chat.jid_row_id")
        if "jid_row_id" in chat_cols:
            if "jid" not in schema:
                raise ValueError("chat.jid_row_id requires jid table")
            if "_id" not in self._cols(schema, "jid"):
                raise ValueError("jid table missing required _id for chat join")
            if not ({"jid", "raw_string"} & self._cols(schema, "jid")):
                raise ValueError("jid table lacks jid/raw_string")
        if "message_media" in schema:
            mc = self._cols(schema, "message_media")
            if not ({"message_row_id", "message_id", "message_id"} & mc):
                raise ValueError("Unsupported message_media schema: missing message link")
            if not ({"file_path", "media_path", "path", "local_path"} & mc):
                raise ValueError("Unsupported message_media schema: missing media path")

    def _read_chats(self, db_path, schema):
        cols = self._cols(schema, "chat")
        if "jid" in cols:
            jid_expr = 'c."jid"'
            sql = 'SELECT c."_id", c."jid" FROM "chat" c ORDER BY c."_id"'
        elif "raw_string" in cols:
            jid_expr = 'c."raw_string"'
            sql = 'SELECT c."_id", c."raw_string" FROM "chat" c ORDER BY c."_id"'
        else:
            jcol = "jid" if "jid" in self._cols(schema, "jid") else "raw_string"
            sql = f'SELECT c."_id", j.{self._quote(jcol)} FROM "chat" c LEFT JOIN "jid" j ON j."_id" = c."jid_row_id" ORDER BY c."_id"'
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(sql).fetchall()
        if not rows:
            raise ValueError("Android schema contains zero chats; refusing silent empty conversion")
        seen = set(); chat_map = {}; chats = []
        for aid, jid in rows:
            if aid is None:
                raise ValueError("Chat has null id")
            if jid is None or not str(jid):
                raise ValueError(f"Chat {aid} has unmatched or empty JID")
            if aid in seen or str(jid) in {x[1] for x in chats}:
                raise ValueError("Duplicate Android chat id or JID")
            seen.add(aid); chat_map[aid] = len(chats) + 1; chats.append((aid, str(jid)))
        return chat_map, chats

    def _read_messages(self, db_path, schema, chat_map):
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute('SELECT "_id","chat_row_id","from_me","timestamp","text_data","message_type" FROM "message" ORDER BY "_id"').fetchall()
        if not rows:
            raise ValueError("Android schema contains zero messages; refusing silent empty conversion")
        result = []
        ids = set()
        for aid, chat_id, from_me, ts, text, typ in rows:
            if aid in ids or aid is None: raise ValueError("Duplicate/null Android message id")
            if chat_id not in chat_map: raise ValueError(f"Orphan message {aid}: chat {chat_id} not found")
            if ts is None: raise ValueError(f"Message {aid} has null timestamp")
            ids.add(aid); result.append((aid, chat_map[chat_id], int(bool(from_me)), self._convert_timestamp(ts), text, typ))
        return result

    def _read_media_links(self, db_path, schema, messages):
        unsupported = sorted({m[5] for m in messages if m[5] not in (None, 0) and m[5] not in self.FILE_BEARING_TYPES})
        if unsupported:
            raise ValueError(f"Unsupported non-file message type(s): {unsupported}")
        if "message_media" not in schema:
            # A non-text message without an explicit relation cannot be certified.
            if any(m[5] not in (None, 0) for m in messages):
                raise ValueError("Media messages require a supported message_media table")
            return []
        cols = self._cols(schema, "message_media")
        mid = "message_row_id" if "message_row_id" in cols else "message_id"
        pathcol = next(c for c in ("file_path", "media_path", "path", "local_path") if c in cols)
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(f'SELECT "{mid}","{pathcol}" FROM "message_media"').fetchall()
        known = {m[0] for m in messages}; links = []
        for message_id, path in rows:
            if message_id not in known: raise ValueError(f"Orphan media link for message {message_id}")
            if not path: raise ValueError(f"Missing media path for message {message_id}")
            links.append((message_id, str(path)))
        linked = {message_id for message_id, _ in links}
        missing = [m[0] for m in messages if m[5] in self.FILE_BEARING_TYPES and m[0] not in linked]
        if missing:
            raise ValueError(f"Media messages have no media link: {missing}")
        return links

    def _publish_candidate(self, candidate, output_dir):
        """Publish DB and Media as one fresh bundle, removing stale prior media."""
        output_dir = os.path.realpath(os.path.abspath(output_dir))
        parent = os.path.dirname(output_dir)
        os.makedirs(parent, exist_ok=True)
        stage = tempfile.mkdtemp(prefix=".candidate-", dir=parent)
        old = None
        try:
            shutil.copy2(candidate, os.path.join(stage, "ChatStorage.sqlite"))
            shutil.copytree(self.media_dir, os.path.join(stage, "Media"))
            if os.path.exists(output_dir):
                old = tempfile.mkdtemp(prefix=".old-candidate-", dir=parent)
                os.rmdir(old)
                os.replace(output_dir, old)
            os.replace(stage, output_dir)
            self.media_dir = os.path.join(output_dir, "Media")
            if old:
                shutil.rmtree(old, ignore_errors=True)
            return os.path.join(output_dir, "ChatStorage.sqlite")
        except Exception:
            if old and not os.path.exists(output_dir):
                os.replace(old, output_dir)
            shutil.rmtree(stage, ignore_errors=True)
            raise

    @staticmethod
    def _convert_timestamp(value):
        value = float(value)
        return (value / 1000.0 if value > 1e12 else value) - APPLE_EPOCH_OFFSET

    def _create_ios_database(self, path):
        with sqlite3.connect(path) as c:
            c.execute("PRAGMA foreign_keys=ON")
            c.executescript("""
              CREATE TABLE ZWACHATSESSION (Z_PK INTEGER PRIMARY KEY, ZCONTACTJID TEXT NOT NULL UNIQUE,
                ZLASTMESSAGEDATE REAL NOT NULL, ZARCHIVED INTEGER NOT NULL, ZUNREADCOUNT INTEGER NOT NULL,
                ZMESSAGECOUNTER INTEGER NOT NULL);
              CREATE TABLE ZWAMESSAGE (Z_PK INTEGER PRIMARY KEY, ZANDROIDID INTEGER NOT NULL UNIQUE,
                ZCHATSESSION INTEGER NOT NULL REFERENCES ZWACHATSESSION(Z_PK), ZMESSAGETYPE INTEGER,
                ZTIMESTAMP REAL NOT NULL, ZTEXT TEXT, ZFROMJID TEXT, ZTOJID TEXT, ZMEDIAITEM INTEGER,
                ZISFROMME INTEGER NOT NULL, ZSENTDATE REAL, FOREIGN KEY(ZMEDIAITEM) REFERENCES ZWAMEDIAITEM(Z_PK));
              CREATE TABLE ZWAMEDIAITEM (Z_PK INTEGER PRIMARY KEY, ZANDROIDID INTEGER NOT NULL,
                ZMESSAGE INTEGER NOT NULL REFERENCES ZWAMESSAGE(Z_PK), ZMEDIALOCALPATH TEXT NOT NULL,
                ZTHUMBNAILLOCALPATH TEXT, ZFILESIZE INTEGER NOT NULL, ZMIMETYPE TEXT, ZMEDIAURL TEXT);
            """)

    def _media_source(self, root, raw):
        root = os.path.realpath(os.path.abspath(root))
        if not os.path.isdir(root): raise FileNotFoundError(f"Media root not found: {root}")
        p = str(raw).replace('\\', '/')
        if os.path.isabs(p): candidate = os.path.realpath(p)
        else:
            while p.startswith('./'): p = p[2:]
            for prefix in ("WhatsApp/Media/", "Media/"):
                if p.startswith(prefix): p = p[len(prefix):]; break
            base = os.path.join(root, "Media") if os.path.isdir(os.path.join(root, "Media")) else root
            candidate = os.path.realpath(os.path.join(base, p))
        if os.path.commonpath([root, candidate]) != root or os.path.islink(os.path.abspath(candidate)):
            raise ValueError(f"Media path escapes media root: {raw}")
        if not os.path.isfile(candidate): raise FileNotFoundError(f"Linked media file missing: {raw}")
        return candidate

    def _populate(self, path, chats, messages, links, media_root):
        os.makedirs(self.media_dir, exist_ok=True)
        with sqlite3.connect(path) as c:
            c.execute("PRAGMA foreign_keys=ON")
            for ios_id, (_, jid) in enumerate(chats, 1):
                c.execute("INSERT INTO ZWACHATSESSION VALUES (?,?,?,?,?,?)", (ios_id, jid, 0.0, 0, 0, 0))
            msg_map = {}
            for ios_id, (aid, chat, frm, ts, text, typ) in enumerate(messages, 1):
                c.execute("INSERT INTO ZWAMESSAGE (Z_PK,ZANDROIDID,ZCHATSESSION,ZMESSAGETYPE,ZTIMESTAMP,ZTEXT,ZISFROMME,ZSENTDATE) VALUES (?,?,?,?,?,?,?,?)", (ios_id, aid, chat, typ, ts, text, frm, ts))
                msg_map[aid] = ios_id
            media_by_msg = {}
            for media_id, (aid, raw) in enumerate(links, 1):
                src = self._media_source(media_root, raw)
                rel = os.path.relpath(src, os.path.realpath(os.path.join(media_root, "Media") if os.path.isdir(os.path.join(media_root, "Media")) else media_root))
                dst = os.path.join(self.media_dir, rel); os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
                ios_msg = msg_map[aid]; size = os.path.getsize(src)
                c.execute("INSERT INTO ZWAMEDIAITEM (Z_PK,ZANDROIDID,ZMESSAGE,ZMEDIALOCALPATH,ZFILESIZE) VALUES (?,?,?,?,?)", (media_id, aid, ios_msg, os.path.join(IOS_MEDIA_DIR, rel).replace('\\','/'), size))
                media_by_msg.setdefault(ios_msg, []).append(media_id)
            for msg, media_ids in media_by_msg.items():
                c.execute("UPDATE ZWAMESSAGE SET ZMEDIAITEM=? WHERE Z_PK=?", (media_ids[0], msg))
            c.execute("""UPDATE ZWACHATSESSION AS cs SET
                ZMESSAGECOUNTER=(SELECT count(*) FROM ZWAMESSAGE AS m WHERE m.ZCHATSESSION=cs.Z_PK),
                ZLASTMESSAGEDATE=COALESCE((SELECT max(m.ZTIMESTAMP) FROM ZWAMESSAGE AS m WHERE m.ZCHATSESSION=cs.Z_PK),0)
            """)
            c.commit()

    def _validate_candidate(self, path, chats, messages, media):
        with sqlite3.connect(path) as c:
            c.execute("PRAGMA foreign_keys=ON")
            if c.execute("PRAGMA foreign_key_check").fetchone(): raise ValueError("Candidate foreign-key check failed")
            counts = [c.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("ZWACHATSESSION", "ZWAMESSAGE", "ZWAMEDIAITEM")]
            if counts != [chats, messages, media]: raise ValueError(f"Candidate count mismatch: {counts}")
            if c.execute("SELECT count(*) FROM ZWAMESSAGE WHERE ZCHATSESSION NOT IN (SELECT Z_PK FROM ZWACHATSESSION)").fetchone()[0]: raise ValueError("Orphan candidate message")
