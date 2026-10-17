import json
import subprocess
import sqlite3
from pathlib import Path
from unittest.mock import patch

from android_backup import AndroidBackup


def completed(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def device_listing(extra=""):
    return "List of devices attached\n" + extra + "\n"


def test_check_devices_reports_multiple_states_and_metadata():
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if argv[1:3] == ["devices", "-l"]:
            return completed(device_listing("A device product:x model:Pixel_8 transport_id:1\nB unauthorized usb:1\nC offline"))
        if argv[-2:] == ["shell", "getprop ro.build.version.release"]:
            return completed("14\n")
        if argv[-2:] == ["shell", "dumpsys package com.whatsapp"]:
            return completed("versionName=2.24.1.1\n")
        if argv[-2:] == ["shell", "dumpsys package com.whatsapp.w4b"]:
            return completed("versionName=2.24.2.2\n")
        raise AssertionError(argv)

    with patch("android_backup.subprocess.run", side_effect=run):
        report = AndroidBackup("adb-test").check_devices()
    assert report["adb_available"] is True
    assert report["devices"][0]["serial"] == "A"
    assert report["devices"][0]["model"] == "Pixel_8"
    assert report["devices"][0]["android_version"] == "14"
    assert report["devices"][0]["whatsapp_version"] == "2.24.1.1"
    assert report["devices"][1]["state"] == "unauthorized"
    assert report["devices"][2]["state"] == "offline"
    assert all("-s" in call or call[1:3] == ["devices", "-l"] for call in calls)


def test_missing_adb_is_actionable():
    with patch("android_backup.subprocess.run", side_effect=FileNotFoundError):
        report = AndroidBackup("not-adb").check_devices()
    assert report["adb_available"] is False
    assert "ADB" in report["error"]
    assert "ثبّت" in report["error"]


def test_backup_rejects_unauthorized_and_requires_explicit_serial(tmp_path):
    listing = device_listing("A unauthorized\nB offline\n")
    with patch("android_backup.subprocess.run", return_value=completed(listing)):
        missing = AndroidBackup().backup("", tmp_path)
        unauthorized = AndroidBackup().backup("A", tmp_path)
    assert missing["status"] == "failed"
    assert "serial" in missing["errors"][0]
    assert unauthorized["status"] == "failed"
    assert "غير جاهز" in unauthorized["errors"][0]


def test_backup_pulls_only_selected_serial_and_creates_manifest(tmp_path):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if argv[1:3] == ["devices", "-l"]:
            return completed(device_listing("SAFE device model:Pixel\nEVIL device model:Other\n"))
        if argv[-2:] == ["shell", "getprop ro.build.version.release"]:
            return completed("14")
        if argv[-2:] == ["shell", "dumpsys package com.whatsapp"] or argv[-2:] == ["shell", "dumpsys package com.whatsapp.w4b"]:
            return completed("versionName=1.0")
        if argv[-2:] == ["shell", "test -d /sdcard/Android/media/com.whatsapp/WhatsApp"]:
            return completed(returncode=0)
        if argv[-2:] == ["shell", "test -d /sdcard/WhatsApp"]:
            return completed(returncode=1)
        if argv[1] == "-s" and argv[3] == "pull":
            local = Path(argv[-1])
            remote = argv[4]
            assert not local.exists(), "ADB would nest source inside existing destination"
            local.mkdir(parents=True, exist_ok=True)
            if remote.endswith("/Databases"):
                with sqlite3.connect(local / "msgstore.db") as conn:
                    conn.execute("CREATE TABLE chat (_id INTEGER PRIMARY KEY)")
            else:
                (local / "image.jpg").write_bytes(b"media")
            return completed("pull complete")
        raise AssertionError(argv)

    with patch("android_backup.subprocess.run", side_effect=run):
        result = AndroidBackup().backup("SAFE", tmp_path)
    assert result["status"] == "success"
    assert result["conversion_ready"] is True
    assert result["android_db"].endswith("Databases/msgstore.db")
    manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
    assert manifest["status"] == "success"
    assert any(entry["sha256"] for entry in manifest["files"])
    pull_calls = [c for c in calls if len(c) > 3 and c[3] == "pull"]
    assert pull_calls and all(c[2] == "SAFE" for c in pull_calls)
    assert "EVIL" not in " ".join(" ".join(c) for c in pull_calls)


def test_empty_or_incomplete_pull_is_failed_and_manifest_marks_failure(tmp_path):
    def run(argv, **kwargs):
        if argv[1:3] == ["devices", "-l"]:
            return completed(device_listing("A device\n"))
        if argv[-2:] == ["shell", "getprop ro.build.version.release"]:
            return completed("13")
        if argv[-2:] == ["shell", "dumpsys package com.whatsapp"] or argv[-2:] == ["shell", "dumpsys package com.whatsapp.w4b"]:
            return completed("")
        if argv[-2:] == ["shell", "test -d /sdcard/Android/media/com.whatsapp/WhatsApp"]:
            return completed(returncode=0)
        if argv[-2:] == ["shell", "test -d /sdcard/WhatsApp"]:
            return completed(returncode=1)
        if argv[1] == "-s" and argv[3] == "pull":
            if argv[4].endswith("/Databases"):
                Path(argv[-1]).mkdir(parents=True, exist_ok=True)
                return completed(returncode=1, stderr="device disconnected")
            return completed(returncode=0)
        raise AssertionError(argv)

    with patch("android_backup.subprocess.run", side_effect=run):
        result = AndroidBackup().backup("A", tmp_path)
    assert result["status"] == "failed"
    assert result["conversion_ready"] is False
    manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
    assert manifest["status"] == "failed"
    assert manifest["errors"]


def test_encrypted_database_never_claims_conversion_ready(tmp_path):
    def run(argv, **kwargs):
        if argv[1:3] == ["devices", "-l"]:
            return completed(device_listing("A device\n"))
        if argv[-2:] == ["shell", "getprop ro.build.version.release"]:
            return completed("13")
        if argv[-2:] == ["shell", "dumpsys package com.whatsapp"] or argv[-2:] == ["shell", "dumpsys package com.whatsapp.w4b"]:
            return completed("")
        if argv[-2:] == ["shell", "test -d /sdcard/Android/media/com.whatsapp/WhatsApp"]:
            return completed(returncode=0)
        if argv[-2:] == ["shell", "test -d /sdcard/WhatsApp"]:
            return completed(returncode=1)
        if argv[1] == "-s" and argv[3] == "pull":
            local = Path(argv[-1]); local.mkdir(parents=True, exist_ok=True)
            if argv[4].endswith("/Databases"):
                (local / "msgstore.db.crypt15").write_bytes(b"encrypted")
            else:
                (local / "x.jpg").write_bytes(b"media")
            return completed()
        raise AssertionError(argv)

    with patch("android_backup.subprocess.run", side_effect=run):
        result = AndroidBackup().backup("A", tmp_path)
    assert result["status"] == "success"
    assert result["conversion_ready"] is False
    assert "plaintext" in " ".join(result["conversion_reasons"])


def test_sqlite_header_alone_is_not_conversion_ready(tmp_path):
    fake = tmp_path / 'msgstore.db'
    fake.write_bytes(b'SQLite format 3\x00' + b'not a database')
    assert not AndroidBackup._is_plain_sqlite(fake)


def test_business_path_is_shell_quoted(tmp_path):
    commands = []
    def run(argv, **kwargs):
        if argv[1:3] == ['devices', '-l']:
            return completed(device_listing('A device'))
        if argv[3] == 'shell':
            commands.append(argv[4])
            if argv[4].startswith('test -d'):
                return completed(returncode=1)
            return completed()
        raise AssertionError(argv)
    with patch('android_backup.subprocess.run', side_effect=run):
        result = AndroidBackup().backup('A', tmp_path, True)
    assert result['status'] == 'failed'
    assert "test -d '/sdcard/Android/media/com.whatsapp.w4b/WhatsApp Business'" in commands


def test_pull_timeout_is_reported_as_failure(tmp_path):
    with patch('android_backup.subprocess.run', side_effect=subprocess.TimeoutExpired(['adb'], 7200)):
        ok, error = AndroidBackup()._pull('A', '/fixed/Media', tmp_path / 'Media')
    assert not ok
    assert 'مهلة' in error


def test_missing_serial_never_executes_adb(tmp_path):
    with patch('android_backup.subprocess.run') as adb:
        result = AndroidBackup().backup('', tmp_path)
    assert result['status'] == 'failed'
    adb.assert_not_called()
