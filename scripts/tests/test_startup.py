import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('startup', Path(__file__).parents[1] / 'start-local.py')
startup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(startup)

class StartupTests(unittest.TestCase):
    def test_dead_process_fails_without_reporting_ready(self):
        with self.assertRaisesRegex(RuntimeError, '启动失败'):
            startup.wait('unused', Mock(poll=lambda: 1), lambda data: True)

    def test_wrong_service_cannot_pass_readiness(self):
        with patch.object(startup, 'read', return_value={'data': {'service': 'another-app'}}), patch.object(startup.time, 'sleep'):
            with self.assertRaisesRegex(RuntimeError, '未在'):
                startup.wait('unused', Mock(poll=lambda: None), lambda data: data['data']['service'] == 'site-inspection')

    def test_transient_connection_failure_then_ready(self):
        with patch.object(startup, 'read', side_effect=[OSError('not yet'), {'ready': True}]), patch.object(startup.time, 'sleep'):
            startup.wait('unused', Mock(poll=lambda: None), lambda data: data.get('ready'))

if __name__ == '__main__':
    unittest.main()
