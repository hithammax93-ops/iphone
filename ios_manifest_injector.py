# ios_manifest_injector.py
import os
import sqlite3
import plistlib
import hashlib
import logging
import uuid
from datetime import datetime
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)

# مفاتيح NSKeyedArchiver
NS_KEYED_ARCHIVER = "NSKeyedArchiver"


class NSKeyedArchiverHelper:
    """
    مساعد للتعامل مع كائنات NSKeyedArchiver الموجودة في عمود 'file' بجدول Files
    داخل Manifest.db (الإصدارات الحديثة من iOS).
    """

    @staticmethod
    def is_nskeyed_archiver(data: bytes) -> bool:
        """فحص ما إذا كانت البيانات عبارة عن NSKeyedArchiver."""
        try:
            plist = plistlib.loads(data)
            return isinstance(plist, dict) and plist.get('$archiver') == NS_KEYED_ARCHIVER
        except Exception:
            return False

    @staticmethod
    def parse(data: bytes) -> Dict[str, Any]:
        """تحليل بيانات NSKeyedArchiver وإرجاعها كقاموس."""
        return plistlib.loads(data)

    @staticmethod
    def get_root_object(archive: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """الحصول على كائن الجذر (MBFile) من الأرشيف."""
        try:
            top = archive.get('$top', {})
            root_uid = top.get('root')
            if root_uid is None:
                return None
            objects = archive.get('$objects', [])
            # root_uid عادة يكون UID (كائن خاص بـ plistlib)
            if hasattr(root_uid, 'data'):
                idx = root_uid.data
            else:
                idx = int(root_uid)
            if 0 <= idx < len(objects):
                obj = objects[idx]
                if isinstance(obj, dict):
                    return obj
            return None
        except Exception as e:
            logger.error(f"فشل استخراج كائن الجذر من NSKeyedArchiver: {e}")
            return None

    @staticmethod
    def update_property(archive: Dict[str, Any], key: str, value: Any) -> bool:
        """تحديث خاصية في كائن الجذر (مثل Size أو Digest)."""
        root = NSKeyedArchiverHelper.get_root_object(archive)
        if root is None:
            return False
        root[key] = value
        return True

    @staticmethod
    def serialize(archive: Dict[str, Any]) -> bytes:
        """إعادة تسلسل الأرشيف إلى bytes."""
        return plistlib.dumps(archive, fmt=plistlib.FMT_BINARY)


class IOSManifestInjector:
    """
    فئة لحقن وتحديث السجلات في Manifest.db للنسخة الاحتياطية لـ iOS.
    تدعم كلاً من:
      - تنسيق plist القديم (قاموس مباشر).
      - تنسيق NSKeyedArchiver الحديث (كائن MBFile).
    """

    def __init__(self, backup_dir: str):
        self.backup_dir = backup_dir
        self.manifest_path = os.path.join(backup_dir, "Manifest.db")
        if not os.path.exists(self.manifest_path):
            raise FileNotFoundError(f"Manifest.db غير موجود في {backup_dir}")

    # ---------- دوال مساعدة ----------

    @staticmethod
    def compute_file_id(domain: str, relative_path: str) -> str:
        """
        حساب معرف الملف (SHA-1) المستخدم كاسم للملف داخل مجلد النسخة الاحتياطية.
        الصيغة: SHA1(domain + '-' + relativePath)
        """
        combined = f"{domain}-{relative_path}"
        return hashlib.sha1(combined.encode('utf-8')).hexdigest()

    def get_backup_file_path(self, file_id: str) -> str:
        """إرجاع مسار الملف الفعلي داخل النسخة الاحتياطية بناءً على file_id."""
        return os.path.join(self.backup_dir, file_id[:2], file_id)

    # ---------- عمليات قاعدة البيانات ----------

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.manifest_path)

    def get_entry(self, file_id: str) -> Optional[Dict[str, Any]]:
        """جلب سجل ملف من Manifest.db."""
        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT fileID, domain, relativePath, flags, file FROM Files WHERE fileID = ?",
            (file_id,)
        )
        row = cursor.fetchone()
        conn.close()
        if not row:
            return None
        return {
            'fileID': row[0],
            'domain': row[1],
            'relativePath': row[2],
            'flags': row[3],
            'file': row[4],
        }

    def _build_plist_payload(self, size: int, digest: bytes,
                             protection_class: int = 3,
                             existing_data: Optional[bytes] = None) -> bytes:
        """
        بناء بيانات عمود 'file' (إما plist عادي أو NSKeyedArchiver).
        إذا كانت existing_data موجودة و NSKeyedArchiver، نحدّثها. وإلا ننشئ plist عاديًا.
        """
        if existing_data and NSKeyedArchiverHelper.is_nskeyed_archiver(existing_data):
            archive = NSKeyedArchiverHelper.parse(existing_data)
            NSKeyedArchiverHelper.update_property(archive, 'Size', size)
            NSKeyedArchiverHelper.update_property(archive, 'Digest', digest)
            NSKeyedArchiverHelper.update_property(archive, 'ProtectionClass', protection_class)
            return NSKeyedArchiverHelper.serialize(archive)
        else:
            # plist عادي
            payload = {
                'Size': size,
                'Digest': digest,
                'ProtectionClass': protection_class,
            }
            return plistlib.dumps(payload, fmt=plistlib.FMT_BINARY)

    def update_entry(self, file_id: str, new_size: int, new_digest: bytes,
                     protection_class: int = 3) -> bool:
        """
        تحديث سجل موجود في Manifest.db (الحجم والبصمة).
        يدعم كلاً من plist العادي و NSKeyedArchiver.
        """
        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute("SELECT file FROM Files WHERE fileID = ?", (file_id,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            logger.warning(f"الملف {file_id} غير موجود في Manifest.db.")
            return False
        existing_data = row[0]
        new_data = self._build_plist_payload(
            new_size, new_digest, protection_class,
            existing_data=existing_data if isinstance(existing_data, bytes) else None
        )
        cursor.execute("UPDATE Files SET file = ? WHERE fileID = ?", (new_data, file_id))
        conn.commit()
        conn.close()
        logger.info(f"تم تحديث سجل Manifest.db للملف {file_id} (size={new_size}).")
        return True

    def add_entry(self, domain: str, relative_path: str, size: int, digest: bytes,
                  flags: int = 1, protection_class: int = 3) -> str:
        """
        إضافة سجل ملف جديد إلى Manifest.db.
        - flags=1: ملف عادي.
        - flags=2: مجلد (Directory).
        - flags=4: symlink.
        يعيد file_id.
        """
        file_id = self.compute_file_id(domain, relative_path)
        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute("SELECT fileID FROM Files WHERE fileID = ?", (file_id,))
        if cursor.fetchone():
            # موجود بالفعل - نحدّثه
            conn.close()
            self.update_entry(file_id, size, digest, protection_class)
            return file_id
        payload = self._build_plist_payload(size, digest, protection_class, existing_data=None)
        cursor.execute(
            "INSERT INTO Files (fileID, domain, relativePath, flags, file) VALUES (?, ?, ?, ?, ?)",
            (file_id, domain, relative_path, flags, payload)
        )
        conn.commit()
        conn.close()
        logger.info(f"تمت إضافة سجل جديد إلى Manifest.db: {relative_path} (size={size}).")
        return file_id

    def add_directory_entry(self, domain: str, relative_path: str, protection_class: int = 3) -> str:
        """إضافة سجل مجلد فارغ إلى Manifest.db."""
        file_id = self.compute_file_id(domain, relative_path)
        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute("SELECT fileID FROM Files WHERE fileID = ?", (file_id,))
        if cursor.fetchone():
            conn.close()
            return file_id
        payload = self._build_plist_payload(0, b'', protection_class, existing_data=None)
        cursor.execute(
            "INSERT INTO Files (fileID, domain, relativePath, flags, file) VALUES (?, ?, ?, ?, ?)",
            (file_id, domain, relative_path, 2, payload)  # flags=2 مجلد
        )
        conn.commit()
        conn.close()
        logger.info(f"تمت إضافة سجل مجلد إلى Manifest.db: {relative_path}.")
        return file_id

    def delete_entry(self, file_id: str) -> bool:
        """حذف سجل ملف من Manifest.db."""
        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM Files WHERE fileID = ?", (file_id,))
        deleted = cursor.rowcount > 0
        conn.commit()
        conn.close()
        if deleted:
            logger.info(f"تم حذف سجل Manifest.db للملف {file_id}.")
        return deleted

    # ---------- عملية حقن ملف فعلي في النسخة الاحتياطية ----------

    def inject_file(self, domain: str, relative_path: str, source_file_path: str,
                    protection_class: int = 3) -> str:
        """
        حقن ملف فعلي في النسخة الاحتياطية:
        1. حساب file_id.
        2. نسخ الملف إلى backup_dir/<aa>/<file_id>.
        3. إضافة/تحديث السجل في Manifest.db مع الحجم والبصمة الجديدة.
        """
        if not os.path.exists(source_file_path):
            raise FileNotFoundError(f"الملف المصدر غير موجود: {source_file_path}")

        with open(source_file_path, 'rb') as f:
            content = f.read()
        size = len(content)
        digest = hashlib.sha1(content).digest()

        file_id = self.compute_file_id(domain, relative_path)
        dest_path = self.get_backup_file_path(file_id)
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        with open(dest_path, 'wb') as f:
            f.write(content)

        # إضافة أو تحديث السجل
        if self.get_entry(file_id):
            self.update_entry(file_id, size, digest, protection_class)
        else:
            self.add_entry(domain, relative_path, size, digest, flags=1,
                           protection_class=protection_class)
        logger.info(f"تم حقن الملف {relative_path} في النسخة الاحتياطية ({size} بايت).")
        return file_id

    def inject_directory_tree(self, domain: str, base_relative_path: str,
                              local_dir: str) -> List[str]:
        """
        حقن شجرة مجلدات كاملة (مثل مجلد Media) في النسخة الاحتياطية.
        يعيد قائمة بمعرفات الملفات المحقونة.
        """
        injected = []
        for root, dirs, files in os.walk(local_dir):
            rel_root = os.path.relpath(root, local_dir).replace('\\', '/')
            if rel_root == '.':
                rel_root = ''
            for d in dirs:
                dir_rel = os.path.join(base_relative_path, rel_root, d).replace('\\', '/').strip('/')
                self.add_directory_entry(domain, dir_rel)
            for fname in files:
                src_file = os.path.join(root, fname)
                file_rel = os.path.join(base_relative_path, rel_root, fname).replace('\\', '/').strip('/')
                fid = self.inject_file(domain, file_rel, src_file)
                injected.append(fid)
        return injected

    # ---------- إزالة WAL و SHM ----------

    def remove_wal_shm(self, domain: str, relative_path: str) -> None:
        """
        حذف ملفي -wal و -shm المرتبطين بقاعدة بيانات (مثل ChatStorage.sqlite).
        يجب استدعاؤها بعد تعديل قاعدة البيانات لضمان قراءتها بشكل صحيح.
        """
        for suffix in ('-wal', '-shm'):
            rel = relative_path + suffix
            file_id = self.compute_file_id(domain, rel)
            entry = self.get_entry(file_id)
            if entry:
                self.delete_entry(file_id)
            # حذف الملف الفعلي إن وجد
            actual_path = self.get_backup_file_path(file_id)
            if os.path.exists(actual_path):
                try:
                    os.remove(actual_path)
                    logger.info(f"تم حذف الملف الفعلي {actual_path}.")
                except OSError as e:
                    logger.warning(f"فشل حذف {actual_path}: {e}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    # مثال للاستخدام (للتجربة):
    # inj = IOSManifestInjector("/path/to/ios/backup")
    # inj.inject_file("AppDomainGroup-group.net.whatsapp.WhatsApp.shared",
    #                 "ChatStorage.sqlite", "/path/to/new/ChatStorage.sqlite")
    print("IOSManifestInjector جاهز.")
