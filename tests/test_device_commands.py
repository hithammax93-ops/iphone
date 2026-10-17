import contextlib
import io
import json
import unittest
from unittest.mock import patch

import main_engine


class DeviceCommandTests(unittest.TestCase):
    def invoke(self, args):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            code = main_engine.main(args)
        events = [json.loads(line[len(main_engine.ProgressEmitter.PREFIX):])
                  for line in output.getvalue().splitlines()
                  if line.startswith(main_engine.ProgressEmitter.PREFIX)]
        return code, events

    def test_check_emits_device_report_not_migration_success(self):
        with patch('android_backup.AndroidBackup.check_devices', return_value={
                'adb_available': True, 'devices': [{'serial': 'TEST', 'state': 'unauthorized'}]}):
            code, events = self.invoke(['--direction', 'check_devices'])
        self.assertEqual(code, 0)
        self.assertEqual(events[-1]['type'], 'devices')
        self.assertEqual(events[-1]['devices'][0]['state'], 'unauthorized')
        self.assertNotIn('success', [event['type'] for event in events])

    def test_backup_dispatch_and_honest_terminal_event(self):
        report = {'backup_dir': '/test', 'manifest': '/test/backup_manifest.json',
                  'android_db': None, 'android_media': '/test/Media',
                  'status': 'success', 'conversion_ready': False}
        with patch('android_backup.AndroidBackup.backup', return_value=report) as backup:
            code, events = self.invoke(['--direction', 'backup_android', '--android-serial', 'TEST',
                                        '--backup-dir', '/copies', '--whatsapp-type', 'business'])
        self.assertEqual(code, 0)
        backup.assert_called_once_with('TEST', '/copies', True)
        self.assertEqual(events[-1]['type'], 'backup')
        self.assertFalse(events[-1]['conversion_ready'])
        self.assertNotIn('success', [event['type'] for event in events])

    def test_backup_streams_progress_before_terminal_result(self):
        def backup(self, *args):
            self.on_progress({'stage': 'pull', 'message': 'نسخ Media: 2 MiB', 'bytes': 2097152})
            return {'status': 'failed', 'conversion_ready': False, 'errors': ['USB disconnected']}

        with patch('android_backup.AndroidBackup.backup', backup):
            code, events = self.invoke(['--direction', 'backup_android', '--android-serial', 'TEST',
                                        '--backup-dir', '/copies'])
        self.assertEqual(code, 1)
        progress = next(e for e in events if e['type'] == 'backup_progress')
        self.assertEqual(progress['bytes'], 2097152)
        self.assertIn('نسخ Media', progress['message'])
        self.assertEqual(events[-1]['type'], 'backup')
        self.assertEqual(events[-1]['status'], 'failed')

    def test_backup_requires_explicit_serial_and_destination(self):
        with patch('android_backup.AndroidBackup.backup') as backup:
            code, events = self.invoke(['--direction', 'backup_android'])
        self.assertEqual(code, 1)
        self.assertEqual(events[-1]['type'], 'error')
        backup.assert_not_called()

    def test_push_remains_blocked_even_with_device_check(self):
        with patch('android_backup.AndroidBackup.check_devices') as check:
            code, events = self.invoke(['--direction', 'check_devices', '--push-to-device'])
        self.assertEqual(code, 1)
        check.assert_not_called()

    def test_failed_pull_is_error_not_backup_event(self):
        with patch('android_backup.AndroidBackup.backup', side_effect=RuntimeError('انقطع الهاتف')):
            code, events = self.invoke(['--direction', 'backup_android', '--android-serial', 'TEST',
                                        '--backup-dir', '/copies'])
        self.assertEqual(code, 1)
        self.assertEqual(events[-1]['type'], 'error')
        self.assertNotIn('backup', [event['type'] for event in events])

    def test_failed_report_has_nonzero_exit_and_no_completion_claim(self):
        with patch('android_backup.AndroidBackup.backup', return_value={
                'status': 'failed', 'conversion_ready': False, 'errors': ['disconnected']}):
            code, events = self.invoke(['--direction', 'backup_android', '--android-serial', 'TEST',
                                        '--backup-dir', '/copies'])
        self.assertEqual(code, 1)
        self.assertEqual(events[-1]['status'], 'failed')
        self.assertIn('disconnected', events[-1]['message'])
        self.assertNotIn('اكتمل', events[-1]['message'])

    def test_missing_adb_report_has_nonzero_exit(self):
        with patch('android_backup.AndroidBackup.check_devices', return_value={
                'adb_available': False, 'devices': [], 'error': 'ADB unavailable'}):
            code, events = self.invoke(['--direction', 'check_devices'])
        self.assertEqual(code, 1)
        self.assertEqual(events[-1]['message'], 'ADB unavailable')


if __name__ == '__main__':
    unittest.main()
