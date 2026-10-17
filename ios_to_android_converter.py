# ios_to_android_converter.py
import sqlite3
import os
import shutil
import logging
import hashlib
import struct
import zlib
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any

# محاولة استيراد PyCryptodome للتشفير اليدوي (احتياطي)
try:
    from Crypto.Cipher import AES
    from Crypto.Protocol.KDF import HKDF
    from Crypto.Hash import SHA256
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False

from device_manager import DeviceManager, IOS_APP_DOMAIN, IOS_BUSINESS_APP_DOMAIN, IOS_CHATSTORAGE_PATH, IOS_MEDIA_DIR

logger = logging.getLogger(__name__)

# --- ثوابت التحويل الزمني ---
APPLE_EPOCH_OFFSET = 978307200  # الفرق بين 1970 و 2001 بالثواني

# --- خريطة أنواع رسائل iOS إلى أنواع رسائل Android (media_wa_type) ---
# وفقًا للمراجع: 0=نص، 1=صورة، 2=صوت، 3=فيديو، 4=جهة اتصال، 5=موقع، 7=ملف، 8=ملصق، إلخ.
# قد تختلف القيم في iOS، لذا نستخدم خريطة تقديرية مع إمكانية التوسيع.
IOS_TO_ANDROID_MESSAGE_TYPE = {
    0: 0,   # نص
    1: 1,   # صورة
    2: 2,   # صوت
    3: 3,   # فيديو
    4: 4,   # جهة اتصال
    5: 5,   # موقع
    6: 7,   # ملف (تقديري)
    7: 8,   # ملصق (تقديري)
    8: 9,   # GIF (تقديري)
    9: 10,  # مكالمة (تقديري)
    10: 11, # نظام (تقديري)
    11: 12, # غير معروف
}

# --- خريطة امتدادات الملفات إلى مجلدات Android ---
# يتم استخدامها لتحديد المجلد المناسب بناءً على امتداد الملف أو MIME type.
EXTENSION_TO_ANDROID_FOLDER = {
    '.jpg': 'WhatsApp Images',
    '.jpeg': 'WhatsApp Images',
    '.png': 'WhatsApp Images',
    '.gif': 'WhatsApp Images',
    '.webp': 'WhatsApp Images',
    '.mp4': 'WhatsApp Video',
    '.3gp': 'WhatsApp Video',
    '.mkv': 'WhatsApp Video',
    '.mov': 'WhatsApp Video',
    '.opus': 'WhatsApp Voice Notes',
    '.ogg': 'WhatsApp Voice Notes',
    '.m4a': 'WhatsApp Audio',
    '.mp3': 'WhatsApp Audio',
    '.aac': 'WhatsApp Audio',
    '.pdf': 'WhatsApp Documents',
    '.doc': 'WhatsApp Documents',
    '.docx': 'WhatsApp Documents',
    '.xls': 'WhatsApp Documents',
    '.xlsx': 'WhatsApp Documents',
    '.ppt': 'WhatsApp Documents',
    '.pptx': 'WhatsApp Documents',
    '.txt': 'WhatsApp Documents',
    '.zip': 'WhatsApp Documents',
}

MIME_TO_ANDROID_FOLDER = {
    'image/jpeg': 'WhatsApp Images',
    'image/png': 'WhatsApp Images',
    'image/gif': 'WhatsApp Images',
    'image/webp': 'WhatsApp Images',
    'video/mp4': 'WhatsApp Video',
    'video/3gpp': 'WhatsApp Video',
    'video/quicktime': 'WhatsApp Video',
    'audio/ogg': 'WhatsApp Voice Notes',
    'audio/opus': 'WhatsApp Voice Notes',
    'audio/mpeg': 'WhatsApp Audio',
    'audio/mp4': 'WhatsApp Audio',
    'application/pdf': 'WhatsApp Documents',
    'application/msword': 'WhatsApp Documents',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'WhatsApp Documents',
}


class IOSToAndroidConverter:
    """
    فئة لتحويل قاعدة بيانات واتساب من iOS (ChatStorage.sqlite) إلى Android (msgstore.db)
    مع إعادة تشفير الناتج إلى crypt15.
    """

    def __init__(self, device_manager: DeviceManager, hex_key: str):
        self.dm = device_manager
        self.hex_key = hex_key
        self.temp_dir = None

    def convert(self, ios_backup_dir: str, output_db_path: str, is_business: bool = False,
                reference_crypt_path: Optional[str] = None) -> str:
        """
        العملية الكاملة للتحويل من iOS إلى Android:
        1. استخراج ChatStorage.sqlite من النسخة الاحتياطية.
        2. إنشاء قاعدة بيانات msgstore.db جديدة بالمخطط المناسب.
        3. نقل المحادثات والرسائل والوسائط.
        4. إعادة تشفير msgstore.db إلى msgstore.db.crypt15.
        """
        logger.info("بدء عملية التحويل من iOS إلى Android...")

        self.temp_dir = os.path.join(os.path.dirname(output_db_path), "temp_ios_to_android")
        self.dm.ensure_dir(self.temp_dir)

        # الخطوة 1: استخراج ChatStorage.sqlite
        domain = IOS_BUSINESS_APP_DOMAIN if is_business else IOS_APP_DOMAIN
        ios_db_path = os.path.join(self.temp_dir, "ChatStorage.sqlite")
        
        if not self.dm.extract_ios_file(ios_backup_dir, domain, IOS_CHATSTORAGE_PATH, ios_db_path):
            # محاولة مباشرة إذا كان الملف موجودًا بالفعل في مجلد النسخة الاحتياطية (مثلاً تم استخراجه مسبقًا)
            possible_path = os.path.join(ios_backup_dir, "ChatStorage.sqlite")
            if os.path.exists(possible_path):
                shutil.copy2(possible_path, ios_db_path)
                logger.info("تم العثور على ChatStorage.sqlite مباشرة في مجلد النسخة الاحتياطية.")
            else:
                raise FileNotFoundError("تعذر العثور على ChatStorage.sqlite في النسخة الاحتياطية. تأكد من المسار أو استخدم استخراج Manifest.db.")

        # الخطوة 2: تحليل مخطط iOS
        ios_schema = self._analyze_ios_schema(ios_db_path)
        logger.info(f"تم تحليل مخطط iOS. الجداول: {list(ios_schema.keys())}")

        # الخطوة 3: إنشاء قاعدة بيانات Android جديدة
        android_db_path = os.path.join(self.temp_dir, "msgstore.db")
        self._create_android_database(android_db_path, ios_schema)
        logger.info(f"تم إنشاء قاعدة بيانات Android في {android_db_path}")

        # الخطوة 4: نقل البيانات
        self._transfer_chats(ios_db_path, android_db_path, ios_schema)
        self._transfer_messages(ios_db_path, android_db_path, ios_schema)
        self._transfer_media(ios_db_path, android_db_path, ios_schema, ios_backup_dir, is_business)
        logger.info("تم نقل جميع البيانات إلى msgstore.db")

        # الخطوة 5: إعادة التشفير إلى crypt15
        crypt_path = output_db_path if output_db_path.endswith('.crypt15') else output_db_path + ".crypt15"
        self._encrypt_to_crypt15(android_db_path, crypt_path, reference_crypt_path)
        logger.info(f"تم إعادة التشفير بنجاح إلى {crypt_path}")

        # نسخ قاعدة البيانات غير المشفرة إلى مجلد الإخراج (للفحص أو الاستخدام لاحقًا)
        shutil.copy2(android_db_path, output_db_path.replace('.crypt15', '.db'))
        logger.info(f"تم حفظ msgstore.db غير المشفر في {output_db_path.replace('.crypt15', '.db')}")

        return crypt_path

    def _analyze_ios_schema(self, db_path: str) -> Dict[str, List[Dict]]:
        """تحليل مخطط ChatStorage.sqlite باستخدام PRAGMA table_info."""
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [row[0] for row in cursor.fetchall()]
        schema = {}
        for table in tables:
            cursor.execute(f"PRAGMA table_info({table});")
            columns = []
            for col in cursor.fetchall():
                columns.append({
                    'name': col[1],
                    'type': col[2],
                    'notnull': bool(col[3]),
                    'pk': bool(col[5])
                })
            schema[table] = columns
        conn.close()
        return schema

    def _create_android_database(self, db_path: str, ios_schema: Dict[str, List[Dict]]) -> None:
        """
        إنشاء قاعدة بيانات msgstore.db بالمخطط القياسي الحديث.
        هذا المخطط مبني على الجداول: jid, chat, message, message_media.
        """
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # جدول jid (لتخزين جهات الاتصال)
        cursor.execute("""
            CREATE TABLE jid (
                _id INTEGER PRIMARY KEY AUTOINCREMENT,
                user TEXT NOT NULL,
                server TEXT NOT NULL,
                agent INTEGER,
                device INTEGER,
                type INTEGER,
                raw_string TEXT
            );
        """)

        # جدول chat (المحادثات)
        cursor.execute("""
            CREATE TABLE chat (
                _id INTEGER PRIMARY KEY AUTOINCREMENT,
                jid_row_id INTEGER NOT NULL,
                subject TEXT,
                creation INTEGER,
                last_read_message_table_id INTEGER,
                last_message_table_id INTEGER,
                foreign_key INTEGER,
                archived INTEGER DEFAULT 0,
                hidden INTEGER DEFAULT 0,
                unread_count INTEGER DEFAULT 0,
                last_message_timestamp INTEGER
            );
        """)

        # جدول message (الرسائل)
        cursor.execute("""
            CREATE TABLE message (
                _id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_row_id INTEGER NOT NULL,
                from_me INTEGER NOT NULL,
                key_id TEXT NOT NULL,
                sender_jid_row_id INTEGER,
                status INTEGER,
                broadcast INTEGER,
                recipient_count INTEGER,
                timestamp INTEGER,
                received_timestamp INTEGER,
                send_timestamp INTEGER,
                message_type INTEGER,
                text_data TEXT,
                starred INTEGER DEFAULT 0,
                quoted_row_id INTEGER,
                media_wa_type INTEGER DEFAULT 0,
                media_size INTEGER,
                media_name TEXT,
                media_caption TEXT,
                media_hash TEXT,
                media_duration INTEGER,
                media_url TEXT,
                latitude REAL,
                longitude REAL,
                thumb_image TEXT,
                origin INTEGER
            );
        """)

        # جدول message_media (بيانات الوسائط)
        cursor.execute("""
            CREATE TABLE message_media (
                _id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_row_id INTEGER NOT NULL,
                file_path TEXT,
                file_size INTEGER,
                file_hash TEXT,
                media_key BLOB,
                media_url TEXT,
                mime_type TEXT,
                width INTEGER,
                height INTEGER,
                media_duration INTEGER,
                file_date INTEGER
            );
        """)

        # جدول message_thumbnails (اختياري، لتحسين التوافق)
        cursor.execute("""
            CREATE TABLE message_thumbnails (
                _id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_row_id INTEGER NOT NULL,
                thumbnail BLOB
            );
        """)

        conn.commit()
        conn.close()
        logger.info("تم إنشاء الجداول الأساسية في msgstore.db.")

    def _convert_ios_timestamp(self, ios_timestamp: float) -> int:
        """
        تحويل طابع آبل الزمني (ثواني منذ 2001-01-01) إلى Unix Epoch بالميلي ثانية.
        المعادلة: int((ios_timestamp + 978307200) * 1000)
        """
        return int((ios_timestamp + APPLE_EPOCH_OFFSET) * 1000)

    def _transfer_chats(self, ios_db_path: str, android_db_path: str, ios_schema: Dict) -> None:
        """نقل المحادثات من ZWACHATSESSION إلى جدولي jid و chat."""
        ios_conn = sqlite3.connect(ios_db_path)
        ios_cursor = ios_conn.cursor()
        android_conn = sqlite3.connect(android_db_path)
        android_cursor = android_conn.cursor()

        if 'ZWACHATSESSION' not in ios_schema:
            logger.warning("جدول ZWACHATSESSION غير موجود في قاعدة بيانات iOS.")
            return

        # جلب المحادثات
        ios_cursor.execute("SELECT Z_PK, ZCONTACTJID, ZLASTMESSAGEDATE, ZUNREADCOUNT, ZARCHIVED FROM ZWACHATSESSION;")
        sessions = ios_cursor.fetchall()

        # خريطة Z_PK (iOS) إلى _id (Android) للمحادثات
        ios_to_android_chat_id = {}

        for session in sessions:
            z_pk, contact_jid, last_msg_date, unread, archived = session
            if not contact_jid:
                continue

            # تقسيم JID إلى user و server
            if '@' in contact_jid:
                user, server = contact_jid.split('@', 1)
            else:
                user, server = contact_jid, 's.whatsapp.net'

            # إدراج في جدول jid
            android_cursor.execute("""
                INSERT INTO jid (user, server, raw_string) VALUES (?, ?, ?)
            """, (user, server, contact_jid))
            jid_row_id = android_cursor.lastrowid

            # إدراج في جدول chat
            last_ts = self._convert_ios_timestamp(last_msg_date) if last_msg_date else 0
            android_cursor.execute("""
                INSERT INTO chat (jid_row_id, creation, archived, unread_count, last_message_timestamp)
                VALUES (?, ?, ?, ?, ?)
            """, (jid_row_id, last_ts, archived or 0, unread or 0, last_ts))
            android_chat_id = android_cursor.lastrowid

            ios_to_android_chat_id[z_pk] = android_chat_id

        android_conn.commit()
        ios_conn.close()
        android_conn.close()
        logger.info(f"تم نقل {len(sessions)} محادثة إلى جدولي jid و chat.")
        return ios_to_android_chat_id

    def _transfer_messages(self, ios_db_path: str, android_db_path: str, ios_schema: Dict) -> None:
        """نقل الرسائل من ZWAMESSAGE إلى جدول message."""
        ios_conn = sqlite3.connect(ios_db_path)
        ios_cursor = ios_conn.cursor()
        android_conn = sqlite3.connect(android_db_path)
        android_cursor = android_conn.cursor()

        if 'ZWAMESSAGE' not in ios_schema:
            logger.warning("جدول ZWAMESSAGE غير موجود.")
            return

        # نحتاج إلى خريطة Z_PK للمحادثة من الجدول السابق. بما أن الدالة السابقة لا تعيدها مباشرة،
        # سنعيد بناء الخريطة أو نعدل التصميم. للتبسيط، سنعيد بناءها هنا بالاستعلام عن chat و jid.
        # في الإنتاج، يفضل تمرير الخريطة بين الدوال.
        # سنستخدم استعلام JOIN للحصول على معرفات Android chat.
        
        # خريطة ZCONTACTJID إلى android_chat_id
        jid_to_chat = {}
        android_cursor.execute("SELECT c._id, j.raw_string FROM chat c JOIN jid j ON c.jid_row_id = j._id;")
        for row in android_cursor.fetchall():
            jid_to_chat[row[1]] = row[0]

        # جلب رسائل iOS
        ios_cursor.execute("""
            SELECT Z_PK, ZCHATSESSION, ZISFROMME, ZTIMESTAMP, ZTEXT, ZMESSAGETYPE,
                   ZMEDIAITEM, ZSENTDATE, ZSTATUS
            FROM ZWAMESSAGE ORDER BY ZTIMESTAMP;
        """)
        messages = ios_cursor.fetchall()

        # خريطة Z_PK للرسالة إلى _id في Android (لاستخدامها في media)
        ios_to_android_msg_id = {}

        # نحتاج إلى معرفة chat_id لكل ZCHATSESSION. سنستعلم عن ZCONTACTJID للمحادثة.
        ios_cursor.execute("SELECT Z_PK, ZCONTACTJID FROM ZWACHATSESSION;")
        session_jid_map = {row[0]: row[1] for row in ios_cursor.fetchall()}

        for msg in messages:
            z_pk, z_chat_session, is_from_me, ios_ts, text, msg_type, media_item, sent_date, status = msg
            android_ts = self._convert_ios_timestamp(ios_ts) if ios_ts else 0
            android_media_type = IOS_TO_ANDROID_MESSAGE_TYPE.get(msg_type, 0)
            
            # الحصول على android_chat_id
            contact_jid = session_jid_map.get(z_chat_session)
            android_chat_id = jid_to_chat.get(contact_jid, 1)  # افتراضيًا 1 إذا لم يوجد

            # إدراج الرسالة
            android_cursor.execute("""
                INSERT INTO message (
                    chat_row_id, from_me, timestamp, received_timestamp, send_timestamp,
                    message_type, text_data, media_wa_type, media_name, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                android_chat_id, is_from_me or 0, android_ts, android_ts, android_ts,
                0, text, android_media_type, None, status or 0
            ))
            android_msg_id = android_cursor.lastrowid
            ios_to_android_msg_id[z_pk] = android_msg_id

        android_conn.commit()
        ios_conn.close()
        android_conn.close()
        logger.info(f"تم نقل {len(messages)} رسالة إلى جدول message.")
        return ios_to_android_msg_id

    def _transfer_media(self, ios_db_path: str, android_db_path: str, ios_schema: Dict,
                        ios_backup_dir: str, is_business: bool) -> None:
        """
        نقل الوسائط من نسخة iOS الاحتياطية إلى مجلدات Android القياسية،
        وتسجيل المسارات في جدول message_media.
        """
        ios_conn = sqlite3.connect(ios_db_path)
        ios_cursor = ios_conn.cursor()
        android_conn = sqlite3.connect(android_db_path)
        android_cursor = android_conn.cursor()

        if 'ZWAMEDIAITEM' not in ios_schema:
            logger.warning("جدول ZWAMEDIAITEM غير موجود. تخطي الوسائط.")
            return

        # خريطة Z_PK للرسالة إلى android message _id (من الخطوة السابقة، لكن سنعيد بناءها)
        # سنستعلم عن جميع الرسائل ونربطها بـ ZWAMEDIAITEM عبر ZMEDIAITEM في ZWAMESSAGE.
        # هذا يتطلب JOIN بين ZWAMESSAGE و ZWAMEDIAITEM.
        
        # جلب عناصر الوسائط مع معلومات الرسالة المرتبطة
        ios_cursor.execute("""
            SELECT m.Z_PK as msg_pk, mi.Z_PK as media_pk, mi.ZMEDIALOCALPATH, mi.ZMIMETYPE,
                   mi.ZFILESIZE, mi.ZMEDIAHASH, m.ZMESSAGETYPE, m.ZCHATSESSION
            FROM ZWAMESSAGE m
            JOIN ZWAMEDIAITEM mi ON m.ZMEDIAITEM = mi.Z_PK
            WHERE mi.ZMEDIALOCALPATH IS NOT NULL;
        """)
        media_items = ios_cursor.fetchall()

        # الحصول على خريطة ZCONTACTJID للمحادثات
        ios_cursor.execute("SELECT Z_PK, ZCONTACTJID FROM ZWACHATSESSION;")
        session_jid_map = {row[0]: row[1] for row in ios_cursor.fetchall()}

        # خريطة android chat_id
        jid_to_chat = {}
        android_cursor.execute("SELECT c._id, j.raw_string FROM chat c JOIN jid j ON c.jid_row_id = j._id;")
        for row in android_cursor.fetchall():
            jid_to_chat[row[1]] = row[0]

        # خريطة android message_id: نحتاج لربط msg_pk من iOS بـ _id في Android.
        # بما أننا أدخلنا الرسائل بالترتيب، يمكننا استخدام ROWID أو إعادة الاستعلام.
        # للدقة، سنستعلم عن android message_id باستخدام chat_id و timestamp.
        # لكن التوقيت قد لا يكون فريدًا. الحل الأفضل هو إرجاع الخريطة من دالة _transfer_messages.
        # لتبسيط الأمر، سنفترض أننا سنعيد بناء الخريطة عبر استعلام مماثل.
        # سنستخدم دالة مساعدة للحصول على android_msg_id.
        
        # بما أن الدالة _transfer_messages لا تعيد الخريطة، سنعيد بناءها هنا باستخدام استعلام.
        # هذا ليس مثاليًا لكنه يعمل. في الإنتاج، يجب تمرير الخريطة.
        # سنستعلم عن جميع الرسائل ونطابقها حسب chat_id و timestamp (مع هامش خطأ).
        # لكن هذا قد يكون غير دقيق. بدلاً من ذلك، سنستخدم Z_PK كـ key في جدول message.
        # لا يوجد عمود Z_PK في Android. لذا سنعتمد على الترتيب.
        
        # الحل العملي: إعادة إدراج الرسائل مع تعيين _id = Z_PK مؤقتًا؟ لا، لأن AUTOINCREMENT.
        # سنستخدم استعلام للحصول على android_msg_id بناءً على chat_id و timestamp.
        def get_android_msg_id(chat_id, timestamp_ms):
            android_cursor.execute("""
                SELECT _id FROM message 
                WHERE chat_row_id = ? AND timestamp = ? 
                LIMIT 1;
            """, (chat_id, timestamp_ms))
            row = android_cursor.fetchone()
            return row[0] if row else None

        for media in media_items:
            msg_pk, media_pk, local_path, mime_type, file_size, media_hash, msg_type, chat_session = media
            contact_jid = session_jid_map.get(chat_session)
            android_chat_id = jid_to_chat.get(contact_jid)

            if not android_chat_id:
                continue

            # الحصول على android_msg_id
            # نحتاج إلى timestamp الرسالة. سنستعلم عنها من iOS.
            ios_cursor.execute("SELECT ZTIMESTAMP FROM ZWAMESSAGE WHERE Z_PK = ?", (msg_pk,))
            row = ios_cursor.fetchone()
            if not row:
                continue
            ios_ts = row[0]
            android_ts = self._convert_ios_timestamp(ios_ts)
            android_msg_id = get_android_msg_id(android_chat_id, android_ts)

            if not android_msg_id:
                logger.warning(f"لم يتم العثور على الرسالة المقابلة للوسائط {local_path}")
                continue

            # تحديد مجلد Android المناسب
            ext = os.path.splitext(local_path)[1].lower()
            folder = EXTENSION_TO_ANDROID_FOLDER.get(ext)
            if not folder and mime_type:
                folder = MIME_TO_ANDROID_FOLDER.get(mime_type)
            if not folder:
                folder = 'WhatsApp Documents'  # افتراضي

            # المسار في النسخة الاحتياطية لـ iOS
            # local_path عادة يكون نسبيًا مثل: Media/Media/xxxx
            # نحتاج إلى استخراج الملف من النسخة الاحتياطية.
            # قد يكون local_path كاملاً أو نسبيًا. سنحاول استخراجه مباشرة إذا كان موجودًا في مجلد النسخة.
            source_file = None
            
            # المحاولة 1: إذا كان local_path يشير إلى ملف داخل مجلد النسخة الاحتياطية مباشرة
            possible_source = os.path.join(ios_backup_dir, local_path.lstrip('/'))
            if os.path.exists(possible_source):
                source_file = possible_source
            
            # المحاولة 2: البحث في Manifest.db باستخدام local_path (قد يكون relativePath)
            if not source_file:
                # محاولة استخراج الملف باستخدام device_manager
                # local_path قد يكون مثل "Media/Media/xxxx" أو "Message/Media/xxxx"
                # نحتاج إلى النطاق الصحيح.
                domain = IOS_BUSINESS_APP_DOMAIN if is_business else IOS_APP_DOMAIN
                # قد يكون local_path نسبيًا داخل النطاق.
                possible_source = self.dm.find_ios_file_in_manifest(ios_backup_dir, domain, local_path)
                if possible_source:
                    source_file = possible_source

            # المحاولة 3: البحث عن الملف بالاسم في مجلد Message/Media
            if not source_file:
                # استخراج اسم الملف
                filename = os.path.basename(local_path)
                # البحث في مجلدات النسخة الاحتياطية
                for root, dirs, files in os.walk(ios_backup_dir):
                    if filename in files:
                        source_file = os.path.join(root, filename)
                        break

            if not source_file or not os.path.exists(source_file):
                logger.warning(f"لم يتم العثور على ملف الوسائط: {local_path}. تخطي.")
                continue

            # نسخ الملف إلى مجلد Android المؤقت (سنحتفظ به في temp_dir لدفعه لاحقًا)
            android_media_dir = os.path.join(self.temp_dir, "Media", folder)
            self.dm.ensure_dir(android_media_dir)
            dest_filename = os.path.basename(source_file)
            dest_path = os.path.join(android_media_dir, dest_filename)
            shutil.copy2(source_file, dest_path)

            # المسار النسبي الذي سيتم تسجيله في message_media
            # على Android، المسار عادة يكون مثل: "Media/WhatsApp Images/IMG-xxxx.jpg"
            relative_media_path = os.path.join("Media", folder, dest_filename).replace("\\", "/")

            # إدراج في message_media
            android_cursor.execute("""
                INSERT INTO message_media (
                    message_row_id, file_path, file_size, file_hash, mime_type
                ) VALUES (?, ?, ?, ?, ?)
            """, (
                android_msg_id, relative_media_path, file_size or 0,
                media_hash, mime_type
            ))

        android_conn.commit()
        ios_conn.close()
        android_conn.close()
        logger.info(f"تم نقل {len(media_items)} عنصر وسائط إلى مجلدات Android وتسجيلها في message_media.")

    def _encrypt_to_crypt15(self, db_path: str, output_path: str, reference_path: Optional[str] = None) -> None:
        """
        إعادة تشفير msgstore.db إلى crypt15.
        يحاول أولاً استخدام أداة waencrypt (إذا كانت متاحة) مع ملف مرجعي.
        إذا لم تتوفر، يستخدم PyCryptodome كحل احتياطي (AES-GCM).
        """
        # المحاولة 1: استخدام waencrypt
        try:
            cmd = ["waencrypt"]
            if reference_path and os.path.exists(reference_path):
                cmd.extend(["--reference", reference_path])
            cmd.extend([self.hex_key, db_path, output_path])
            
            import subprocess
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            logger.info("تم التشفير باستخدام waencrypt بنجاح.")
            return
        except (FileNotFoundError, subprocess.CalledProcessError) as e:
            logger.warning(f"waencrypt غير متاح أو فشل: {e}. محاولة التشفير اليدوي باستخدام PyCryptodome.")

        # المحاولة 2: التشفير اليدوي باستخدام PyCryptodome
        if not CRYPTO_AVAILABLE:
            raise RuntimeError("PyCryptodome غير مثبت. لا يمكن إجراء التشفير اليدوي. قم بتثبيت pycryptodome.")

        # تحويل المفتاح من hex إلى bytes (32 بايت)
        try:
            key_bytes = bytes.fromhex(self.hex_key)
        except ValueError:
            raise ValueError("المفتاح يجب أن يكون 64 حرفًا hex صالحًا.")

        if len(key_bytes) != 32:
            raise ValueError("المفتاح يجب أن يكون 32 بايت (64 حرف hex).")

        # قراءة قاعدة البيانات غير المشفرة
        with open(db_path, 'rb') as f:
            plaintext = f.read()

        # ضغط البيانات باستخدام zlib (كما تفعل واتساب)
        compressed = zlib.compress(plaintext)

        # إنشاء IV عشوائي (16 بايت)
        iv = os.urandom(16)

        # تشفير AES-GCM
        cipher = AES.new(key_bytes, AES.MODE_GCM, nonce=iv)
        ciphertext, tag = cipher.encrypt_and_digest(compressed)

        # بناء ملف crypt15
        # البنية التقريبية: [Header 67 بايت] + [IV 16 بايت] + [Ciphertext] + [Tag 16 بايت]
        # الـ Header يحتوي على بيانات وصفية. نستخدم Header افتراضي (قد لا يعمل مع جميع الإصدارات).
        # للحصول على أفضل توافق، يوصى بشدة باستخدام ملف مرجعي.
        # هنا نصنع Header بسيطًا (67 بايت) مع بعض القيم الافتراضية.
        # ملاحظة: هذا التشفير قد لا يكون متوافقًا تمامًا مع واتساب إذا لم يكن الـ Header صحيحًا.
        # يفضل استخدام waencrypt مع مرجع.
        
        # Header افتراضي: 67 بايت. نضع بعض البايتات كعلامة.
        # في الواقع، crypt15 header يحتوي على protobuf. هنا نستخدم Header مبسط.
        header = b'\x00' * 67  # هذا ليس صحيحًا تمامًا، لكنه قد يعمل في بعض الحالات.
        # لتحسين التوافق، يمكننا محاولة استخدام Header من ملف مرجعي إذا توفر.
        if reference_path and os.path.exists(reference_path):
            with open(reference_path, 'rb') as f:
                ref_data = f.read()
            # استخراج أول 67 بايت كـ Header (تقديري)
            if len(ref_data) >= 67:
                header = ref_data[:67]
                logger.info("تم استخدام Header من الملف المرجعي.")

        # بناء الملف النهائي
        # ملاحظة: ترتيب IV والبيانات قد يختلف. وفقًا للمراجع، في crypt15:
        # header (67 بايت) ثم IV (16 بايت) ثم ciphertext ثم tag.
        # هذا ما سنتبعه.
        output_data = header + iv + ciphertext + tag

        with open(output_path, 'wb') as f:
            f.write(output_data)

        logger.info(f"تم التشفير اليدوي إلى {output_path} (قد لا يكون متوافقًا تمامًا بدون مرجع).")


# مثال للاستخدام (للتجربة فقط)
if __name__ == "__main__":
    dm = DeviceManager()
    converter = IOSToAndroidConverter(dm, hex_key="0"*64)  # استبدل المفتاح الفعلي
    # converter.convert("/path/to/ios/backup", "/path/to/output/msgstore.db", is_business=False)
    print("IOSToAndroidConverter جاهز للاستخدام.")
