# ios_manifest_injector.py
"""Safe, deliberately conservative updates to an iOS backup Manifest.db."""
import hashlib
import logging
import ntpath
import os
import plistlib
import secrets
import shutil
import sqlite3
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)
NS_KEYED_ARCHIVER = "NSKeyedArchiver"
_CHUNK = 1024 * 1024


class NSKeyedArchiverHelper:
    @staticmethod
    def is_nskeyed_archiver(data: bytes) -> bool:
        try:
            value = plistlib.loads(data)
            return isinstance(value, dict) and value.get("$archiver") == NS_KEYED_ARCHIVER
        except Exception:
            return False

    @staticmethod
    def parse(data: bytes) -> Dict[str, Any]:
        try:
            archive = plistlib.loads(data)
        except Exception as exc:
            raise ValueError("Invalid NSKeyedArchiver plist") from exc
        if not isinstance(archive, dict) or archive.get("$archiver") != NS_KEYED_ARCHIVER:
            raise ValueError("Invalid NSKeyedArchiver archive")
        objects = archive.get("$objects")
        top = archive.get("$top")
        if not isinstance(objects, list) or not isinstance(top, dict) or "root" not in top:
            raise ValueError("Invalid NSKeyedArchiver structure")
        NSKeyedArchiverHelper._uid_index(top["root"], len(objects))
        return archive

    @staticmethod
    def _uid_index(value: Any, count: int) -> int:
        # Integer values are ordinary archived scalars, never references.
        if not isinstance(value, plistlib.UID):
            raise ValueError("Invalid NSKeyedArchiver UID")
        try:
            index = int(value.data)
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError("Invalid NSKeyedArchiver UID") from exc
        if index < 0 or index >= count:
            raise ValueError("NSKeyedArchiver UID out of range")
        return index

    @staticmethod
    def get_root_object(archive: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        objects = archive.get("$objects")
        top = archive.get("$top")
        if not isinstance(objects, list) or not isinstance(top, dict) or "root" not in top:
            raise ValueError("Invalid NSKeyedArchiver structure")
        obj = objects[NSKeyedArchiverHelper._uid_index(top["root"], len(objects))]
        if not isinstance(obj, dict):
            raise ValueError("NSKeyedArchiver root is not an object")
        return obj

    @staticmethod
    def update_property(archive: Dict[str, Any], key: str, value: Any) -> bool:
        root = NSKeyedArchiverHelper.get_root_object(archive)
        objects = archive["$objects"]
        if key in root and isinstance(root[key], plistlib.UID):
            # MBFile properties are commonly UID references.  Replace the referenced
            # object, rather than the UID itself, so all archive indirection survives.
            index = NSKeyedArchiverHelper._uid_index(root[key], len(objects))
            objects[index] = value
        else:
            root[key] = value
        return True

    @staticmethod
    def serialize(archive: Dict[str, Any]) -> bytes:
        # Validate before serialization and never silently turn a malformed archive
        # into a different plist format.
        NSKeyedArchiverHelper.get_root_object(archive)
        try:
            return plistlib.dumps(archive, fmt=plistlib.FMT_BINARY, sort_keys=False)
        except Exception as exc:
            raise ValueError("Unable to serialize NSKeyedArchiver") from exc


class IOSManifestInjector:
    def __init__(self, backup_dir: str):
        self.backup_dir = os.path.abspath(backup_dir)
        self.manifest_path = os.path.join(self.backup_dir, "Manifest.db")
        if not os.path.exists(self.manifest_path):
            raise FileNotFoundError(f"Manifest.db غير موجود في {backup_dir}")
        encrypted = os.path.join(self.backup_dir, "Manifest.plist")
        if os.path.exists(encrypted):
            try:
                with open(encrypted, "rb") as fh:
                    info = plistlib.load(fh)
            except Exception as exc:
                raise ValueError("Cannot inspect Manifest.plist encryption state") from exc
            if isinstance(info, dict) and info.get("IsEncrypted") is True:
                raise ValueError("Encrypted iOS backups are not supported for plaintext injection")

    @staticmethod
    def _validate_relative_path(path: str) -> str:
        if not isinstance(path, str) or not path or "\x00" in path:
            raise ValueError("relativePath must be a non-empty relative path")
        normalized = path.replace("\\", "/")
        # Use ntpath as well as os.path: Manifest paths must not accept a
        # Windows drive-qualified path even when validated on Linux.
        if normalized.startswith("/") or os.path.splitdrive(normalized)[0] or ntpath.splitdrive(normalized)[0]:
            raise ValueError("relativePath must not be absolute")
        parts = normalized.split("/")
        if any(part in ("", ".", "..") for part in parts):
            raise ValueError("relativePath contains an unsafe component")
        return "/".join(parts)

    @staticmethod
    def compute_file_id(domain: str, relative_path: str) -> str:
        if not isinstance(domain, str) or not domain or "\x00" in domain:
            raise ValueError("domain must be a non-empty string")
        relative_path = IOSManifestInjector._validate_relative_path(relative_path)
        return hashlib.sha1(f"{domain}-{relative_path}".encode()).hexdigest()

    @staticmethod
    def _validate_file_id(file_id: str) -> str:
        if not isinstance(file_id, str) or len(file_id) != 40 or any(c not in "0123456789abcdefABCDEF" for c in file_id):
            raise ValueError("invalid fileID")
        return file_id.lower()

    def get_backup_file_path(self, file_id: str) -> str:
        file_id = self._validate_file_id(file_id)
        return os.path.join(self.backup_dir, file_id[:2], file_id)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.manifest_path)

    def get_entry(self, file_id: str) -> Optional[Dict[str, Any]]:
        file_id = self._validate_file_id(file_id)
        with self._connect() as conn:
            row = conn.execute("SELECT fileID, domain, relativePath, flags, file FROM Files WHERE fileID=?", (file_id,)).fetchone()
        if not row:
            return None
        return dict(zip(("fileID", "domain", "relativePath", "flags", "file"), row))

    def _build_plist_payload(self, size: int, digest: bytes, protection_class: int = 3, existing_data: Optional[bytes] = None) -> bytes:
        if size < 0 or not isinstance(digest, (bytes, bytearray)):
            raise ValueError("invalid file metadata")
        if existing_data is not None and NSKeyedArchiverHelper.is_nskeyed_archiver(existing_data):
            archive = NSKeyedArchiverHelper.parse(existing_data)
            NSKeyedArchiverHelper.update_property(archive, "Size", size)
            NSKeyedArchiverHelper.update_property(archive, "Digest", bytes(digest))
            NSKeyedArchiverHelper.update_property(archive, "ProtectionClass", protection_class)
            return NSKeyedArchiverHelper.serialize(archive)
        if existing_data is not None:
            try:
                old = plistlib.loads(existing_data)
            except Exception as exc:
                raise ValueError("Invalid existing Manifest metadata") from exc
            if not isinstance(old, dict):
                raise ValueError("Invalid existing Manifest metadata")
            # Preserve every unknown key; only replace facts we actually measured.
            old.update(Size=size, Digest=bytes(digest), ProtectionClass=protection_class)
            return plistlib.dumps(old, fmt=plistlib.FMT_BINARY, sort_keys=False)
        raise ValueError("Cannot create new Manifest metadata without a compatible template")

    def update_entry(self, file_id: str, new_size: int, new_digest: bytes, protection_class: int = 3) -> bool:
        file_id = self._validate_file_id(file_id)
        with self._connect() as conn:
            row = conn.execute("SELECT file FROM Files WHERE fileID=?", (file_id,)).fetchone()
            if not row:
                return False
            if not isinstance(row[0], bytes):
                raise ValueError("Invalid existing Manifest metadata")
            payload = self._build_plist_payload(new_size, new_digest, protection_class, row[0])
            conn.execute("UPDATE Files SET file=? WHERE fileID=?", (payload, file_id))
        return True

    def add_entry(self, domain: str, relative_path: str, size: int, digest: bytes, flags: int = 1, protection_class: int = 3) -> str:
        file_id = self.compute_file_id(domain, relative_path)
        existing = self.get_entry(file_id)
        if existing:
            self.update_entry(file_id, size, digest, protection_class)
            return file_id
        raise ValueError("Cannot create new Manifest entry without a compatible metadata template")

    def add_directory_entry(self, domain: str, relative_path: str, protection_class: int = 3) -> str:
        return self.add_entry(domain, relative_path, 0, b"", flags=2, protection_class=protection_class)

    def delete_entry(self, file_id: str) -> bool:
        file_id = self._validate_file_id(file_id)
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM Files WHERE fileID=?", (file_id,))
            return cur.rowcount > 0

    def inject_file(self, domain: str, relative_path: str, source_file_path: str, protection_class: int = 3) -> str:
        relative_path = self._validate_relative_path(relative_path)
        if not os.path.isfile(source_file_path):
            raise FileNotFoundError(f"الملف المصدر غير موجود: {source_file_path}")
        file_id = self.compute_file_id(domain, relative_path)
        dest = self.get_backup_file_path(file_id)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        temp = os.path.join(os.path.dirname(dest), f".{file_id}.{secrets.token_hex(8)}.tmp")
        old_temp = os.path.join(os.path.dirname(dest), f".{file_id}.{secrets.token_hex(8)}.old")
        moved_old = False
        try:
            size = 0
            digest = hashlib.sha1()
            with open(source_file_path, "rb") as src, open(temp, "wb") as out:
                while True:
                    chunk = src.read(_CHUNK)
                    if not chunk:
                        break
                    out.write(chunk); digest.update(chunk); size += len(chunk)
                out.flush(); os.fsync(out.fileno())
            # Serialize metadata before touching an existing physical backup file.
            entry = self.get_entry(file_id)
            if not entry:
                raise ValueError("Cannot create new Manifest entry without a compatible metadata template")
            if not isinstance(entry.get("file"), bytes):
                raise ValueError("Invalid existing Manifest metadata")
            payload = self._build_plist_payload(size, digest.digest(), protection_class, entry["file"])
            # Stage the physical replacement and retain the old file until the
            # metadata transaction succeeds.  This gives rollback semantics for
            # both copy/rename and SQLite failures.
            if os.path.exists(dest):
                os.replace(dest, old_temp)
                moved_old = True
            os.replace(temp, dest)
            try:
                with self._connect() as conn:
                    if entry:
                        conn.execute("UPDATE Files SET file=? WHERE fileID=?", (payload, file_id))
                    else:
                        conn.execute("INSERT INTO Files(fileID,domain,relativePath,flags,file) VALUES(?,?,?,?,?)", (file_id, domain, relative_path, 1, payload))
                    conn.commit()
            except Exception:
                try:
                    os.unlink(dest)
                except FileNotFoundError:
                    pass
                if moved_old:
                    os.replace(old_temp, dest)
                    moved_old = False
                raise
            if moved_old:
                os.unlink(old_temp)
            return file_id
        except Exception:
            try:
                os.unlink(temp)
            except FileNotFoundError:
                pass
            # If staging failed before the inner rollback ran, restore the old
            # physical backup and never leave a partial artifact behind.
            if moved_old and os.path.exists(old_temp) and not os.path.exists(dest):
                os.replace(old_temp, dest)
            raise

    def inject_directory_tree(self, domain: str, base_relative_path: str, local_dir: str) -> List[str]:
        base_relative_path = self._validate_relative_path(base_relative_path)
        injected = []
        for root, dirs, files in os.walk(local_dir):
            rel = os.path.relpath(root, local_dir).replace("\\", "/")
            rel = "" if rel == "." else rel
            for d in dirs:
                self.add_directory_entry(domain, "/".join(x for x in (base_relative_path, rel, d) if x))
            for name in files:
                injected.append(self.inject_file(domain, "/".join(x for x in (base_relative_path, rel, name) if x), os.path.join(root, name)))
        return injected

    def remove_wal_shm(self, domain: str, relative_path: str) -> None:
        relative_path = self._validate_relative_path(relative_path)
        for suffix in ("-wal", "-shm"):
            fid = self.compute_file_id(domain, relative_path + suffix)
            if self.get_entry(fid):
                self.delete_entry(fid)
            path = self.get_backup_file_path(fid)
            try:
                os.remove(path)
            except FileNotFoundError:
                pass


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("IOSManifestInjector جاهز.")
