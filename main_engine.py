# main_engine.py
import argparse
import json
import logging
import os
import sys
import tempfile
import shutil
import traceback
from datetime import datetime
from typing import Dict, Any, Optional

# استيراد الوحدات
from device_manager import (
    DeviceManager,
    IOS_APP_DOMAIN, IOS_BUSINESS_APP_DOMAIN,
    IOS_CHATSTORAGE_PATH, IOS_MEDIA_DIR,
    WHATSAPP_PACKAGE, WHATSAPP_BUSINESS_PACKAGE,
    ANDROID_WHATSAPP_MEDIA, ANDROID_WHATSAPP_BUSINESS_MEDIA,
)
from android_to_ios_converter import AndroidToIOSConverter
from ios_to_android_converter import IOSToAndroidConverter
from ios_manifest_injector import IOSManifestInjector

logger = logging.getLogger("main_engine")


# ---------- نظام إخراج JSON للتواصل مع واجهة C# ----------

class ProgressEmitter:
    """
    يصدر رسائل JSON سطرية إلى stdout حتى تتمكن واجهة C# من قراءتها بسهولة.
    جميع الرسائل تبدأ بـ "::PROGRESS::" لتسهيل الفلترة.
    """

    PREFIX = "::PROGRESS::"

    @staticmethod
    def _emit(payload: Dict[str, Any]) -> None:
        payload["timestamp"] = datetime.utcnow().isoformat() + "Z"
        line = ProgressEmitter.PREFIX + json.dumps(payload, ensure_ascii=False)
        print(line, flush=True)

    @staticmethod
    def log(level: str, message: str) -> None:
        ProgressEmitter._emit({"type": "log", "level": level, "message": message})

    @staticmethod
    def step(step_id: str, message: str, percent: int) -> None:
        ProgressEmitter._emit({
            "type": "step", "step": step_id,
            "message": message, "percent": max(0, min(100, percent))
        })

    @staticmethod
    def success(message: str, extra: Optional[Dict[str, Any]] = None) -> None:
        payload = {"type": "success", "message": message}
        if extra:
            payload.update(extra)
        ProgressEmitter._emit(payload)

    @staticmethod
    def error(message: str, detail: Optional[str] = None) -> None:
        payload = {"type": "error", "message": message}
        if detail:
            payload["detail"] = detail
        ProgressEmitter._emit(payload)


# ---------- معالج logging مخصص يحوّل السجلات إلى JSON ----------

class JsonLogHandler(logging.Handler):
    def emit(self, record):
        try:
            msg = self.format(record)
            ProgressEmitter.log(record.levelname, msg)
        except Exception:
            pass


def setup_logging():
    """إعداد logging ليصدر بصيغة JSON إلى stdout، مع منع التكرار."""
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = JsonLogHandler()
    handler.setFormatter(logging.Formatter('%(name)s: %(message)s'))
    root.addHandler(handler)


# ---------- الفئة الرئيسية للمحرك ----------

class MigrationEngine:
    """
    المحرك الرئيسي لعملية النقل.
    """

    def __init__(self, hex_key: str):
        self.hex_key = hex_key
        self.dm = DeviceManager()
        self.work_dir: Optional[str] = None

    def _prepare_workdir(self) -> str:
        self.work_dir = tempfile.mkdtemp(prefix="wa_migrator_")
        logger.info(f"مجلد العمل المؤقت: {self.work_dir}")
        return self.work_dir

    def _cleanup(self) -> None:
        if self.work_dir and os.path.exists(self.work_dir):
            try:
                shutil.rmtree(self.work_dir, ignore_errors=True)
                logger.info("تم تنظيف مجلد العمل المؤقت.")
            except Exception as e:
                logger.warning(f"فشل تنظيف مجلد العمل: {e}")

    # ---------- الاتجاه 1: Android → iOS ----------

    def run_android_to_ios(self, android_db_path: str, android_media_path: str,
                           ios_backup_dir: str, is_business: bool,
                           output_dir: Optional[str] = None) -> None:
        ProgressEmitter.step("prepare", "تجهيز البيئة...", 2)
        self._prepare_workdir()

        try:
            ProgressEmitter.step("validate", "التحقق من المدخلات...", 5)
            if not os.path.exists(android_db_path):
                raise FileNotFoundError(f"قاعدة بيانات أندرويد غير موجودة: {android_db_path}")
            if not os.path.exists(ios_backup_dir):
                raise FileNotFoundError(f"مجلد النسخة الاحتياطية لـ iOS غير موجود: {ios_backup_dir}")

            ProgressEmitter.step("convert", "بدء التحويل من أندرويد إلى iOS...", 10)
            converter = AndroidToIOSConverter(self.dm, self.hex_key)
            ios_db_path = converter.convert(
                android_db_path=android_db_path,
                android_media_path=android_media_path,
                ios_backup_dir=ios_backup_dir,
                is_business=is_business,
                output_dir=output_dir,
            )
            ProgressEmitter.step("convert_done", "اكتمل إنشاء ChatStorage.sqlite.", 55)

            # حقن قاعدة البيانات الناتجة + إزالة WAL/SHM
            ProgressEmitter.step("inject_db", "حقن ChatStorage.sqlite في النسخة الاحتياطية...", 70)
            domain = IOS_BUSINESS_APP_DOMAIN if is_business else IOS_APP_DOMAIN
            injector = IOSManifestInjector(ios_backup_dir)
            injector.inject_file(domain, IOS_CHATSTORAGE_PATH, ios_db_path)
            injector.remove_wal_shm(domain, IOS_CHATSTORAGE_PATH)
            ProgressEmitter.step("inject_db_done", "اكتمل حقن قاعدة البيانات وإزالة WAL/SHM.", 85)

            # حقن الميديا
            ProgressEmitter.step("inject_media", "حقن ملفات الميديا في النسخة الاحتياطية...", 90)
            media_local_dir = os.path.join(self.work_dir, "Media")
            # AndroidToIOSConverter كان قد نسخها في temp_convert/Media
            temp_media = os.path.join(
                os.path.dirname(android_db_path), "temp_convert", "Media"
            )
            if os.path.exists(temp_media):
                shutil.copytree(temp_media, media_local_dir, dirs_exist_ok=True)
            if os.path.exists(media_local_dir):
                injector.inject_directory_tree(domain, IOS_MEDIA_DIR, media_local_dir)
            ProgressEmitter.step("inject_media_done", "اكتمل حقن الميديا.", 98)

            ProgressEmitter.success(
                "اكتمل التحويل من أندرويد إلى iOS بنجاح.",
                extra={"output_db": ios_db_path, "backup_dir": ios_backup_dir}
            )
        except Exception as e:
            ProgressEmitter.error(f"فشل التحويل: {e}", traceback.format_exc())
            raise
        finally:
            self._cleanup()

    # ---------- الاتجاه 2: iOS → Android ----------

    def run_ios_to_android(self, ios_backup_dir: str, output_db_path: str,
                           is_business: bool, reference_crypt_path: Optional[str] = None,
                           android_target_serial: Optional[str] = None,
                           push_to_device: bool = False,
                           is_rooted: bool = False) -> None:
        ProgressEmitter.step("prepare", "تجهيز البيئة...", 2)
        self._prepare_workdir()

        try:
            ProgressEmitter.step("validate", "التحقق من المدخلات...", 5)
            if not os.path.exists(ios_backup_dir):
                raise FileNotFoundError(f"مجلد النسخة الاحتياطية لـ iOS غير موجود: {ios_backup_dir}")

            ProgressEmitter.step("convert", "بدء التحويل من iOS إلى Android...", 10)
            converter = IOSToAndroidConverter(self.dm, self.hex_key)
            crypt_path = converter.convert(
                ios_backup_dir=ios_backup_dir,
                output_db_path=output_db_path,
                is_business=is_business,
                reference_crypt_path=reference_crypt_path,
            )
            ProgressEmitter.step("convert_done", "اكتمل إنشاء msgstore.db.crypt15.", 65)

            # دفع إلى الجهاز إن طُلب
            if push_to_device:
                ProgressEmitter.step("push", "دفع قاعدة البيانات والميديا إلى جهاز أندرويد...", 75)
                if not is_rooted:
                    raise RuntimeError(
                        "دفع msgstore.db.crypt15 إلى /data/data يتطلب صلاحيات Root. "
                        "قم بتشغيل الخيار is_rooted بعد التأكد من صلاحيات الجهاز."
                    )
                package = WHATSAPP_BUSINESS_PACKAGE if is_business else WHATSAPP_PACKAGE
                remote_db = f"/data/data/{package}/databases/msgstore.db.crypt15"
                serial_opt = ["-s", android_target_serial] if android_target_serial else []

                # إيقاف التطبيق أولاً
                import subprocess
                subprocess.run([self.dm.adb_path] + serial_opt + ["shell", "am", "force-stop", package],
                               check=False)
                # دفع الملف إلى /sdcard ثم نقله إلى /data/data
                tmp_remote = f"/sdcard/Download/msgstore.db.crypt15"
                subprocess.run([self.dm.adb_path] + serial_opt + ["push", crypt_path, tmp_remote],
                               check=True)
                subprocess.run([self.dm.adb_path] + serial_opt +
                               ["shell", "su", "-c",
                                f"cp {tmp_remote} {remote_db} && chown {package}:{package} {remote_db} && "
                                f"chmod 660 {remote_db} && rm {tmp_remote}"],
                               check=True)
                logger.info(f"تم دفع msgstore.db.crypt15 إلى {remote_db}")

                # دفع الميديا
                local_media = os.path.join(self.work_dir, "Media")
                temp_media = os.path.join(os.path.dirname(output_db_path), "temp_ios_to_android", "Media")
                if os.path.exists(temp_media):
                    shutil.copytree(temp_media, local_media, dirs_exist_ok=True)
                if os.path.exists(local_media):
                    remote_media = (ANDROID_WHATSAPP_BUSINESS_MEDIA if is_business else ANDROID_WHATSAPP_MEDIA)
                    subprocess.run([self.dm.adb_path] + serial_opt + ["push", local_media + "/.", remote_media],
                                   check=True)
                    logger.info(f"تم دفع مجلد الميديا إلى {remote_media}")

                ProgressEmitter.step("push_done", "اكتمل الدفع إلى الجهاز.", 95)

            ProgressEmitter.success(
                "اكتمل التحويل من iOS إلى Android بنجاح.",
                extra={"output_crypt": crypt_path}
            )
        except Exception as e:
            ProgressEmitter.error(f"فشل التحويل: {e}", traceback.format_exc())
            raise
        finally:
            self._cleanup()


# ---------- نقطة الدخول CLI ----------

def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="WhatsApp Bi-Directional Migration Engine")
    p.add_argument("--direction", required=True,
                   choices=["android_to_ios", "ios_to_android", "check_devices"],
                   help="اتجاه النقل")
    p.add_argument("--whatsapp-type", default="regular",
                   choices=["regular", "business"],
                   help="نوع واتساب")
    p.add_argument("--hex-key", required=True,
                   help="مفتاح فك/إعادة التشفير (64 حرف hex)")
    # مسارات Android → iOS
    p.add_argument("--android-db", help="مسار msgstore.db (أو crypt15) في أندرويد")
    p.add_argument("--android-media", help="مسار مجلد Media في أندرويد")
    p.add_argument("--ios-backup", help="مسار النسخة الاحتياطية لـ iOS")
    # مسارات iOS → Android
    p.add_argument("--output-db", help="مسار الإخراج لـ msgstore.db (سيُشفَّر إلى crypt15)")
    p.add_argument("--reference-crypt", help="مسار ملف crypt15 مرجعي (يُفضل)")
    p.add_argument("--output-dir", help="مجلد إخراج إضافي")
    # خيارات الدفع إلى أندرويد
    p.add_argument("--push-to-device", action="store_true", help="دفع الناتج إلى جهاز أندرويد")
    p.add_argument("--android-serial", help="الرقم التسلسلي لجهاز أندرويد")
    p.add_argument("--is-rooted", action="store_true", help="الجهاز لديه صلاحيات Root")
    return p


def main(argv: Optional[list] = None) -> int:
    setup_logging()
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if len(args.hex_key) != 64:
        ProgressEmitter.error("المفتاح يجب أن يكون 64 حرف hex.")
        return 2

    try:
        engine = MigrationEngine(hex_key=args.hex_key)
        is_business = args.whatsapp_type == "business"

        if args.direction == "check_devices":
            ProgressEmitter.step("check", "فحص الأجهزة المتصلة...", 50)
            import subprocess
            adb_out = subprocess.run([engine.dm.adb_path, "devices"], capture_output=True, text=True).stdout
            ProgressEmitter.success("تم الفحص.", extra={"adb": adb_out})
            return 0

        elif args.direction == "android_to_ios":
            required = ["android_db", "android_media", "ios_backup"]
            missing = [k for k in required if not getattr(args, k)]
            if missing:
                ProgressEmitter.error(f"وسائط مفقودة: {missing}")
                return 2
            engine.run_android_to_ios(
                android_db_path=args.android_db,
                android_media_path=args.android_media,
                ios_backup_dir=args.ios_backup,
                is_business=is_business,
                output_dir=args.output_dir,
            )
            return 0

        elif args.direction == "ios_to_android":
            if not args.output_db:
                ProgressEmitter.error("--output-db مطلوب.")
                return 2
            engine.run_ios_to_android(
                ios_backup_dir=args.ios_backup,
                output_db_path=args.output_db,
                is_business=is_business,
                reference_crypt_path=args.reference_crypt,
                android_target_serial=args.android_serial,
                push_to_device=args.push_to_device,
                is_rooted=args.is_rooted,
            )
            return 0

        return 0
    except Exception as e:
        ProgressEmitter.error(f"فشل عام: {e}", traceback.format_exc())
        return 1


if __name__ == "__main__":
    sys.exit(main())
