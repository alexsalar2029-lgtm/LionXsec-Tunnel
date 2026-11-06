# Third-party components

This source distribution includes the Sora and Vazirmatn font files. Their SIL Open Font License notices are included beside the fonts in `ui/assets`.

The launcher installs dependencies from their published Python packages into a local virtual environment. They are separate dependencies; their binaries are not bundled in this ZIP. Preserve the license and attribution files supplied with those packages if redistributing an installed environment or a frozen executable.

- **PySide6 / Shiboken6 / Qt**: Qt for Python, with open-source LGPLv3/GPLv3 and commercial licensing options. See the installed package's license files and [Qt for Python licensing](https://doc.qt.io/qtforpython-6/licenses.html). Qt WebEngine includes Chromium and its third-party notices. Source: https://code.qt.io/cgit/pyside/pyside-setup.git/ and https://code.qt.io/cgit/qt/qtwebengine.git/.
- **QtPy**: MIT; https://github.com/spyder-ide/qtpy.
- **pywebview**: BSD-3-Clause; https://github.com/r0x0r/pywebview.
- **pythonnet**: MIT; https://github.com/pythonnet/pythonnet. Used on Windows with .NET Framework.
- **Microsoft Edge WebView2 Runtime**: installed separately on Windows from Microsoft's official download page, subject to Microsoft's license terms; https://developer.microsoft.com/microsoft-edge/webview2/. The runtime is not included in this ZIP. Qt/PySide6 are installed only on non-Windows platforms in this compatibility patch.
- **Telethon**: MIT; https://github.com/LonamiWebs/Telethon. Used for the MTProto user-account connection. Its optional accelerator **cryptg** (CC0 1.0; https://github.com/cher-nov/cryptg) and its pure-Python fallbacks **pyaes** (MIT) and **rsa** (Apache-2.0) are installed with it.
- **Requests**: Apache-2.0; see its installed package notices.
- **Pillow**: HPND and third-party image-library notices; https://python-pillow.org/.
- **imageio-ffmpeg / FFmpeg**: wrapper and executable have separate licenses. Consult the installed distribution and the executable's license configuration before redistributing FFmpeg.
- **pypdf, jdatetime and tzdata**: consult their installed package license files.

The project accesses these libraries through normal Python imports; the installed libraries remain separate and replaceable. This file is an index of notices and does not replace any dependency's license terms.
