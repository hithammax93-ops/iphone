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

    def __init__(self, adb_path: str = "adb", pymobiledevice3_path: str = "python -m pymobiledevice3"):
        self.adb_path = adb_path
        self.pymobiledevice3_path = pymobiledevice3_path
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
        """فك تشفير ملف msgstore.db.crypt15 باستخدام مفتاح hex."""
        logger.info(f"فك تشفير {encrypted_db_path}...")
        # التحقق من صحة المفتاح
        if len(hex_key) != 64 or not all(c in '0123456789abcdefABCDEF' for c in hex_key):
            raise ValueError("المفتاح يجب أن يكون 64 حرفًا hex (0-9, a-f).")
        
        # استخدام أداة wadecrypt من wa-crypt-tools
        # wadecrypt <keyfile_or_hex> <encrypted> <decrypted>
        cmd = ["wadecrypt", hex_key, encrypted_db_path, output_db_path]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            logger.info(f"تم فك التشفير بنجاح إلى {output_db_path}")
        except subprocess.CalledProcessError as e:
            logger.error(f"فشل فك التشفير: {e.stderr}")
            raise RuntimeError(f"فشل فك التشفير: {e.stderr}")
        except FileNotFoundError:
            logger.error("أداة wadecrypt غير موجودة. تأكد من تثبيت wa-crypt-tools.")
            raise

    def encrypt_crypt15(self, db_path: str, output_crypt_path: str, hex_key: str = DEFAULT_HEX_KEY,
                        reference_crypt_path: Optional[str] = None) -> None:
        """إعادة تشفير msgstore.db إلى msgstore.db.crypt15."""
        logger.info(f"إعادة تشفير {db_path} إلى {output_crypt_path}...")
        # استخدام أداة waencrypt من wa-crypt-tools
        # waencrypt --reference <reference.crypt15> <keyfile> <decrypted> <encrypted>
        if reference_crypt_path and os.path.exists(reference_crypt_path):
            cmd = ["waencrypt", "--reference", reference_crypt_path, hex_key, db_path, output_crypt_path]
        else:
            # في حال عدم وجود ملف مرجعي، قد تحتاج لمعاملات إضافية (feature list, version, jid)
            # هذا تنبيه: التشفير بدون مرجع قد لا يعمل بشكل موثوق في كل الإصدارات.
            logger.warning("لا يوجد ملف مرجعي. محاولة التشفير بدون مرجع قد تفشل.")
            cmd = ["waencrypt", hex_key, db_path, output_crypt_path]
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            logger.info(f"تم التشفير بنجاح إلى {output_crypt_path}")
        except subprocess.CalledProcessError as e:
            logger.error(f"فشل التشفير: {e.stderr}")
            raise RuntimeError(f"فشل التشفير: {e.stderr}")

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
        """
        تحديث بيانات ملف في Manifest.db (الحجم والبصمة).
        يُستخدم بعد تعديل محتوى ملف (مثل ChatStorage.sqlite).
        """
        manifest_path = self.get_ios_manifest_path(backup_dir)
        conn = sqlite3.connect(manifest_path)
        cursor = conn.cursor()
        
        # جلب البيانات الحالية للملف
        cursor.execute("SELECT file FROM Files WHERE fileID = ?", (file_hash,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            raise ValueError(f"الملف {file_hash} غير موجود في Manifest.db")
        
        # تحليل plist الموجود في العمود 'file'
        file_data = plistlib.loads(row[0])
        
        # تحديث الحجم والبصمة
        file_data['Size'] = new_size
        file_data['Digest'] = new_digest
        
        # إعادة تحويله إلى plist وحفظه
        new_plist = plistlib.dumps(file_data)
        
        cursor.execute("UPDATE Files SET file = ? WHERE fileID = ?", (new_plist, file_hash))
        conn.commit()
        conn.close()
        logger.info(f"تم تحديث Manifest.db للملف {file_hash}")

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
