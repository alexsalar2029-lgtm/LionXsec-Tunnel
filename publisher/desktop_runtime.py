"""Use WebView2 on Windows and Qt WebEngine elsewhere; reject MSHTML."""
import os
import sys


def desktop_gui():
    # Qt 6.10 targets Windows 10 1809+, excluding Server 2016 (build 14393).
    # Microsoft's WebView2 Runtime supports Server 2016 Desktop Experience.
    return 'edgechromium' if sys.platform == 'win32' else 'qt'


def software_environment():
    environment = dict(os.environ)
    environment['LIONXSEC_SOFTWARE_RETRY'] = '1'
    for name in ('QTWEBENGINE_CHROMIUM_FLAGS', 'WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS'):
        flags = environment.get(name, '')
        if '--disable-gpu' not in flags.split():
            environment[name] = (flags + ' --disable-gpu').strip()
    return environment


def initialize_desktop(initialize=None):
    gui = desktop_gui()
    if gui == 'qt':
        os.environ['QT_API']='pyside6'
    if initialize is None:
        if gui == 'qt':
            from PySide6 import QtWebEngineWidgets  # noqa: F401
            import qtpy
            if qtpy.API_NAME!='PySide6': raise RuntimeError('desktop_renderer_unavailable')
        from webview.guilib import initialize
    backend=initialize(gui)
    expected = 'edgechromium' if gui == 'edgechromium' else 'qtwebengine'
    if getattr(backend,'renderer',None)!=expected:
        raise RuntimeError('desktop_renderer_unavailable')
    return backend
