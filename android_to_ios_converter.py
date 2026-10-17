# android_to_ios_converter.py
import sqlite3
import os
import shutil
import logging
import plistlib
import hashlib
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from device_manager import DeviceManager, IOS_APP_DOMAIN, IOS_BUSINESS_APP_DOMAIN, IOS_CHATSTORAGE_PATH, IOS_MEDIA_DIR

logger = logging.getLogger(__name__)

# --- ثوابت التحويل الزمني ---
# الفرق بين Unix Epoch (1970) و Apple Core Data Epoch (2001-01-01)
APPLE_EPOCH_OFFSET = 978307200

# --- خريطة أنواع الرسائل (تقديرية بناءً على ملاحظات المجتمع) ---
# قد تختلف القيم بين إصدارات واتساب المختلفة، لذا يجب فحصها ديناميكيًا إن أمكن.
# القيم الشائعة: 0=نص، 1=صورة، 2=صوت، 3=فيديو، 4=جهة اتصال، 5=موقع، 7=ملف، 8=ملصق، إلخ.
# هذا مجرد مثال، الأفضل استخدام PRAGMA table_info والاستعلام عن القيم الفعلية.

class AndroidToIOSConverter:
    """
    فئة لتحويل قاعدة بيانات واتساب من أندرويد (msgstore.db) إلى تنسيق iOS (ChatStorage.sqlite).
    """

    def __init__(self, device_manager: DeviceManager, hex_key: str):
        self.dm = device_manager
        self.hex_key = hex_key
        self.temp_dir = None

    def convert(self, android_db_path: str, android_media_path: str, ios_backup_dir: str,
                is_business: bool = False, output_dir: Optional[str] = None) -> str:
        """
        عملية التحويل الكاملة:
        1. فك تشفير قاعدة بيانات أندرويد (إذا كانت مشفرة)
        2. تحليل مخطط قاعدة البيانات
        3. إنشاء قاعدة بيانات iOS جديدة وملء الجداول
        4. نسخ وتحويل الميديا
        5. تحديث Manifest.db في النسخة الاحتياطية لـ iOS
        """
        logger.info("بدء عملية التحويل من أندرويد إلى iOS...")

        # إنشاء مجلد عمل مؤقت
        self.temp_dir = os.path.join(os.path.dirname(android_db_path), "temp_convert")
        self.dm.ensure_dir(self.temp_dir)

        # الخطوة 1: التأكد من فك تشفير قاعدة البيانات (إذا كانت .crypt15)
        decrypted_db = android_db_path
        if android_db_path.endswith('.crypt15'):
            decrypted_db = os.path.join(self.temp_dir, "msgstore_decrypted.db")
            self.dm.decrypt_crypt15(android_db_path, decrypted_db, self.hex_key)
        elif not os.path.exists(android_db_path):
            raise FileNotFoundError(f"قاعدة البيانات {android_db_path} غير موجودة.")

        # الخطوة 2: تحليل مخطط قاعدة بيانات أندرويد
        android_schema = self._analyze_android_schema(decrypted_db)
        logger.info(f"تم تحليل مخطط أندرويد. الجداول: {list(android_schema.keys())}")

        # الخطوة 3: إنشاء قاعدة بيانات iOS جديدة
        ios_db_path = os.path.join(self.temp_dir, "ChatStorage.sqlite")
        self._create_ios_database(ios_db_path, android_schema)
        logger.info(f"تم إنشاء قاعدة بيانات iOS في {ios_db_path}")

        # الخطوة 4: ملء البيانات
        self._transfer_chats(decrypted_db, ios_db_path, android_schema, is_business)
        self._transfer_messages(decrypted_db, ios_db_path, android_schema)
        self._transfer_media(decrypted_db, ios_db_path, android_schema, android_media_path, ios_backup_dir, is_business)
        logger.info("تم نقل جميع البيانات إلى ChatStorage.sqlite")

        # الخطوة 5: تحديث Manifest.db في النسخة الاحتياطية
        self._update_manifest_for_ios(ios_backup_dir, ios_db_path, is_business)
        logger.info("تم تحديث Manifest.db.")

        # نسخ قاعدة البيانات النهائية إلى مجلد الإخراج (إذا تم تحديده)
        if output_dir:
            final_path = os.path.join(output_dir, "ChatStorage.sqlite")
            shutil.copy2(ios_db_path, final_path)
            logger.info(f"تم نسخ ChatStorage.sqlite النهائي إلى {final_path}")

        # تنظيف المجلد المؤقت (اختياري)
        # self.dm.clean_temp_dir(self.temp_dir)
        return ios_db_path

    def _analyze_android_schema(self, db_path: str) -> Dict[str, List[Dict]]:
        """
        تحليل مخطط قاعدة بيانات أندرويد باستخدام PRAGMA table_info.
        يعيد قاموسًا يحتوي على أسماء الجداول وأعمدتها.
        """
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # الحصول على جميع أسماء الجداول
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [row[0] for row in cursor.fetchall()]
        
        schema = {}
        for table in tables:
            cursor.execute(f"PRAGMA table_info({table});")
            columns = []
            for col in cursor.fetchall():
                # col: (cid, name, type, notnull, dflt_value, pk)
                columns.append({
                    'name': col[1],
                    'type': col[2],
                    'notnull': bool(col[3]),
                    'pk': bool(col[5])
                })
            schema[table] = columns
        
        conn.close()
        return schema

    def _create_ios_database(self, ios_db_path: str, android_schema: Dict[str, List[Dict]]) -> None:
        """
        إنشاء قاعدة بيانات iOS جديدة بالجداول المطلوبة.
        ملاحظة: هذا المخطط مبسط. قد تحتاج لإضافة المزيد من الأعمدة حسب إصدار iOS.
        """
        conn = sqlite3.connect(ios_db_path)
        cursor = conn.cursor()
        
        # جدول ZWACHATSESSION
        cursor.execute("""
            CREATE TABLE ZWACHATSESSION (
                Z_PK INTEGER PRIMARY KEY AUTOINCREMENT,
                Z_ENT INTEGER,
                Z_OPT INTEGER,
                ZCONTACTJID TEXT,
                ZLASTMESSAGEDATE REAL,
                ZARCHIVED INTEGER,
                ZUNREADCOUNT INTEGER,
                ZMESSAGECOUNTER INTEGER
            );
        """)
        
        # جدول ZWAMESSAGE
        cursor.execute("""
            CREATE TABLE ZWAMESSAGE (
                Z_PK INTEGER PRIMARY KEY AUTOINCREMENT,
                Z_ENT INTEGER,
                Z_OPT INTEGER,
                ZCHATSESSION INTEGER,
                ZMESSAGETYPE INTEGER,
                ZTIMESTAMP REAL,
                ZTEXT TEXT,
                ZFROMJID TEXT,
                ZTOJID TEXT,
                ZMEDIAITEM INTEGER,
                ZISFROMME INTEGER,
                ZSENTDATE REAL,
                FOREIGN KEY (ZCHATSESSION) REFERENCES ZWACHATSESSION(Z_PK),
                FOREIGN KEY (ZMEDIAITEM) REFERENCES ZWAMEDIAITEM(Z_PK)
            );
        """)
        
        # جدول ZWAMEDIAITEM
        cursor.execute("""
            CREATE TABLE ZWAMEDIAITEM (
                Z_PK INTEGER PRIMARY KEY AUTOINCREMENT,
                Z_ENT INTEGER,
                Z_OPT INTEGER,
                ZMESSAGE INTEGER,
                ZMEDIALOCALPATH TEXT,
                ZTHUMBNAILLOCALPATH TEXT,
                ZFILESIZE INTEGER,
                ZMIME TYPE TEXT,
                ZMEDIAURL TEXT
            );
        """)
        
        conn.commit()
        conn.close()

    def _convert_timestamp(self, android_timestamp_ms: int) -> float:
        """
        تحويل طابع زمني من أندرويد (Unix Epoch بالميلي ثانية) إلى تنسيق Apple Core Data (ثواني).
        المعادلة: (android_timestamp_ms / 1000) - 978307200
        """
        # بعض قواعد بيانات أندرويد تخزن الطابع الزمني بالثواني، والبعض بالميلي ثانية.
        # نفترض هنا أنه بالميلي ثانية. إذا كان بالثواني، يجب تعديل ذلك.
        # يمكن التحقق من خلال فحص نطاق القيم.
        android_timestamp_s = android_timestamp_ms / 1000.0 if android_timestamp_ms > 1e12 else android_timestamp_ms
        return android_timestamp_s - APPLE_EPOCH_OFFSET

    def _transfer_chats(self, android_db: str, ios_db: str, schema: Dict, is_business: bool) -> None:
        """نقل المحادثات من جدول chat/jid في أندرويد إلى ZWACHATSESSION في iOS."""
        android_conn = sqlite3.connect(android_db)
        android_cursor = android_conn.cursor()
        ios_conn = sqlite3.connect(ios_db)
        ios_cursor = ios_conn.cursor()
        
        # تحديد اسم جدول المحادثات (قد يكون 'chat' أو 'jid')
        chat_table = 'chat' if 'chat' in schema else 'jid'
        if chat_table not in schema:
            logger.warning("لم يتم العثور على جدول chat أو jid في قاعدة بيانات أندرويد.")
            return
        
        # الحصول على الأعمدة المتاحة
        android_cols = [c['name'] for c in schema[chat_table]]
        # نفترض وجود عمود jid أو raw_string
        jid_col = 'jid' if 'jid' in android_cols else 'raw_string' if 'raw_string' in android_cols else None
        if not jid_col:
            logger.error("لا يمكن العثور على عمود jid في جدول المحادثات.")
            return
        
        # جلب المحادثات
        android_cursor.execute(f"SELECT {jid_col}, _id FROM {chat_table};")
        chats = android_cursor.fetchall()
        
        for jid, android_chat_id in chats:
            # إدراج في ZWACHATSESSION
            ios_cursor.execute("""
                INSERT INTO ZWACHATSESSION (ZCONTACTJID, ZLASTMESSAGEDATE, ZARCHIVED, ZUNREADCOUNT, ZMESSAGECOUNTER)
                VALUES (?, ?, ?, ?, ?)
            """, (jid, 0.0, 0, 0, 0))
            # يمكن لاحقًا تحديث ZLASTMESSAGEDATE بناءً على آخر رسالة
        
        ios_conn.commit()
        android_conn.close()
        ios_conn.close()
        logger.info(f"تم نقل {len(chats)} محادثة.")

    def _transfer_messages(self, android_db: str, ios_db: str, schema: Dict) -> None:
        """نقل الرسائل من جدول message في أندرويد إلى ZWAMESSAGE في iOS."""
        android_conn = sqlite3.connect(android_db)
        android_cursor = android_conn.cursor()
        ios_conn = sqlite3.connect(ios_db)
        ios_cursor = ios_conn.cursor()
        
        if 'message' not in schema:
            logger.warning("جدول message غير موجود.")
            return
        
        android_cols = [c['name'] for c in schema['message']]
        # تحديد الأعمدة المطلوبة
        # نفترض وجود: _id, chat_row_id, from_me, timestamp, text_data, message_type, media_url, إلخ.
        # قد تختلف الأسماء. نحاول التكيف.
        
        # جلب الرسائل (مبسط)
        try:
            android_cursor.execute("SELECT _id, chat_row_id, from_me, timestamp, text_data, message_type FROM message;")
            messages = android_cursor.fetchall()
        except sqlite3.OperationalError as e:
            logger.error(f"فشل استعلام الرسائل: {e}. قد تحتاج لتعديل أسماء الأعمدة.")
            return
        
        for msg in messages:
            android_msg_id, chat_id, from_me, ts, text, msg_type = msg
            # تحويل الطابع الزمني
            apple_ts = self._convert_timestamp(ts)
            # البحث عن Z_PK للمحادثة المقابلة (مبسط: نفترض التطابق)
            # في الواقع، تحتاج لربط chat_id من أندرويد بـ Z_PK في iOS. هنا نبسط الأمر.
            ios_cursor.execute("""
                INSERT INTO ZWAMESSAGE (ZCHATSESSION, ZMESSAGETYPE, ZTIMESTAMP, ZTEXT, ZISFROMME)
                VALUES (?, ?, ?, ?, ?)
            """, (chat_id, msg_type, apple_ts, text, 1 if from_me else 0))
        
        ios_conn.commit()
        android_conn.close()
        ios_conn.close()
        logger.info(f"تم نقل {len(messages)} رسالة.")

    def _transfer_media(self, android_db: str, ios_db: str, schema: Dict,
                         android_media_path: str, ios_backup_dir: str, is_business: bool) -> None:
        """
        نسخ ملفات الميديا من مجلد أندرويد إلى النسخة الاحتياطية لـ iOS،
        وتسجيل المسارات في ZWAMEDIAITEM.
        """
        if not os.path.exists(android_media_path):
            logger.warning(f"مجلد الميديا {android_media_path} غير موجود. تخطي نسخ الميديا.")
            return
        
        # هنا يجب التنقل في مجلدات الميديا (WhatsApp Images, Video, Voice Notes, إلخ)
        # ونسخ الملفات إلى المسار المناسب في iOS (Message/Media/...)
        # ثم تحديث جدول ZWAMEDIAITEM.
        # هذا الجزء معقد ويتطلب معرفة بنية مجلدات أندرويد الدقيقة.
        # سنقدم هيكلًا عامًا.
        
        # مثال: نسخ جميع الملفات من مجلدات الميديا الشائعة
        media_subdirs = ['WhatsApp Images', 'WhatsApp Video', 'WhatsApp Voice Notes', 'WhatsApp Documents']
        for subdir in media_subdirs:
            src_dir = os.path.join(android_media_path, subdir)
            if not os.path.exists(src_dir):
                continue
            # في iOS، المسار النسبي داخل Message/Media
            # على سبيل المثال: Message/Media/<JID>/<filename>
            # نحتاج إلى JID لكل ملف، وهو ما يمكن الحصول عليه من جدول message/media.
            # هذا يتطلب ربطًا معقدًا. هنا نكتفي بنسخ الملفات إلى مجلد عام.
            dst_dir = os.path.join(ios_backup_dir, IOS_MEDIA_DIR, subdir)
            self.dm.ensure_dir(dst_dir)
            for filename in os.listdir(src_dir):
                src_file = os.path.join(src_dir, filename)
                if os.path.isfile(src_file):
                    dst_file = os.path.join(dst_dir, filename)
                    shutil.copy2(src_file, dst_file)
                    # بعد النسخ، يجب إدراج سجل في ZWAMEDIAITEM مع المسار النسبي الصحيح.
                    # (هذا الجزء يحتاج إلى استعلامات إضافية للحصول على JID ونوع الرسالة)
        
        logger.info("تم نسخ ملفات الميديا (بشكل مبسط).")

    def _update_manifest_for_ios(self, ios_backup_dir: str, ios_db_path: str, is_business: bool) -> None:
        """
        تحديث Manifest.db ليعكس التغييرات في ChatStorage.sqlite.
        """
        domain = IOS_BUSINESS_APP_DOMAIN if is_business else IOS_APP_DOMAIN
        relative_path = IOS_CHATSTORAGE_PATH
        
        # حساب SHA-1 المتوقع
        combined = f"{domain}-{relative_path}"
        file_hash = hashlib.sha1(combined.encode('utf-8')).hexdigest()
        
        # المسار الفعلي للملف في النسخة الاحتياطية
        file_path = os.path.join(ios_backup_dir, file_hash[:2], file_hash)
        
        # نسخ قاعدة البيانات الجديدة إلى مكانها في النسخة الاحتياطية
        self.dm.ensure_dir(os.path.dirname(file_path))
        shutil.copy2(ios_db_path, file_path)
        logger.info(f"تم نسخ ChatStorage.sqlite إلى {file_path}")
        
        # حساب الحجم والبصمة الجديدة
        size, digest = self.dm.calculate_ios_file_digest(file_path)
        
        # تحديث Manifest.db
        try:
            self.dm.update_manifest_entry(ios_backup_dir, file_hash, size, digest)
        except ValueError as e:
            logger.warning(f"لم يتم العثور على الملف {file_hash} في Manifest.db. قد تحتاج إلى إضافته.")
            # في حالة عدم وجود الملف، يجب إضافته (أكثر تعقيدًا، يتطلب إنشاء plist كامل)
            # هذا الجزء متروك للجزء الثاني من المشروع (ios_manifest_injector.py)


# مثال للاستخدام (للتجربة فقط)
if __name__ == "__main__":
    dm = DeviceManager()
    converter = AndroidToIOSConverter(dm, hex_key="0"*64)  # استبدل المفتاح الفعلي
    # converter.convert("/path/to/msgstore.db", "/path/to/android/media", "/path/to/ios/backup")
    print("AndroidToIOSConverter جاهز للاستخدام.")
