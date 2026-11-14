"""Exercise the full single-cell notebook launcher in a fresh namespace."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


class StartupTests(unittest.TestCase):
    def test_launcher_displays_app_with_shared_debrid_selector(self):
        source = Path(__file__).with_name('ultimate_downloader.py').read_text(encoding='utf-8')
        namespace = {'__name__': '__main__'}
        with patch.dict(sys.modules, {'google.colab': None}), patch('IPython.display.display') as display:
            exec(source, namespace)

        app = namespace['input_ui']
        selector = namespace['debrid_service_toggle']
        display.assert_called_once_with(app)
        self.assertIn(selector, app.children[1].children)
        self.assertIn(selector, namespace['debrid_row'].children)
        self.assertEqual(selector.options, ('None', 'Real-Debrid', 'TorBox'))
        self.assertEqual(selector.value, 'None')
