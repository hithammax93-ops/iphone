import os
import sys
import textwrap
from pathlib import Path

from android_backup import AndroidBackup


FAKE_ADB = r'''#!/usr/bin/env python3
import os, sys, time
remote, local = sys.argv[-2:]
mode = os.environ["FAKE_ADB_MODE"]
local = os.path.abspath(local)
os.makedirs(local, exist_ok=True)
if mode == "success":
    sys.stdout.buffer.write(b"progress\r")
    sys.stdout.buffer.flush()
    time.sleep(.1)
    word = " مرحبا".encode("utf-8")
    sys.stdout.buffer.write(word[:2]); sys.stdout.buffer.flush(); time.sleep(.05)
    sys.stdout.buffer.write(word[2:] + b"\n"); sys.stdout.buffer.flush()
    open(os.path.join(local, "ok.bin"), "wb").write(b"payload")
elif mode == "growing":
    with open(os.path.join(local, "growing.bin"), "wb") as f:
        for _ in range(8):
            f.write(b"x" * 1024); f.flush(); time.sleep(.4)
elif mode == "stall":
    time.sleep(10)
elif mode == "nonzero":
    open(os.path.join(local, "partial.bin"), "wb").write(b"partial")
    sys.stderr.buffer.write("انقطع الجهاز".encode("utf-8")); sys.stderr.buffer.flush()
    sys.exit(7)
'''


def fake_adb(tmp_path):
    path = tmp_path / "fake_adb.py"
    path.write_text(textwrap.dedent(FAKE_ADB), encoding="utf-8")
    path.chmod(0o755)
    return path


def run_pull(adb, local, mode, **kwargs):
    old = os.environ.get("FAKE_ADB_MODE")
    os.environ["FAKE_ADB_MODE"] = mode
    try:
        return AndroidBackup(adb_path=str(adb), **kwargs)._pull("SERIAL", "/remote/Media", local)
    finally:
        if old is None:
            os.environ.pop("FAKE_ADB_MODE", None)
        else:
            os.environ["FAKE_ADB_MODE"] = old


def test_real_subprocess_success_preserves_cr_and_split_utf8(tmp_path):
    events = []
    adb = fake_adb(tmp_path)
    old = os.environ.get("FAKE_ADB_MODE")
    os.environ["FAKE_ADB_MODE"] = "success"
    try:
        ok, error = AndroidBackup(adb_path=str(adb), idle_timeout=2.6)._pull(
            "SERIAL", "/remote/Media", tmp_path / "Media", events.append
        )
    finally:
        if old is None: os.environ.pop("FAKE_ADB_MODE", None)
        else: os.environ["FAKE_ADB_MODE"] = old
    assert ok and not error
    output = "".join(e.get("output", "") for e in events)
    assert "\r" in output
    assert "مرحبا" in output
    assert "�" not in output


def test_real_subprocess_silent_growing_file_is_not_idle(tmp_path):
    adb = fake_adb(tmp_path)
    ok, error = run_pull(adb, tmp_path / "Media", "growing", idle_timeout=2.6, hard_timeout=8)
    assert ok and not error
    assert (tmp_path / "Media" / "growing.bin").stat().st_size == 8192


def test_real_subprocess_stall_is_killed(tmp_path):
    adb = fake_adb(tmp_path)
    ok, error = run_pull(adb, tmp_path / "Media", "stall", idle_timeout=2.6, hard_timeout=8)
    assert not ok
    assert "التوقف" in error


def test_real_subprocess_nonzero_keeps_partial_and_diagnostic(tmp_path):
    adb = fake_adb(tmp_path)
    ok, error = run_pull(adb, tmp_path / "Media", "nonzero", idle_timeout=2.6)
    assert not ok
    assert "انقطع الجهاز" in error
    assert (tmp_path / "Media" / "partial.bin").exists()
