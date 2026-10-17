"""Read-only WhatsApp shared-data backup from Android devices via ADB.

This module deliberately does not use root, private app data, keys, or ADB backup.
It only copies the user-readable WhatsApp Databases and Media directories.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import shlex
import sqlite3
import subprocess
import tempfile
import queue
import threading
import time
from collections import deque
import codecs
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional


_PACKAGES = {False: "com.whatsapp", True: "com.whatsapp.w4b"}
_ADB_ROOTS = {False: ("/sdcard/Android/media/com.whatsapp/WhatsApp", "/sdcard/WhatsApp"),
              True: ("/sdcard/Android/media/com.whatsapp.w4b/WhatsApp Business", "/sdcard/WhatsApp Business")}


class AndroidBackup:
    """Perform a bounded, read-only backup of shared WhatsApp files through ADB."""

    def __init__(self, adb_path: str = "adb", timeout: int = 60, idle_timeout: float = 120.0, hard_timeout: float = 7200.0, on_progress: Optional[Callable[[dict[str, Any]], None]] = None):
        self.adb_path = adb_path
        self.timeout = timeout
        self.idle_timeout = float(idle_timeout)
        self.hard_timeout = float(hard_timeout)
        self.on_progress = on_progress

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _missing_adb() -> str:
        return "لم يتم العثور على ADB. ثبّت Android Platform Tools وضع مجلد platform-tools بجانب التطبيق أو أضف adb إلى PATH."

    def _run(self, args: list[str], timeout: Optional[int] = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(args, capture_output=True, text=True,
                              timeout=self.timeout if timeout is None else timeout)

    def _adb(self, *args: str) -> subprocess.CompletedProcess[str]:
        return self._run([self.adb_path, *args])

    @staticmethod
    def _parse_devices(output: str) -> list[dict[str, str]]:
        devices: list[dict[str, str]] = []
        for line in output.splitlines():
            line = line.strip()
            if not line or line.startswith("List of devices attached"):
                continue
            fields = line.split()
            if len(fields) < 2:
                continue
            serial, state = fields[0], fields[1]
            if state not in {"device", "offline", "unauthorized", "bootloader", "no permissions"}:
                continue
            item: dict[str, str] = {"serial": serial, "state": state}
            for token in fields[2:]:
                if token.startswith("model:"):
                    item["model"] = token[6:]
                elif token.startswith("device:"):
                    item.setdefault("model", token[7:])
            devices.append(item)
        return devices

    def _shell(self, serial: str, command: str) -> subprocess.CompletedProcess[str]:
        # command is always a fixed command or a fixed path from _ADB_ROOTS; serial is argv data.
        return self._adb("-s", serial, "shell", command)

    def _metadata(self, item: dict[str, str]) -> None:
        serial = item["serial"]
        try:
            version = self._shell(serial, "getprop ro.build.version.release")
            if version.returncode == 0 and version.stdout.strip():
                item["android_version"] = version.stdout.strip().splitlines()[-1].strip()
            for business, key in ((False, "whatsapp_version"), (True, "business_version")):
                package = _PACKAGES[business]
                result = self._shell(serial, f"dumpsys package {package}")
                if result.returncode == 0:
                    match = re.search(r"versionName=([^\s]+)", result.stdout)
                    if match:
                        item[key] = match.group(1)
        except (OSError, subprocess.TimeoutExpired):
            # Device enumeration remains useful even when optional metadata times out.
            return

    @staticmethod
    def _progress(callback: Optional[Callable[[dict[str, Any]], None]], event: dict[str, Any]) -> None:
        if callback:
            try:
                callback(event)
            except Exception:
                pass

    def check_devices(self, include_metadata: bool = True, on_progress: Optional[Callable[[dict[str, Any]], None]] = None) -> dict[str, Any]:
        """Return ADB availability and all devices reported by ``adb devices -l``."""
        result: dict[str, Any] = {"devices": [], "adb_available": False, "checked_at": self._now()}
        try:
            proc = self._adb("devices", "-l")
        except FileNotFoundError:
            result["error"] = self._missing_adb()
            return result
        except subprocess.TimeoutExpired:
            result["error"] = "انتهت مهلة ADB أثناء تعداد الأجهزة. تحقق من اتصال الجهاز وADB."
            return result
        result["adb_available"] = proc.returncode == 0
        if proc.returncode != 0:
            result["error"] = "فشل تنفيذ adb devices -l؛ تحقق من تثبيت ADB وخدمة adb."
            if proc.stderr.strip():
                result["adb_error"] = proc.stderr.strip()
            return result
        devices = self._parse_devices(proc.stdout)
        for index, item in enumerate(devices):
            if on_progress and index % 10 == 0:
                self._progress(on_progress, {"stage": "inventory", "message": f"تم تعداد {index + 1} جهازًا", "index": index})
            if include_metadata and item["state"] == "device":
                self._metadata(item)
        result["devices"] = devices
        return result

    @staticmethod
    def _is_plain_sqlite(path: Path) -> bool:
        try:
            with path.open("rb") as stream:
                if stream.read(16) != b"SQLite format 3\x00":
                    return False
            with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as conn:
                return conn.execute("PRAGMA quick_check").fetchall() == [("ok",)]
        except (OSError, sqlite3.Error):
            return False

    def _inventory(self, root: Path, on_progress: Optional[Callable[[dict[str, Any]], None]] = None) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        hashed = 0
        last_report = time.monotonic()
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            digest = hashlib.sha256()
            size = 0
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    size += len(block)
                    digest.update(block)
                    hashed += len(block)
                    now = time.monotonic()
                    if on_progress and now - last_report >= 2.0:
                        self._progress(on_progress, {"stage": "inventory", "message": f"فحص SHA-256: {hashed} بايت", "bytes_hashed": hashed})
                        last_report = now
            entries.append({"path": path.relative_to(root).as_posix(), "bytes": size,
                            "sha256": digest.hexdigest()})
        if on_progress:
            self._progress(on_progress, {"stage": "inventory", "message": f"اكتمل فحص SHA-256: {hashed} بايت", "bytes_hashed": hashed})
        return entries

    def _pull(self, serial: str, remote: str, local: Path,
              on_progress: Optional[Callable[[dict[str, Any]], None]] = None) -> tuple[bool, str]:
        """Stream adb pull without PIPE deadlocks; heartbeat is never activity."""
        on_progress = on_progress or self.on_progress
        local.parent.mkdir(parents=True, exist_ok=True)
        argv = [self.adb_path, "-s", serial, "pull", remote, str(local)]
        try:
            proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=False, bufsize=0)
        except FileNotFoundError:
            return False, self._missing_adb()
        except subprocess.TimeoutExpired:
            return False, "انتهت مهلة ADB أثناء سحب البيانات."
        except OSError as exc:
            return False, f"تعذر تشغيل ADB: {exc}"

        events: queue.Queue[tuple[str, bytes]] = queue.Queue(maxsize=128)
        def reader(stream: Any, kind: str) -> None:
            try:
                while True:
                    chunk = stream.read(4096)
                    if not chunk:
                        break
                    # A noisy adb diagnostic must not block process supervision forever.
                    try:
                        events.put((kind, chunk), timeout=0.25)
                    except queue.Full:
                        pass
            finally:
                try:
                    events.put((kind, b""), timeout=1.0)
                except queue.Full:
                    pass
                close = getattr(stream, "close", None)
                if close:
                    try: close()
                    except OSError: pass
        threads = [threading.Thread(target=reader, args=(proc.stdout, "stdout"), daemon=True),
                   threading.Thread(target=reader, args=(proc.stderr, "stderr"), daemon=True)]
        for thread in threads: thread.start()
        output: deque[str] = deque()
        output_bytes = 0
        decoders = {"stdout": codecs.getincrementaldecoder("utf-8")(errors="replace"),
                    "stderr": codecs.getincrementaldecoder("utf-8")(errors="replace")}
        last_activity = time.monotonic()
        started = last_heartbeat = time.monotonic()
        last_size = -1
        last_scan = 0.0
        streams_done = 0
        try:
            while True:
                now = time.monotonic()
                if now - last_scan >= 2.0:
                    size = self._tree_size(local)
                    last_scan = now
                    if size != last_size:
                        last_size, last_activity = size, now
                        self._progress(on_progress, {"stage": "pull", "message": f"سحب {size} بايت", "bytes": size, "remote": remote})
                try:
                    kind, chunk = events.get(timeout=min(0.25, max(0.05, 2.0 - (now-last_heartbeat))))
                    if chunk:
                        last_activity = time.monotonic()
                        text = decoders[kind].decode(chunk, final=False)
                        if text:
                            encoded = text.encode("utf-8", errors="replace")
                            output_bytes += len(encoded)
                            output.append(text)
                            while output_bytes > 16 * 1024 and output:
                                output_bytes -= len(output.popleft().encode("utf-8", errors="replace"))
                            self._progress(on_progress, {"stage": "pull", "message": text.strip(), "output": text[:4096], "remote": remote})
                    else:
                        streams_done += 1
                        tail = decoders[kind].decode(b"", final=True)
                        if tail:
                            output.append(tail)
                except queue.Empty:
                    pass
                now = time.monotonic()
                if now - last_heartbeat >= 2.0:
                    self._progress(on_progress, {"stage": "heartbeat", "message": "ما زال سحب البيانات جاريًا", "remote": remote})
                    last_heartbeat = now
                if proc.poll() is not None and streams_done >= 2 and events.empty():
                    break
                if now - started >= self.hard_timeout:
                    raise TimeoutError("hard")
                if now - last_activity >= self.idle_timeout:
                    raise TimeoutError("idle")
        except TimeoutError as exc:
            try: proc.kill()
            except OSError: pass
            try: proc.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired): pass
            return False, ("انتهت مهلة التوقف أثناء سحب البيانات." if str(exc) == "idle" else "انتهت المهلة القصوى لـ ADB أثناء سحب البيانات.")
        finally:
            for thread in threads: thread.join(timeout=1)
            for stream in (getattr(proc, "stdout", None), getattr(proc, "stderr", None)):
                close = getattr(stream, "close", None)
                if close:
                    try: close()
                    except OSError: pass
        if proc.returncode != 0:
            return False, ("".join(output).strip() or f"فشل adb pull (رمز الخروج {proc.returncode})")
        return True, ""

    @staticmethod
    def _tree_size(path: Path) -> int:
        total = 0
        try:
            if path.is_file(): return path.stat().st_size
            if path.exists():
                for item in path.rglob("*"):
                    if item.is_file():
                        try: total += item.stat().st_size
                        except OSError: pass
        except OSError:
            pass
        return total

    def backup(self, serial: str, destination: str | os.PathLike[str], is_business: bool = False, on_progress: Optional[Callable[[dict[str, Any]], None]] = None) -> dict[str, Any]:
        """Copy shared WhatsApp data for one explicitly selected, authorized device.

        The returned manifest is written even for failed/incomplete pulls. No existing
        destination is overwritten; each attempt gets a new directory.
        """
        started = self._now()
        on_progress = on_progress or self.on_progress
        base: Optional[Path] = None
        result: dict[str, Any] = {"backup_dir": None, "manifest": None, "android_db": None,
                                  "android_media": None, "status": "failed", "conversion_ready": False,
                                  "started_at": started, "completed_at": None, "errors": []}
        if not isinstance(serial, str) or not serial.strip():
            result["errors"].append("يجب تحديد serial صريح للجهاز؛ لا يمكن النسخ دون تحديد جهاز.")
            return result
        try:
            destination_path = Path(destination).expanduser()
            destination_path.mkdir(parents=True, exist_ok=True)
            if not destination_path.is_dir():
                raise ValueError("وجهة النسخ ليست مجلدًا.")
        except (OSError, ValueError) as exc:
            result["errors"].append(f"وجهة النسخ غير صالحة: {exc}")
            return result

        self._progress(on_progress, {"stage": "inventory", "message": "فحص الأجهزة"})
        checked = self.check_devices(include_metadata=False, on_progress=on_progress)
        result["adb_available"] = checked.get("adb_available", False)
        if not checked.get("adb_available"):
            result["errors"].append(checked.get("error", self._missing_adb()))
            return result
        devices = {d["serial"]: d for d in checked.get("devices", [])}
        device = devices.get(serial)
        if device is None:
            result["errors"].append("الـ serial المحدد غير موجود في adb devices -l؛ اختر جهازًا متصلًا صراحةً.")
            return result
        if device.get("state") != "device":
            result["errors"].append(f"الجهاز {serial} غير جاهز: حالته {device.get('state')}. وافق على تصحيح USB وانتظر state=device.")
            return result

        try:
            base = Path(tempfile.mkdtemp(prefix="android-backup-", dir=str(destination_path)))
        except OSError as exc:
            result["errors"].append(f"تعذر إنشاء مجلد نسخة فريد: {exc}")
            return result
        result["backup_dir"] = str(base)
        errors: list[str] = []
        roots = _ADB_ROOTS[bool(is_business)]
        remote_root: Optional[str] = None
        for candidate in roots:
            try:
                probe = self._shell(serial, f"test -d {shlex.quote(candidate)}")
            except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
                errors.append(self._missing_adb() if isinstance(exc, FileNotFoundError) else "انتهت مهلة ADB أثناء فحص مجلد واتساب.")
                break
            if probe.returncode == 0:
                remote_root = candidate
                break
        if remote_root is None and not errors:
            errors.append("لم يُعثر على مجلد WhatsApp المشترك القابل للقراءة (modern أو legacy).")

        pulled: set[str] = set()
        if remote_root:
            for name in ("Databases", "Media"):
                self._progress(on_progress, {"stage": "pull", "message": f"بدء سحب {name}", "name": name})
                ok, error = self._pull(serial, f"{remote_root}/{name}", base / name, on_progress=on_progress)
                if ok:
                    pulled.add(name)
                else:
                    errors.append(f"فشل سحب {name}: {error}")

        self._progress(on_progress, {"stage": "inventory", "message": "فحص الملفات المحلية"})
        try:
            entries = self._inventory(base, on_progress=on_progress)
        except OSError as exc:
            entries = []
            errors.append(f"تعذر فحص الملفات المحلية (خطأ قرص/صلاحيات): {exc}")
        db_dir, media_dir = base / "Databases", base / "Media"
        db_files = [p for p in db_dir.rglob("*") if p.is_file()] if db_dir.exists() else []
        media_files = [p for p in media_dir.rglob("*") if p.is_file()] if media_dir.exists() else []
        plain = next((p for p in db_files if p.name == "msgstore.db" and self._is_plain_sqlite(p)), None)
        crypt = next((p for p in db_files if p.name.startswith("msgstore.db.") and p.suffix.startswith(".crypt")), None)
        result["android_db"] = str(plain or crypt) if (plain or crypt) else None
        result["android_media"] = str(media_dir) if media_files else None
        conversion_reasons: list[str] = []
        if not plain:
            conversion_reasons.append("لا توجد قاعدة msgstore.db plaintext SQLite قابلة للتحقق؛ ملفات crypt مشفرة.")
        if not media_files:
            conversion_reasons.append("مجلد Media فارغ أو غير متاح.")
        result["conversion_reasons"] = conversion_reasons
        if not entries:
            errors.append("البيانات المسحوبة فارغة؛ رُفضت النسخة.")
        if errors:
            result["status"] = "failed"
        else:
            result["status"] = "success"
        result["conversion_ready"] = result["status"] == "success" and bool(plain and media_files)
        manifest = {"schema": 1, "status": result["status"], "serial": serial,
                    "is_business": bool(is_business), "remote_root": remote_root,
                    "started_at": started, "completed_at": self._now(), "files": entries,
                    "errors": errors, "conversion_ready": result["conversion_ready"],
                    "conversion_reasons": conversion_reasons,
                    "note": "هذه نسخة read-only من shared storage وليست نسخة WhatsApp كاملة؛ لا مفاتيح مستخرجة."}
        manifest_path = base / "manifest.json"
        try:
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            result["manifest"] = str(manifest_path)
        except OSError as exc:
            result["errors"].append(f"تعذر كتابة manifest: {exc}")
            result["status"] = "failed"
            result["conversion_ready"] = False
        result["errors"].extend(errors)
        result["completed_at"] = manifest["completed_at"]
        return result
