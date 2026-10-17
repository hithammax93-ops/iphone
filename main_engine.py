# main_engine.py
"""Offline candidate generation. Device restore is intentionally not implemented."""
import argparse
import json
import logging
import os
import re
import sys
import tempfile
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from device_manager import DeviceManager
from android_to_ios_converter import AndroidToIOSConverter

logger = logging.getLogger("main_engine")
KEY_ENV = "WHATSAPP_MIGRATION_HEX_KEY"


class ProgressEmitter:
    PREFIX = "::PROGRESS::"

    @staticmethod
    def _emit(payload: Dict[str, Any]) -> None:
        payload["timestamp"] = datetime.now(timezone.utc).isoformat()
        print(ProgressEmitter.PREFIX + json.dumps(payload, ensure_ascii=False), flush=True)

    @staticmethod
    def log(level: str, message: str) -> None:
        ProgressEmitter._emit({"type": "log", "level": level, "message": message})

    @staticmethod
    def step(step_id: str, message: str, percent: int) -> None:
        ProgressEmitter._emit({"type": "step", "step": step_id, "message": message,
                               "percent": max(0, min(100, percent))})

    @staticmethod
    def success(message: str, extra: Optional[Dict[str, Any]] = None) -> None:
        ProgressEmitter._emit({"type": "success", "message": message, **(extra or {})})

    @staticmethod
    def error(message: str, detail: Optional[str] = None) -> None:
        payload = {"type": "error", "message": message}
        if detail:
            payload["detail"] = detail
        ProgressEmitter._emit(payload)


class JsonLogHandler(logging.Handler):
    def emit(self, record):
        try:
            ProgressEmitter.log(record.levelname, self.format(record))
        except Exception:
            self.handleError(record)


def setup_logging():
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = JsonLogHandler()
    handler.setFormatter(logging.Formatter('%(name)s: %(message)s'))
    root.addHandler(handler)


def _inside(path: str, directory: str) -> bool:
    path, directory = os.path.realpath(path), os.path.realpath(directory)
    try:
        return os.path.commonpath([path, directory]) == directory
    except ValueError:
        return False


class MigrationEngine:
    def __init__(self, hex_key: str):
        self.hex_key = hex_key
        # Offline database processing must not require ADB or a connected iPhone.
        self.dm = DeviceManager(check_tools=False)

    def run_android_to_ios(self, android_db_path: str, android_media_path: str,
                           ios_backup_dir: str, is_business: bool,
                           output_dir: Optional[str] = None) -> Dict[str, Any]:
        ProgressEmitter.step("validate", "فحص البيانات؛ لن تُعدّل نسخة آيفون الاحتياطية.", 5)
        if not os.path.isfile(android_db_path):
            raise FileNotFoundError("قاعدة بيانات أندرويد غير موجودة.")
        if not os.path.isdir(android_media_path):
            raise FileNotFoundError("مجلد وسائط أندرويد غير موجود.")
        if not ios_backup_dir or not os.path.isdir(ios_backup_dir):
            raise FileNotFoundError("مجلد النسخة الاحتياطية غير موجود.")
        if output_dir and (_inside(output_dir, ios_backup_dir)
                           or _inside(ios_backup_dir, output_dir)
                           or _inside(android_db_path, output_dir)
                           or _inside(output_dir, android_media_path)
                           or _inside(android_media_path, output_dir)):
            raise ValueError("اختر مجلد إخراج مستقلًا خارج المدخلات والنسخة الاحتياطية.")
        destination = output_dir or tempfile.mkdtemp(prefix="wa_candidate_")
        os.makedirs(destination, exist_ok=True)
        ProgressEmitter.step("convert", "إنشاء ناتج تجريبي لفحص ربط البيانات فقط...", 15)
        converter = AndroidToIOSConverter(self.dm, self.hex_key)
        candidate = converter.convert(android_db_path, android_media_path, ios_backup_dir,
                                      is_business=is_business, output_dir=destination)
        report = {
            "status": "experimental_candidate",
            "device_compatibility": "unverified",
            "backup_modified": False,
            "restored_to_device": False,
            "output_db": candidate,
            "output_dir": destination,
            "counts": getattr(converter, "stats", {}),
            "warning": "مخطط iOS تجريبي؛ الناتج ليس نسخة واتساب معتمدة للاستعادة.",
        }
        report_path = os.path.join(destination, "migration_report.json")
        with open(report_path, "w", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        report["report"] = report_path
        # Not a success/migrated event. Never inject the speculative schema into a backup.
        ProgressEmitter._emit({"type": "candidate", "message":
                              "أُنشئ ناتج تجريبي فقط. لم يتم النقل إلى آيفون أو تعديل نسخته الاحتياطية.",
                              **report})
        return report

    def run_ios_to_android(self, *args, **kwargs):
        raise RuntimeError("الاتجاه العكسي معطّل: مخطط البيانات والتشفير لم يُعتمدا باختبار أجهزة فعلية.")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Experimental WhatsApp candidate generator — no device restore")
    parser.add_argument("--direction", required=True,
                        choices=["android_to_ios", "ios_to_android", "check_devices", "backup_android"])
    parser.add_argument("--whatsapp-type", default="regular", choices=["regular", "business"])
    parser.add_argument("--hex-key", help="Deprecated: prefer WHATSAPP_MIGRATION_HEX_KEY environment variable")
    parser.add_argument("--android-db")
    parser.add_argument("--android-media")
    parser.add_argument("--ios-backup")
    parser.add_argument("--output-db")
    parser.add_argument("--reference-crypt")
    parser.add_argument("--output-dir")
    parser.add_argument("--push-to-device", action="store_true")
    parser.add_argument("--android-serial")
    parser.add_argument("--backup-dir")
    parser.add_argument("--is-rooted", action="store_true")
    return parser


def resolve_adb() -> str:
    """Use official platform-tools beside the installed engine, otherwise PATH."""
    base = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    name = "adb.exe" if os.name == "nt" else "adb"
    bundled = base / "platform-tools" / name
    return str(bundled) if bundled.is_file() else name


def main(argv: Optional[list] = None) -> int:
    setup_logging()
    args = build_arg_parser().parse_args(argv)
    key = args.hex_key or os.environ.get(KEY_ENV, "")
    try:
        if args.push_to_device:
            raise RuntimeError("الاستعادة والدفع إلى الأجهزة معطّلان حتى إثبات التوافق.")
        if args.direction == "check_devices":
            from android_backup import AndroidBackup
            result = AndroidBackup(resolve_adb()).check_devices()
            ProgressEmitter._emit({**result, "type": "devices", "message":
                                  result.get("error") or "اكتمل فحص اتصال أندرويد عبر ADB فقط؛ لم يُفحص آيفون أو تُنقل بيانات."})
            return 0 if result.get("adb_available") else 1
        if args.direction == "backup_android":
            from android_backup import AndroidBackup
            if not args.android_serial or not args.backup_dir:
                raise ValueError("حدد جهاز أندرويد ومجلد حفظ النسخة على اللابتوب.")
            ProgressEmitter.step("backup", "نسخ ملفات واتساب المشتركة من أندرويد إلى اللابتوب...", 10)
            def backup_progress(event):
                ProgressEmitter._emit({**event, "type": "backup_progress"})

            result = AndroidBackup(resolve_adb(), on_progress=backup_progress).backup(args.android_serial, args.backup_dir,
                                                        args.whatsapp_type == "business")
            if result.get("status") != "success":
                ProgressEmitter._emit({**result, "type": "backup", "message":
                                      "فشل النسخ أو بقي ناقصًا؛ لا تستخدم الناتج للتحويل. " + "; ".join(result.get("errors", []))})
                return 1
            ProgressEmitter._emit({**result, "type": "backup", "message":
                                  "اكتمل نسخ الملفات المتاحة محليًا فقط؛ ليست نسخة كاملة للتطبيق ولم يتم النقل إلى آيفون."})
            return 0
        if args.direction == "ios_to_android" or args.push_to_device:
            raise RuntimeError("الاستعادة والدفع إلى الأجهزة معطّلان حتى إثبات التوافق؛ لا يُعلن نقل تجريبي كنجاح.")
        if key and not re.fullmatch(r"[0-9a-fA-F]{64}", key):
            ProgressEmitter.error("المفتاح يجب أن يكون 64 حرف hex صالحًا.")
            return 2
        if args.android_db and args.android_db.lower().endswith(".crypt15") and not key:
            ProgressEmitter.error("المفتاح مطلوب لفك تشفير crypt15.")
            return 2
        required = ("android_db", "android_media", "ios_backup")
        missing = [name for name in required if not getattr(args, name)]
        if missing:
            ProgressEmitter.error("وسائط مطلوبة: " + ", ".join(missing))
            return 2
        MigrationEngine(key).run_android_to_ios(args.android_db, args.android_media,
                                               args.ios_backup, args.whatsapp_type == "business",
                                               args.output_dir)
        # 0 means candidate generation completed, never that data was transferred.
        return 0
    except Exception as exc:
        message = str(exc)
        if key:
            message = message.replace(key, "[REDACTED]")
        ProgressEmitter.error("فشل: " + message)
        return 1


if __name__ == "__main__":
    sys.exit(main())
