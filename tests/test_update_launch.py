import os
import sys
import unittest
from unittest.mock import patch
from media_categorizer.update_launch import independent_environment, installer_command

class UpdateLaunchTests(unittest.TestCase):
    def test_independent_environment_preserves_user_settings_and_resets_frozen_runtime(self):
        env = {'APPDATA':'settings', 'PATH':'system', '_PYI_ARCHIVE_FILE':'old.exe',
               '_PYI_PARENT_PROCESS_LEVEL':'1', '_PYI_APPLICATION_HOME_DIR':'old-temp', '_MEIPASS2':'old-temp'}
        with patch.dict(os.environ, env, clear=True):
            actual = independent_environment()
            self.assertEqual(actual, {'APPDATA':'settings','PATH':'system','PYINSTALLER_RESET_ENVIRONMENT':'1'})
            self.assertIn('_PYI_ARCHIVE_FILE', os.environ)

    def test_frozen_handoff_waits_for_both_processes(self):
        with patch.object(sys, 'frozen', True, create=True), patch('os.getpid', return_value=100), patch('os.getppid', return_value=99):
            command = installer_command("C:/update's.exe")
        self.assertIn('Wait-Process -Id 100,99 ', command)
        self.assertIn("update''s.exe", command)
        self.assertIn('/RESTARTAPP=1', command)
