import contextlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import main_engine
from device_manager import DeviceManager
from ios_to_android_converter import IOSToAndroidConverter


class EngineSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / 'source.db'
        with sqlite3.connect(self.db) as conn:
            conn.execute('CREATE TABLE sentinel (value TEXT)')
        self.media = self.root / 'media'
        self.media.mkdir()
        self.backup = self.root / 'backup'
        self.backup.mkdir()
        (self.backup / 'Manifest.db').write_bytes(b'original backup, never modify')
        self.output = self.root / 'output'
        self.output.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def test_offline_engine_does_not_probe_device_tools(self):
        with patch('device_manager.subprocess.run', side_effect=AssertionError('offline')):
            main_engine.MigrationEngine('')

    def test_end_to_end_candidate_preserves_source_and_backup(self):
        import hashlib
        with sqlite3.connect(self.db) as conn:
            conn.executescript('''
                CREATE TABLE chat (_id INTEGER PRIMARY KEY, jid TEXT);
                CREATE TABLE message (_id INTEGER PRIMARY KEY, chat_row_id INTEGER,
                    from_me INTEGER, timestamp INTEGER, text_data TEXT, message_type INTEGER);
                CREATE TABLE message_media (message_row_id INTEGER, file_path TEXT);
                INSERT INTO chat VALUES(42, 'test@s.whatsapp.net');
                INSERT INTO message VALUES(900,42,0,1700000000000,'مرحبا',0);
                INSERT INTO message VALUES(999,42,1,1700000001000,NULL,2);
                INSERT INTO message_media VALUES(999,'Media/WhatsApp Voice Notes/week/voice.opus');
            ''')
        voice = self.media / 'WhatsApp Voice Notes' / 'week' / 'voice.opus'
        voice.parent.mkdir(parents=True)
        voice.write_bytes(b'synthetic-audio-test-payload')
        before_db = hashlib.sha256(self.db.read_bytes()).digest()
        before_backup = (self.backup / 'Manifest.db').read_bytes()
        with contextlib.redirect_stdout(io.StringIO()) as capture:
            code = main_engine.main(['--direction', 'android_to_ios', '--android-db', str(self.db),
                                     '--android-media', str(self.media), '--ios-backup', str(self.backup),
                                     '--output-dir', str(self.output)])
        self.assertEqual(code, 0, capture.getvalue())
        event = json.loads(capture.getvalue().splitlines()[-1].removeprefix(main_engine.ProgressEmitter.PREFIX))
        self.assertEqual(event['type'], 'candidate')
        self.assertEqual(event['counts'], {'chats': 1, 'messages': 2, 'media': 1})
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).digest(), before_db)
        self.assertEqual((self.backup / 'Manifest.db').read_bytes(), before_backup)
        with sqlite3.connect(event['output_db']) as conn:
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])
            self.assertEqual(conn.execute('SELECT ZCHATSESSION FROM ZWAMESSAGE').fetchall(), [(1,), (1,)])
            self.assertEqual(conn.execute('SELECT ZTEXT FROM ZWAMESSAGE WHERE ZANDROIDID=900').fetchone()[0], 'مرحبا')
        copied = Path(event['output_db']).parent / 'Media' / 'WhatsApp Voice Notes' / 'week' / 'voice.opus'
        self.assertEqual(copied.read_bytes(), voice.read_bytes())

    def test_candidate_is_not_success_and_backup_untouched(self):
        before = (self.backup / 'Manifest.db').read_bytes()
        candidate = self.output / 'ChatStorage.sqlite'
        candidate.write_bytes(b'candidate')
        with patch.object(main_engine.AndroidToIOSConverter, 'convert', return_value=str(candidate)), \
                contextlib.redirect_stdout(io.StringIO()) as capture:
            report = main_engine.MigrationEngine('').run_android_to_ios(
                str(self.db), str(self.media), str(self.backup), False, str(self.output))
        events = [json.loads(line.removeprefix(main_engine.ProgressEmitter.PREFIX))
                  for line in capture.getvalue().splitlines()]
        self.assertNotIn('success', [event['type'] for event in events])
        self.assertEqual(events[-1]['type'], 'candidate')
        self.assertFalse(report['backup_modified'])
        self.assertEqual((self.backup / 'Manifest.db').read_bytes(), before)
        self.assertTrue(Path(report['output_db']).is_file())
        self.assertEqual(json.loads((self.output / 'migration_report.json').read_text())['device_compatibility'], 'unverified')

    def test_reject_output_inside_backup(self):
        with self.assertRaises(ValueError):
            main_engine.MigrationEngine('').run_android_to_ios(
                str(self.db), str(self.media), str(self.backup), False, str(self.backup / 'out'))

    def test_reject_output_containing_input_db(self):
        with self.assertRaises(ValueError):
            main_engine.MigrationEngine('').run_android_to_ios(
                str(self.db), str(self.media), str(self.backup), False, str(self.root))

    def test_reject_output_inside_media(self):
        with self.assertRaises(ValueError):
            main_engine.MigrationEngine('').run_android_to_ios(
                str(self.db), str(self.media), str(self.backup), False, str(self.media / 'out'))

    def test_unrelated_output_files_are_never_deleted(self):
        marker = self.output / 'important.txt'
        marker.write_text('keep this file')
        with contextlib.redirect_stdout(io.StringIO()):
            code = main_engine.main(['--direction', 'android_to_ios', '--android-db', str(self.db),
                                     '--android-media', str(self.media), '--ios-backup', str(self.backup),
                                     '--output-dir', str(self.output)])
        self.assertEqual(code, 1)
        self.assertEqual(marker.read_text(), 'keep this file')

    def test_invalid_hex_is_rejected_without_echo(self):
        secret = 'z' * 64
        with contextlib.redirect_stdout(io.StringIO()) as capture:
            code = main_engine.main(['--direction', 'android_to_ios', '--hex-key', secret])
        self.assertEqual(code, 2)
        self.assertNotIn(secret, capture.getvalue())

    def test_reverse_direction_is_blocked(self):
        with contextlib.redirect_stdout(io.StringIO()) as capture:
            code = main_engine.main(['--direction', 'ios_to_android'])
        self.assertEqual(code, 1)
        self.assertNotIn('"type": "success"', capture.getvalue())

    def test_push_is_blocked(self):
        with contextlib.redirect_stdout(io.StringIO()):
            code = main_engine.main(['--direction', 'android_to_ios', '--push-to-device'])
        self.assertEqual(code, 1)

    def test_plaintext_input_does_not_require_key(self):
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(main_engine.MigrationEngine, 'run_android_to_ios') as convert, \
                contextlib.redirect_stdout(io.StringIO()):
            code = main_engine.main(['--direction', 'android_to_ios', '--android-db', str(self.db),
                                     '--android-media', str(self.media), '--ios-backup', str(self.backup)])
        self.assertEqual(code, 0)
        convert.assert_called_once()

    def test_environment_key_and_error_redaction(self):
        secret = 'ab' * 32
        with patch.dict(os.environ, {main_engine.KEY_ENV: secret}), \
                patch.object(main_engine.MigrationEngine, 'run_android_to_ios', side_effect=RuntimeError(secret)), \
                contextlib.redirect_stdout(io.StringIO()) as capture:
            code = main_engine.main(['--direction', 'android_to_ios', '--android-db', str(self.db),
                                     '--android-media', str(self.media), '--ios-backup', str(self.backup)])
        self.assertEqual(code, 1)
        self.assertNotIn(secret, capture.getvalue())
        self.assertIn('[REDACTED]', capture.getvalue())

    def test_crypto_failure_never_echoes_command_key(self):
        secret = 'ab' * 32
        failure = subprocess.CalledProcessError(1, ['wadecrypt', secret], stderr=secret)
        with patch('device_manager.subprocess.run', side_effect=failure):
            with self.assertRaises(RuntimeError) as caught:
                DeviceManager(check_tools=False).decrypt_crypt15(str(self.db), str(self.output / 'db'), secret)
        self.assertNotIn(secret, str(caught.exception))
        self.assertTrue(caught.exception.__suppress_context__)

    def test_encrypt_requires_reference_and_never_fakes_header(self):
        dm = DeviceManager(check_tools=False)
        output = self.output / 'fake.crypt15'
        with self.assertRaises(ValueError):
            dm.encrypt_crypt15(str(self.db), str(output), 'ab' * 32)
        self.assertFalse(output.exists())

    def test_reverse_encrypt_delegates_without_fallback(self):
        dm = DeviceManager(check_tools=False)
        converter = IOSToAndroidConverter(dm, 'ab' * 32)
        with patch.object(dm, 'encrypt_crypt15', side_effect=RuntimeError('tool failed')) as encrypt:
            with self.assertRaises(RuntimeError):
                converter._encrypt_to_crypt15(str(self.db), str(self.output / 'file'), 'reference')
        encrypt.assert_called_once()
        self.assertFalse((self.output / 'file').exists())

    def test_crypt_requires_key(self):
        with patch.dict(os.environ, {}, clear=True), contextlib.redirect_stdout(io.StringIO()):
            code = main_engine.main(['--direction', 'android_to_ios', '--android-db', 'source.crypt15'])
        self.assertEqual(code, 2)


if __name__ == '__main__':
    unittest.main()
