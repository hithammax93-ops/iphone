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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


_PACKAGES = {False: "com.whatsapp", True: "com.whatsapp.w4b"}
_ADB_ROOTS = {False: ("/sdcard/Android/media/com.whatsapp/WhatsApp", "/sdcard/WhatsApp"),
              True: ("/sdcard/Android/media/com.whatsapp.w4b/WhatsApp Business", "/sdcard/WhatsApp Business")}


class AndroidBackup:
    """Perform a bounded, read-only backup of shared WhatsApp files through ADB."""

    def __init__(self, adb_path: str = "adb", timeout: int = 60):
        self.adb_path = adb_path
        self.timeout = timeout

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

    def check_devices(self) -> dict[str, Any]:
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
        for item in devices:
            if item["state"] == "device":
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

    @staticmethod
    def _inventory(root: Path) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            digest = hashlib.sha256()
            size = 0
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    size += len(block)
                    digest.update(block)
            entries.append({"path": path.relative_to(root).as_posix(), "bytes": size,
                            "sha256": digest.hexdigest()})
        return entries

    def _pull(self, serial: str, remote: str, local: Path) -> tuple[bool, str]:
        # ADB nests a source directory inside an existing destination directory.
        # Leave destination absent so its contents land at exactly local/.
        local.parent.mkdir(parents=True, exist_ok=True)
        try:
            proc = self._run([self.adb_path, "-s", serial, "pull", remote, str(local)], timeout=7200)
        except FileNotFoundError:
            return False, self._missing_adb()
        except subprocess.TimeoutExpired:
            return False, "انتهت مهلة ADB أثناء سحب البيانات."
        if proc.returncode != 0:
            return False, (proc.stderr or proc.stdout or "فشل adb pull").strip()
        return True, ""

    def backup(self, serial: str, destination: str | os.PathLike[str], is_business: bool = False) -> dict[str, Any]:
        """Copy shared WhatsApp data for one explicitly selected, authorized device.

        The returned manifest is written even for failed/incomplete pulls. No existing
        destination is overwritten; each attempt gets a new directory.
        """
        started = self._now()
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

        checked = self.check_devices()
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
                ok, error = self._pull(serial, f"{remote_root}/{name}", base / name)
                if ok:
                    pulled.add(name)
                else:
                    errors.append(f"فشل سحب {name}: {error}")

        entries = self._inventory(base)
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
