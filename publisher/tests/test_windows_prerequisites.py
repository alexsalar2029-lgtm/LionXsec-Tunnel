"""Exercise Windows prerequisite decisions without downloading or running an EXE."""
import contextlib
import hashlib
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import setup_desktop
import windows_prerequisites as prerequisites


MISSING_RUNTIME = {'net_release': 394806, 'webview2_version': None}
READY = {'net_release': 394806, 'webview2_version': '130.0.2849.80'}


class Registry:
    HKEY_CURRENT_USER = 'user'
    HKEY_LOCAL_MACHINE = 'machine'
    KEY_READ = 0x20019

    def __init__(self, values):
        self.values = values

    @contextlib.contextmanager
    def OpenKey(self, hive, path, reserved, access):
        yield (hive, path)

    def QueryValueEx(self, key, name):
        try:
            return self.values[(*key, name)], 1
        except KeyError:
            raise FileNotFoundError from None


class WindowsPrerequisiteTests(unittest.TestCase):
    def run_ensure(self, statuses, install_code=0):
        with patch.object(sys, 'platform', 'win32'), \
             patch.object(prerequisites, 'read_windows_status', side_effect=statuses) as read, \
             patch.object(prerequisites, 'install_webview2', return_value=install_code) as install, \
             patch('sys.stdout', new=io.StringIO()) as output:
            result = prerequisites.ensure_windows_runtime()
        return result, read, install, output.getvalue()

    def test_registry_detects_per_user_runtime(self):
        registry = Registry({('machine', prerequisites.NET_KEY, 'Release'): 394806,
                             ('user', prerequisites.RUNTIME_KEY, 'pv'): '130.0.2849.80'})
        self.assertEqual(prerequisites.read_windows_status(registry), READY)

    def test_registry_detects_machine_runtime_in_32_bit_registry_location(self):
        path = r'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
        registry = Registry({('machine', prerequisites.NET_KEY, 'Release'): 394806,
                             ('machine', path, 'pv'): '130.0.2849.80'})
        self.assertEqual(prerequisites.read_windows_status(registry), READY)

    def test_zero_malformed_and_outdated_versions_require_installation(self):
        for version in ('0.0.0.0', '', 'bad', '85.0.0.0', '86.0.621.0'):
            with self.subTest(version=version):
                registry = Registry({('user', prerequisites.RUNTIME_KEY, 'pv'): version})
                self.assertIsNone(prerequisites.read_windows_status(registry)['webview2_version'])

    def test_existing_runtime_is_not_reinstalled(self):
        result, read, install, _ = self.run_ensure([READY])
        self.assertEqual(result, 0)
        read.assert_called_once()
        install.assert_not_called()

    def test_missing_dotnet_is_reported_separately_without_installing_webview(self):
        for release in (None, 394254):
            with self.subTest(release=release):
                result, _, install, output = self.run_ensure([
                    {'net_release': release, 'webview2_version': READY['webview2_version']}])
                self.assertEqual(result, 1)
                install.assert_not_called()
                self.assertIn('Install or enable .NET Framework', output)

    def test_missing_webview_is_installed_and_registration_rechecked(self):
        result, read, install, output = self.run_ensure([MISSING_RUNTIME, READY])
        self.assertEqual(result, 0)
        self.assertEqual(read.call_count, 2)
        install.assert_called_once()
        self.assertIn('WebView2 Runtime installed: 130.0.2849.80', output)

    def test_installer_success_without_registration_does_not_claim_readiness(self):
        result, _, _, output = self.run_ensure([MISSING_RUNTIME, MISSING_RUNTIME])
        self.assertEqual(result, 1)
        self.assertIn('still not registered', output)

    def test_restart_request_stops_setup_without_rebooting(self):
        result, read, _, output = self.run_ensure([MISSING_RUNTIME], install_code=3010)
        self.assertEqual(result, 1)
        read.assert_called_once()
        self.assertIn('Restart Windows', output)

    def test_registry_access_error_does_not_trigger_download(self):
        result, _, install, output = self.run_ensure([PermissionError('registry denied')])
        self.assertEqual(result, 1)
        install.assert_not_called()
        self.assertIn('PermissionError', output)

    def test_non_windows_setup_never_downloads_or_reads_registry(self):
        with patch.object(sys, 'platform', 'linux'), \
             patch.object(prerequisites, 'read_windows_status') as read, \
             patch.object(prerequisites, 'install_webview2') as install:
            self.assertEqual(prerequisites.ensure_windows_runtime(), 0)
        read.assert_not_called()
        install.assert_not_called()

    def test_invalid_signature_prevents_installer_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            installer = Path(directory) / prerequisites.INSTALLER_NAME
            installer.write_bytes(b'untrusted test fixture')
            with patch.object(prerequisites, 'ROOT', Path(directory)), \
                 patch.object(prerequisites, 'download_installer') as download, \
                 patch.object(prerequisites.subprocess, 'run',
                              side_effect=subprocess.CalledProcessError(1, 'signature check')) as run, \
                 patch('sys.stdout', new=io.StringIO()):
                with self.assertRaises(subprocess.CalledProcessError):
                    prerequisites.install_webview2()
            download.assert_not_called()
            self.assertEqual(run.call_count, 1)
            self.assertIn('Get-AuthenticodeSignature', run.call_args.args[0][-1])
            self.assertTrue(run.call_args.kwargs['check'])

    def test_signed_local_installer_runs_after_verification_without_download(self):
        with tempfile.TemporaryDirectory() as directory:
            installer = Path(directory) / prerequisites.INSTALLER_NAME
            installer.write_bytes(b'mocked installer fixture')
            with patch.object(prerequisites, 'ROOT', Path(directory)), \
                 patch.object(prerequisites, 'download_installer') as download, \
                 patch.object(prerequisites.subprocess, 'run', return_value=Mock(returncode=0)) as run, \
                 patch('sys.stdout', new=io.StringIO()):
                self.assertEqual(prerequisites.install_webview2(), 0)
            download.assert_not_called()
            self.assertEqual(run.call_count, 2)
            self.assertEqual(run.call_args_list[0].kwargs['env']['LIONXSEC_VERIFY_INSTALLER'], str(installer))
            self.assertEqual(run.call_args.args[0], [str(installer), '/silent', '/install'])
            self.assertTrue(installer.exists())

    def test_failed_installer_exit_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            installer = Path(directory) / prerequisites.INSTALLER_NAME
            installer.write_bytes(b'mocked installer fixture')
            with patch.object(prerequisites, 'ROOT', Path(directory)), \
                 patch.object(prerequisites.subprocess, 'run',
                              side_effect=[Mock(returncode=0), Mock(returncode=1603)]), \
                 patch('sys.stdout', new=io.StringIO()):
                with self.assertRaisesRegex(RuntimeError, '1603'):
                    prerequisites.install_webview2()

    def test_renderer_probe_runs_only_after_prerequisites_succeed_on_cached_setup(self):
        for prerequisites_result in (0, 1):
            with self.subTest(result=prerequisites_result), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'requirements.txt').write_text('example==1.0\n')
                environment = root / '.venv'
                (environment / 'Scripts').mkdir(parents=True)
                (environment / 'Scripts/python.exe').touch()
                (environment / '.requirements-sha256').write_text(
                    hashlib.sha256((root / 'requirements.txt').read_bytes()).hexdigest())
                with patch.object(setup_desktop, 'ROOT', root), \
                     patch.object(sys, 'platform', 'win32'), \
                     patch.object(sys, 'getwindowsversion', create=True, return_value=Mock(major=10)), \
                     patch.object(setup_desktop, 'ensure_windows_runtime', return_value=prerequisites_result) as ensure, \
                     patch.object(setup_desktop.subprocess, 'run', return_value=Mock(returncode=0)) as run, \
                     patch.object(setup_desktop, 'pip_install') as pip, \
                     patch('sys.stdout', new=io.StringIO()):
                    self.assertEqual(setup_desktop.main(), prerequisites_result)
                ensure.assert_called_once()
                pip.assert_not_called()
                self.assertEqual(run.call_count, 1 if prerequisites_result else 2)


if __name__ == '__main__':
    unittest.main()
