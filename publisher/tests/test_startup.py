"""Startup regressions; Windows selection is simulated without a live account."""
import builtins
import io
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import app
import desktop_runtime
import launcher


class StartupTests(unittest.TestCase):
    def test_windows_initializes_webview2_without_importing_qt(self):
        guilib = ModuleType('webview.guilib')
        guilib.initialize = Mock(return_value=SimpleNamespace(renderer='edgechromium'))
        original_import = builtins.__import__

        def guarded_import(name, *args, **kwargs):
            if name.split('.')[0] in ('PySide6', 'qtpy', 'shiboken6'):
                raise AssertionError('Windows startup must not load Qt')
            return original_import(name, *args, **kwargs)

        with patch.object(sys, 'platform', 'win32'), \
             patch.dict(sys.modules, {'webview.guilib': guilib}), \
             patch('builtins.__import__', side_effect=guarded_import):
            backend = desktop_runtime.initialize_desktop()
        self.assertEqual(backend.renderer, 'edgechromium')
        guilib.initialize.assert_called_once_with('edgechromium')

    def test_windows_rejects_legacy_fallback(self):
        initialize = Mock(return_value=SimpleNamespace(renderer='mshtml'))
        with patch.object(sys, 'platform', 'win32'):
            with self.assertRaisesRegex(RuntimeError, 'desktop_renderer_unavailable'):
                desktop_runtime.initialize_desktop(initialize)
        initialize.assert_called_once_with('edgechromium')

    def test_software_mode_preserves_flags_for_both_renderers(self):
        original = {'QTWEBENGINE_CHROMIUM_FLAGS': '--example',
                    'WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS': '--existing --disable-gpu'}
        with patch.dict('os.environ', original, clear=True):
            result = desktop_runtime.software_environment()
            self.assertEqual(dict(desktop_runtime.os.environ), original)
        self.assertEqual(result['QTWEBENGINE_CHROMIUM_FLAGS'], '--example --disable-gpu')
        self.assertEqual(result['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS'], '--existing --disable-gpu')
        self.assertEqual(result['LIONXSEC_SOFTWARE_RETRY'], '1')

    def test_import_failure_is_logged_before_application_can_start(self):
        log = io.StringIO()
        with patch('sys.stdout', new=io.StringIO()):
            code = launcher.run_logged([sys.executable, '-c',
                "raise ModuleNotFoundError('missing startup dependency')"], log)
        self.assertNotEqual(code, 0)
        self.assertIn('ModuleNotFoundError: missing startup dependency', log.getvalue())
        self.assertIn('Process exit code: 1', log.getvalue())

    def test_native_exit_code_is_logged_even_without_python_traceback(self):
        log = io.StringIO()
        with patch('sys.stdout', new=io.StringIO()):
            code = launcher.run_logged([sys.executable, '-c', 'import os; os._exit(42)'], log)
        self.assertEqual(code, 42)
        self.assertIn('0x0000002A', log.getvalue())

    def test_setup_failure_does_not_launch_app(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(launcher, 'ROOT', root), \
                 patch.object(launcher, 'LOG_FILE', root / 'startup-error.log'), \
                 patch.object(launcher, 'run_logged', return_value=1) as run, \
                 patch('sys.stdout', new=io.StringIO()):
                self.assertEqual(launcher.main(), 1)
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.args[0][-1], str(root / 'setup_desktop.py'))
            self.assertIn('Python:', (root / 'startup-error.log').read_text())

    def test_launcher_uses_local_environment_after_successful_setup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(launcher, 'ROOT', root), \
                 patch.object(launcher, 'LOG_FILE', root / 'startup-error.log'), \
                 patch.object(launcher, 'run_logged', return_value=0) as run, \
                 patch.object(sys, 'platform', 'win32'):
                self.assertEqual(launcher.main(), 0)
            self.assertEqual(run.call_count, 2)
            self.assertEqual(run.call_args.args[0],
                             [str(root / '.venv' / 'Scripts/python.exe'), '-u', str(root / 'app.py')])

    def test_windows_app_passes_selected_renderer_to_window_start(self):
        webview = ModuleType('webview')
        webview.settings = {}
        window = SimpleNamespace(events=SimpleNamespace(closed=[]))
        # The event hook implements += callback in pywebview.
        class ClosedEvent:
            def __iadd__(self, callback):
                return self
        window.events.closed = ClosedEvent()
        webview.create_window = Mock(return_value=window)
        webview.start = Mock()
        with tempfile.TemporaryDirectory() as directory:
            store = Mock(directory=Path(directory))
            with patch.dict(sys.modules, {'webview': webview}), \
                 patch.object(sys, 'platform', 'win32'), \
                 patch.object(app, 'initialize_desktop'), \
                 patch.object(app, 'Store', return_value=store), \
                 patch.object(app, 'SingleInstance'), patch.object(app, 'Engine'):
                self.assertEqual(app.main(), 0)
        self.assertEqual(webview.start.call_args.kwargs['gui'], 'edgechromium')


if __name__ == '__main__':
    unittest.main()
