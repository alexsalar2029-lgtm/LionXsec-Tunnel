"""Detect Windows prerequisites and install the signed Evergreen WebView2 runtime."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent
WEBVIEW2_DOWNLOAD = 'https://go.microsoft.com/fwlink/p/?LinkId=2124703'
WEBVIEW2_PAGE = 'https://developer.microsoft.com/microsoft-edge/webview2/#download'
INSTALLER_NAME = 'MicrosoftEdgeWebView2RuntimeInstallerX64.exe'
RUNTIME_KEY = r'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
NET_KEY = r'SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full'
MIN_NET_RELEASE = 394802  # .NET Framework 4.6.2
MIN_WEBVIEW2 = (86, 0, 622, 0)

SIGNATURE_CHECK = r"""
$ErrorActionPreference = 'Stop'
$signature = Get-AuthenticodeSignature -LiteralPath $env:LIONXSEC_VERIFY_INSTALLER
if ($signature.Status -ne 'Valid') {
    throw ('Installer signature is not valid: ' + $signature.Status)
}
if ($signature.SignerCertificate.Subject -notmatch '(^|,\s*)O=Microsoft Corporation(,|$)') {
    throw 'Installer is not signed by Microsoft Corporation.'
}
Write-Output 'Microsoft installer signature verified.'
"""


def _version(value):
    try:
        parts = tuple(int(part) for part in str(value).split('.'))
    except ValueError:
        return ()
    return parts if len(parts) == 4 and all(part >= 0 for part in parts) else ()


def read_windows_status(registry=None):
    if registry is None:
        import winreg as registry

    def read(hive, path, name):
        try:
            with registry.OpenKey(hive, path, 0, registry.KEY_READ) as key:
                return registry.QueryValueEx(key, name)[0]
        except FileNotFoundError:
            return None

    release = read(registry.HKEY_LOCAL_MACHINE, NET_KEY, 'Release')
    versions = [
        read(registry.HKEY_CURRENT_USER, RUNTIME_KEY, 'pv'),
        read(registry.HKEY_LOCAL_MACHINE,
             RUNTIME_KEY.replace('Software\\', 'SOFTWARE\\WOW6432Node\\', 1), 'pv'),
    ]
    versions = [value for value in versions if _version(value) >= MIN_WEBVIEW2]
    return {'net_release': release if isinstance(release, int) else None,
            'webview2_version': max(versions, key=_version) if versions else None}


def download_installer(destination):
    print('Downloading Microsoft WebView2 Evergreen Runtime (x64)...', flush=True)
    with urlopen(WEBVIEW2_DOWNLOAD, timeout=60) as response:
        if not response.geturl().lower().startswith('https://'):
            raise RuntimeError('The installer download redirected to an insecure URL.')
        with Path(destination).open('wb') as output:
            downloaded = 0
            while chunk := response.read(8 * 1024 * 1024):
                output.write(chunk)
                downloaded += len(chunk)
                print(f'Downloaded {downloaded // (1024 * 1024)} MiB...', flush=True)
    if not Path(destination).stat().st_size:
        raise RuntimeError('The downloaded installer is empty.')


def install_webview2():
    with tempfile.TemporaryDirectory(prefix='lionxsec-webview2-') as temporary:
        installer = ROOT / INSTALLER_NAME
        if installer.is_file():
            print(f'Using the local {INSTALLER_NAME}.', flush=True)
        else:
            installer = Path(temporary) / INSTALLER_NAME
            download_installer(installer)

        powershell = (Path(os.environ.get('SystemRoot', r'C:\Windows')) /
                      'System32/WindowsPowerShell/v1.0/powershell.exe')
        environment = dict(os.environ)
        environment['LIONXSEC_VERIFY_INSTALLER'] = str(installer)
        # Verify with Windows trust before executing either a downloaded or local file.
        subprocess.run([str(powershell), '-NoProfile', '-NonInteractive',
                        '-Command', SIGNATURE_CHECK], env=environment, check=True, timeout=120)
        print('Installing WebView2. Keep this window open until setup finishes...', flush=True)
        result = subprocess.run([str(installer), '/silent', '/install'], timeout=600)
        if result.returncode not in (0, 3010):
            raise RuntimeError(f'WebView2 installer exit code: {result.returncode} '
                               f'(0x{result.returncode & 0xffffffff:08X})')
        return result.returncode


def ensure_windows_runtime():
    if sys.platform != 'win32':
        return 0
    try:
        status = read_windows_status()
        print(f'.NET Framework release: {status["net_release"] or "not found"}', flush=True)
        print(f'WebView2 Runtime: {status["webview2_version"] or "not found or too old"}', flush=True)
        if (status['net_release'] or 0) < MIN_NET_RELEASE:
            print('Install or enable .NET Framework 4.6.2 or later (.NET Framework 4.8 is suitable for Server 2016).', flush=True)
            print('Official download: https://dotnet.microsoft.com/download/dotnet-framework/net48', flush=True)
            return 1
        if status['webview2_version']:
            return 0

        print('The required WebView2 Runtime will be installed from Microsoft.', flush=True)
        if install_webview2() == 3010:
            print('The installer requested a restart. Restart Windows, then run START-LIONXSEC.cmd again.', flush=True)
            return 1
        status = read_windows_status()
        if not status['webview2_version']:
            raise RuntimeError('The installer finished but WebView2 is still not registered for this Windows user.')
        print(f'WebView2 Runtime installed: {status["webview2_version"]}', flush=True)
        return 0
    except Exception as error:
        print(f'Windows prerequisite setup failed: {type(error).__name__}: {error}', flush=True)
        print('Install the Evergreen Standalone Installer (x64) manually from:', flush=True)
        print(WEBVIEW2_PAGE, flush=True)
        print(f'Or place {INSTALLER_NAME} next to START-LIONXSEC.cmd and run it again.', flush=True)
        print('Details are in startup-error.log. Do not use an unsigned installer.', flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(ensure_windows_runtime())
