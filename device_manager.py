# device_manager.py
import os
import subprocess
import shutil
import tempfile
import logging
import plistlib
import sqlite3
import hashlib
from pathlib import Path
from typing import Optional, Dict, Any, Tuple

# إعداد نظام التسجيل (Logging)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- الثوابت ---
# حزم واتساب على أندرويد
WHATSAPP_PACKAGE = "com.whatsapp"
WHATSAPP_BUSINESS_PACKAGE = "com.whatsapp.w4b"

# مسارات الميديا على أندرويد
ANDROID_MEDIA_BASE = "/sdcard/Android/media/"
ANDROID_WHATSAPP_MEDIA = os.path.join(ANDROID_MEDIA_BASE, WHATSAPP_PACKAGE)
ANDROID_WHATSAPP_BUSINESS_MEDIA = os.path.join(ANDROID_MEDIA_BASE, WHATSAPP_BUSINESS_PACKAGE)

# مسارات قاعدة البيانات على أندرويد (تتطلب صلاحيات root أو نسخ احتياطي)
ANDROID_DB_PATH = "/data/data/{package}/databases/msgstore.db"
ANDROID_DB_CRYPT_PATH = "/data/data/{package}/databases/msgstore.db.crypt15"

# نطاقات (Domains) واتساب على iOS
IOS_APP_DOMAIN = "AppDomainGroup-group.net.whatsapp.WhatsApp.shared"
IOS_BUSINESS_APP_DOMAIN = "AppDomainGroup-group.net.whatsapp.WhatsAppSMB.shared"

# المسارات النسبية داخل نطاق iOS
IOS_CHATSTORAGE_PATH = "ChatStorage.sqlite"
IOS_MEDIA_DIR = "Message/Media"

# مفتاح فك التشفير (64 حرف hex)
DEFAULT_HEX_KEY = "0" * 64  # يجب استبداله بالمفتاح الفعلي

class DeviceManager:
    """
    فئة مسؤولة عن إدارة التفاعلات مع أجهزة Android (عبر ADB) و iOS (عبر pymobiledevice3).
    """

    def __init__(self, adb_path: str = "adb", pymobiledevice3_path: str = "python -m pymobiledevice3",
                 check_tools: bool = True):
        self.adb_path = adb_path
        self.pymobiledevice3_path = pymobiledevice3_path
        if check_tools:
            self._check_adb()
            self._check_pymobiledevice3()

    def _check_adb(self):
        try:
            subprocess.run([self.adb_path, "version"], capture_output=True, check=True)
            logger.info("ADB متاح وجاهز.")
        except (subprocess.CalledProcessError, FileNotFoundError):
            logger.error("ADB غير موجود أو غير قابل للتنفيذ. تأكد من تثبيت Android Platform Tools.")
            raise

    def _check_pymobiledevice3(self):
        try:
            cmd = self.pymobiledevice3_path.split() + ["version"]
            subprocess.run(cmd, capture_output=True, check=True)
            logger.info("pymobiledevice3 متاح وجاهز.")
        except (subprocess.CalledProcessError, FileNotFoundError):
            logger.error("pymobiledevice3 غير موجود. قم بتثبيته عبر: pip install pymobiledevice3")
            raise

    # ---------- عمليات ADB (أندرويد) ----------

    def adb_shell(self, command: str) -> str:
        """تنفيذ أمر shell على جهاز أندرويد وإرجاع المخرجات."""
        result = subprocess.run([self.adb_path, "shell", command], capture_output=True, text=True)
        if result.returncode != 0:
            logger.error(f"فشل أمر ADB shell: {command} - {result.stderr}")
            raise RuntimeError(f"ADB shell error: {result.stderr}")
        return result.stdout.strip()

    def adb_pull(self, remote_path: str, local_path: str) -> None:
        """سحب ملف أو مجلد من أندرويد إلى الكمبيوتر."""
        logger.info(f"سحب {remote_path} إلى {local_path}")
        result = subprocess.run([self.adb_path, "pull", remote_path, local_path], capture_output=True, text=True)
        if result.returncode != 0:
            logger.error(f"فشل سحب ADB: {result.stderr}")
            raise RuntimeError(f"ADB pull error: {result.stderr}")

    def adb_push(self, local_path: str, remote_path: str) -> None:
        """دفع ملف أو مجلد من الكمبيوتر إلى أندرويد."""
        logger.info(f"دفع {local_path} إلى {remote_path}")
        # التأكد من وجود المجلد البعيد
        remote_dir = os.path.dirname(remote_path)
        self.adb_shell(f"mkdir -p {remote_dir}")
        result = subprocess.run([self.adb_path, "push", local_path, remote_path], capture_output=True, text=True)
        if result.returncode != 0:
            logger.error(f"فشل دفع ADB: {result.stderr}")
            raise RuntimeError(f"ADB push error: {result.stderr}")

    def get_android_db_path(self, is_business: bool = False) -> str:
        """إرجاع مسار قاعدة بيانات msgstore.db على أندرويد."""
        package = WHATSAPP_BUSINESS_PACKAGE if is_business else WHATSAPP_PACKAGE
        return ANDROID_DB_PATH.format(package=package)

    def get_android_media_path(self, is_business: bool = False) -> str:
        """إرجاع مسار مجلد الميديا على أندرويد."""
        return ANDROID_WHATSAPP_BUSINESS_MEDIA if is_business else ANDROID_WHATSAPP_MEDIA

    # ---------- عمليات التشفير (wa-crypt-tools) ----------

    def decrypt_crypt15(self, encrypted_db_path: str, output_db_path: str, hex_key: str = DEFAULT_HEX_KEY) -> None:
        """Decrypt with upstream tooling, without forwarding potentially secret diagnostics."""
        self._validate_key(hex_key)
        self._run_crypto(["wadecrypt", hex_key, encrypted_db_path, output_db_path])
        if not os.path.isfile(output_db_path):
            raise RuntimeError("لم تُنتج أداة فك التشفير ملفًا.")
        with open(output_db_path, "rb") as stream:
            if stream.read(16) != b"SQLite format 3\x00":
                raise RuntimeError("ناتج فك التشفير ليس قاعدة SQLite صالحة.")

    def encrypt_crypt15(self, db_path: str, output_crypt_path: str, hex_key: str = DEFAULT_HEX_KEY,
                        reference_crypt_path: Optional[str] = None) -> None:
        """Upstream beta encryption only; never manufacture a crypt15 header."""
        self._validate_key(hex_key)
        if not reference_crypt_path or not os.path.isfile(reference_crypt_path):
            raise ValueError("التشفير يتطلب ملف crypt15 مرجعيًا؛ لا تُستخدم ترويسة تقديرية.")
        self._run_crypto(["waencrypt", "--reference", reference_crypt_path,
                          hex_key, db_path, output_crypt_path])
        if not os.path.isfile(output_crypt_path) or os.path.getsize(output_crypt_path) == 0:
            raise RuntimeError("لم تُنتج أداة التشفير ملفًا صالحًا للفحص.")

    @staticmethod
    def _validate_key(hex_key: str) -> None:
        if len(hex_key) != 64 or not all(c in '0123456789abcdefABCDEF' for c in hex_key):
            raise ValueError("المفتاح يجب أن يكون 64 حرفًا hex صالحًا.")

    @staticmethod
    def _run_crypto(command: list) -> None:
        try:
            subprocess.run(command, capture_output=True, check=True, timeout=1800)
        except FileNotFoundError:
            raise RuntimeError("أداة wa-crypt-tools غير متوفرة في PATH.") from None
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            # Upstream errors can echo argv (including the full key). Never relay them.
            raise RuntimeError("فشلت أداة التشفير أو انتهت المهلة؛ تحقق من المفتاح والملف محليًا.") from None

    # ---------- عمليات iOS (pymobiledevice3) ----------

    def pull_ios_backup(self, udid: str, backup_dir: str, password: str = "") -> str:
        """
        إنشاء نسخة احتياطية كاملة لجهاز iOS.
        يعيد مسار النسخة الاحتياطية.
        """
        logger.info(f"بدء النسخ الاحتياطي لجهاز iOS {udid} إلى {backup_dir}")
        os.makedirs(backup_dir, exist_ok=True)
        # pymobiledevice3 backup2 backup --udid <udid> --directory <backup_dir>
        cmd = self.pymobiledevice3_path.split() + [
            "backup2", "backup",
            "--udid", udid,
            "--directory", backup_dir
        ]
        if password:
            cmd.extend(["--password", password])
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            logger.info("تم إنشاء النسخة الاحتياطية بنجاح.")
            return backup_dir
        except subprocess.CalledProcessError as e:
            logger.error(f"فشل النسخ الاحتياطي: {e.stderr}")
            raise RuntimeError(f"فشل النسخ الاحتياطي: {e.stderr}")

    def get_ios_manifest_path(self, backup_dir: str) -> str:
        """إرجاع مسار ملف Manifest.db داخل النسخة الاحتياطية."""
        return os.path.join(backup_dir, "Manifest.db")

    def find_ios_file_in_manifest(self, backup_dir: str, domain: str, relative_path: str) -> Optional[str]:
        """
        البحث عن ملف في Manifest.db باستخدام النطاق والمسار النسبي،
        وإرجاع مسار الملف الفعلي داخل مجلد النسخة الاحتياطية (الذي يكون اسمه SHA-1).
        """
        manifest_path = self.get_ios_manifest_path(backup_dir)
        if not os.path.exists(manifest_path):
            logger.error("ملف Manifest.db غير موجود.")
            return None
        
        # حساب SHA-1 المتوقع
        combined = f"{domain}-{relative_path}"
        file_hash = hashlib.sha1(combined.encode('utf-8')).hexdigest()
        
        # المسار الفعلي للملف في النسخة الاحتياطية
        file_path = os.path.join(backup_dir, file_hash[:2], file_hash)
        
        if os.path.exists(file_path):
            return file_path
        else:
            # قد لا يكون الملف موجودًا في هذه النسخة (مثلاً، لم يتم نسخه احتياطيًا)
            logger.warning(f"الملف {relative_path} في النطاق {domain} غير موجود في النسخة الاحتياطية.")
            return None

    def extract_ios_file(self, backup_dir: str, domain: str, relative_path: str, output_path: str) -> bool:
        """استخراج ملف معين من النسخة الاحتياطية إلى المسار المحدد."""
        source_file = self.find_ios_file_in_manifest(backup_dir, domain, relative_path)
        if source_file:
            shutil.copy2(source_file, output_path)
            logger.info(f"تم استخراج {relative_path} إلى {output_path}")
            return True
        return False

    def list_ios_backup_files(self, backup_dir: str, domain_filter: Optional[str] = None) -> list:
        """سرد الملفات في Manifest.db، مع إمكانية التصفية حسب النطاق."""
        manifest_path = self.get_ios_manifest_path(backup_dir)
        conn = sqlite3.connect(manifest_path)
        cursor = conn.cursor()
        query = "SELECT fileID, domain, relativePath, flags, file FROM Files"
        params = []
        if domain_filter:
            query += " WHERE domain = ?"
            params.append(domain_filter)
        
        cursor.execute(query, params)
        files = cursor.fetchall()
        conn.close()
        return files

    def update_manifest_entry(self, backup_dir: str, file_hash: str, new_size: int, new_digest: bytes) -> None:
        """Use the same binary/NSKeyedArchiver-aware metadata updater everywhere."""
        from ios_manifest_injector import IOSManifestInjector
        if not IOSManifestInjector(backup_dir).update_entry(file_hash, new_size, new_digest):
            raise ValueError(f"الملف {file_hash} غير موجود في Manifest.db")

    def calculate_ios_file_digest(self, file_path: str) -> Tuple[int, bytes]:
        """حساب الحجم وبصمة SHA-1 لملف (للاستخدام في Manifest.db)."""
        with open(file_path, 'rb') as f:
            content = f.read()
        size = len(content)
        digest = hashlib.sha1(content).digest()
        return size, digest

    # ---------- دوال مساعدة ----------

    def ensure_dir(self, path: str) -> None:
        """التأكد من وجود المجلد."""
        os.makedirs(path, exist_ok=True)

    def clean_temp_dir(self, temp_dir: str) -> None:
        """حذف مجلد مؤقت."""
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)

if __name__ == "__main__":
    # مثال للاستخدام (للتجربة فقط)
    dm = DeviceManager()
    print("DeviceManager تم تهيئته بنجاح.")
