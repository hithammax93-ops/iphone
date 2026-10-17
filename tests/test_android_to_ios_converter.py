import os
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from android_to_ios_converter import AndroidToIOSConverter


class DummyDM:
    pass


class DecryptDM:
    def __init__(self):
        self.calls = []

    def decrypt_crypt15(self, source, destination, key):
        self.calls.append((source, destination, key))
        shutil.copy2(source, destination)


def make_db(path, modern=True, media=True, orphan=False):
    con = sqlite3.connect(path)
    if modern:
        con.executescript("CREATE TABLE jid(_id INTEGER PRIMARY KEY, jid TEXT); CREATE TABLE chat(_id INTEGER PRIMARY KEY, jid_row_id INTEGER);")
        con.executemany("INSERT INTO jid VALUES (?,?)", [(30, "a@s.whatsapp.net"), (70, "b@s.whatsapp.net")])
        con.executemany("INSERT INTO chat VALUES (?,?)", [(900, 30), (4, 70)])
    else:
        con.execute("CREATE TABLE chat(_id INTEGER PRIMARY KEY, jid TEXT)")
        con.executemany("INSERT INTO chat VALUES (?,?)", [(900, "a@s.whatsapp.net"), (4, "b@s.whatsapp.net")])
    con.execute("CREATE TABLE message(_id INTEGER PRIMARY KEY, chat_row_id INTEGER, from_me INTEGER, timestamp INTEGER, text_data TEXT, message_type INTEGER)")
    con.executemany("INSERT INTO message VALUES (?,?,?,?,?,?)", [(1001, 900, 1, 1700000000000, "one", 0), (8, 4, 0, 1700000001, "two", 0)])
    if media:
        con.execute("CREATE TABLE message_media(message_row_id INTEGER, media_path TEXT)")
        con.execute("INSERT INTO message_media VALUES (?,?)", (8 if not orphan else 999, "WhatsApp/Media/WhatsApp Images/nested/a.jpg"))
    con.commit(); con.close()


def test_modern_join_nested_media_noncontiguous_ids_and_repeat(tmp_path):
    db = tmp_path / "msg.db"; make_db(db)
    root = tmp_path / "WhatsApp" / "Media" / "WhatsApp Images" / "nested"; root.mkdir(parents=True)
    (root / "a.jpg").write_bytes(b"abc")
    backup = tmp_path / "backup"; backup.mkdir(); (backup / "Manifest.db").write_bytes(b"unchanged")
    out = tmp_path / "out"
    conv = AndroidToIOSConverter(DummyDM(), "")
    first = conv.convert(str(db), str(tmp_path / "WhatsApp"), str(backup), output_dir=str(out))
    second = conv.convert(str(db), str(tmp_path / "WhatsApp"), str(backup), output_dir=str(out))
    assert first == second and Path(first).is_file()
    assert (backup / "Manifest.db").read_bytes() == b"unchanged"
    with sqlite3.connect(first) as c:
        assert c.execute("SELECT ZCONTACTJID FROM ZWACHATSESSION ORDER BY Z_PK").fetchall() == [("b@s.whatsapp.net",), ("a@s.whatsapp.net",)]
        assert c.execute("SELECT ZANDROIDID,ZCHATSESSION FROM ZWAMESSAGE ORDER BY Z_PK").fetchall() == [(8, 1), (1001, 2)]
        assert c.execute("SELECT ZMESSAGECOUNTER FROM ZWACHATSESSION ORDER BY Z_PK").fetchall() == [(1,), (1,)]
        assert c.execute("SELECT ZMEDIALOCALPATH FROM ZWAMEDIAITEM").fetchone()[0].endswith("WhatsApp Images/nested/a.jpg")


def test_missing_schema_and_orphan_fail_loudly(tmp_path):
    db = tmp_path / "bad.db"; sqlite3.connect(db).close()
    with pytest.raises(ValueError, match="missing table chat"):
        AndroidToIOSConverter(DummyDM(), "").convert(str(db), str(tmp_path), str(tmp_path))
    db = tmp_path / "orphan.db"; make_db(db, orphan=True)
    with pytest.raises(ValueError, match="Orphan media"):
        AndroidToIOSConverter(DummyDM(), "").convert(str(db), str(tmp_path), str(tmp_path))


def test_missing_media_and_traversal_fail(tmp_path):
    db = tmp_path / "msg.db"; make_db(db)
    with pytest.raises(FileNotFoundError, match="missing"):
        AndroidToIOSConverter(DummyDM(), "").convert(str(db), str(tmp_path / "Media"), str(tmp_path))
    db = tmp_path / "escape.db"; make_db(db)
    with sqlite3.connect(db) as c:
        c.execute("UPDATE message_media SET media_path='../outside'"); c.commit()
    media = tmp_path / "Media"; media.mkdir(); (tmp_path / "outside").write_bytes(b"x")
    with pytest.raises(ValueError, match="escapes"):
        AndroidToIOSConverter(DummyDM(), "").convert(str(db), str(media), str(tmp_path))


def test_crypt15_is_decrypted_to_work_db_and_publishes_media_bundle(tmp_path):
    plain = tmp_path / "msg.db"
    make_db(plain)
    encrypted = tmp_path / "msg.db.crypt15"
    shutil.copy2(plain, encrypted)
    root = tmp_path / "WhatsApp" / "Media" / "WhatsApp Images" / "nested"; root.mkdir(parents=True)
    (root / "a.jpg").write_bytes(b"abc")
    out = tmp_path / "out"
    dm = DecryptDM()
    result = AndroidToIOSConverter(dm, "secret").convert(str(encrypted), str(tmp_path / "WhatsApp"), str(tmp_path / "backup"), output_dir=str(out))
    assert dm.calls and dm.calls[0][0] == str(encrypted)
    assert Path(dm.calls[0][1]).parent != encrypted.parent
    assert Path(result).is_file() and (out / "Media" / "WhatsApp Images" / "nested" / "a.jpg").is_file()


def test_unmatched_jid_and_missing_media_link_fail(tmp_path):
    db = tmp_path / "unmatched.db"; make_db(db)
    with sqlite3.connect(db) as c:
        c.execute("UPDATE chat SET jid_row_id=999 WHERE _id=900"); c.commit()
    with pytest.raises(ValueError, match="unmatched"):
        AndroidToIOSConverter(DummyDM(), "").convert(str(db), str(tmp_path), str(tmp_path / "backup"))

    db = tmp_path / "no-link.db"; make_db(db)
    with sqlite3.connect(db) as c:
        c.execute("UPDATE message SET message_type=1 WHERE _id=8")
        c.execute("DELETE FROM message_media"); c.commit()
    with pytest.raises(ValueError, match="no media link"):
        AndroidToIOSConverter(DummyDM(), "").convert(str(db), str(tmp_path), str(tmp_path / "backup"))


def test_direct_converter_rejects_output_inside_input_paths(tmp_path):
    db = tmp_path / "msg.db"; make_db(db)
    media = tmp_path / "WhatsApp"; media.mkdir()
    backup = tmp_path / "backup"; backup.mkdir()
    with pytest.raises(ValueError, match="output_dir"):
        AndroidToIOSConverter(DummyDM(), "").convert(str(db), str(media), str(backup), output_dir=str(media / "candidate"))
    with pytest.raises(ValueError, match="output_dir"):
        AndroidToIOSConverter(DummyDM(), "").convert(str(db), str(media), str(backup), output_dir=str(backup / "candidate"))
