# 1.4.1 validation

102 unit/integration tests passed on Linux; JavaScript syntax passed. New tests exercise the real MTProto wrapper against an async client matching Telethon's None-returning connect() contract, verify code-request progression, reconnect, disconnected-state rejection and retry after network failure. Network error mappings are secret-free. The installed Telethon 1.45.0 source confirms connect() returns None and exposes is_connected(). Live Telegram connectivity, code delivery and Windows Server 2016 execution remain untested. Historical results follow.

# 1.4.0 validation

97 unit/integration tests passed on Linux, including streamed TXT branding for UTF-8/UTF-16/CP1256, long-line link boundaries, source preservation, metadata-only polling, one failed API call per scan and sent-history protection. Browser UI, real Qt WebEngine desktop bridge and real FFmpeg tests passed. OpenAI and Telegram are simulated; Windows Server 2016 and live delivery were not tested. Historical results follow.

# 1.3.3 validation

92 unit/integration tests passed on Linux. Browser smoke passed, including disabling/enabling the content restriction checkbox through the real UI and verifying persisted settings. Media smoke passed with real FFmpeg. JavaScript syntax passed. Remote OpenAI and Telegram calls are simulated; Windows Server 2016 execution and live API delivery were not tested. Historical results follow.

# Validation — LIONXSEC Publisher Studio 1.3.0

## 1.3.0 validation

Validated on Debian Linux with Python 3.12. Production dependencies remain in `requirements.txt`; `requirements-dev.txt` adds Playwright for the browser check.

```sh
python -m pip install -r requirements-dev.txt
python -m pip check
python -m unittest discover -s tests -p 'test_*.py' -q
python tests/media_smoke.py
node --check ui/app.js
XDG_CACHE_HOME=/workspace/.cache python tests/browser_smoke.py
XDG_CACHE_HOME=/workspace/.cache QT_QPA_PLATFORM=offscreen QTWEBENGINE_CHROMIUM_FLAGS=--disable-gpu python tests/native_smoke.py
```

- **89 unit/integration tests passed.** The 25 added tests cover explicit versus background rescans, unchanged TXT reanalysis, corrected blocked content, retained sensitive-data checks, source/history preservation, per-post clearing, busy scans, format changes, renames, missing files, mid-analysis settings changes, metadata, legacy schema migration, private API key storage/removal, structured multimodal requests, safe HTTP errors, refusals and invented source dates.
- **Browser UI passed** against installed Chromium through Playwright: actual UI and Python APIs, key save/remove, OpenAI settings, clear confirmation, repeated analysis, corrected blocked TXT, metadata, library, login controls, queue/pause and widths 900/1100/1440 without horizontal overflow or JavaScript errors. `CHROMIUM_EXECUTABLE` can select a local Chromium binary; otherwise the test uses `chromium` on PATH or a Playwright-managed browser. Install a browser separately if neither is available.
- **Native Linux window passed** with real Qt WebEngine and the JavaScript/Python bridge, including clearing, rechecking, sent-history preservation and API key privacy. The test uses temporary data and closes itself.
- **Real FFmpeg media check passed:** three extracted frames, MP4 transcoding, watermark overlay and unchanged original bytes.
- **Dependency consistency, JavaScript syntax and Python 3.10 grammar checks passed.**

OpenAI responses, connection checks and Telegram operations are simulated in automated tests. No real API key or account credentials are required. These checks validate request/response handling and workflow, not live model quality, billing, connectivity or delivery. Windows Server 2016, Windows DPAPI, native WebView2 and Microsoft's installer were not executed in this Linux environment. On Windows, run `python tests/native_smoke.py` using `.venv\Scripts\python.exe` to exercise the platform renderer without live Telegram sends.

The OpenAI key is stored in `openai-key.dat`, using the existing DPAPI protection on Windows and restrictive file permissions elsewhere. UI snapshots contain only a saved-key boolean. Authentication headers are sent only to the fixed OpenAI HTTPS endpoint with redirects disabled. Analysis uses `store: false`; actual service retention is governed by the account's OpenAI policies.

Screenshots are generated in `tests/artifacts/` and excluded from release archives. Distribution includes source, launchers, fonts, guides and tests; it excludes environments, runtime databases, credentials, sessions, caches and screenshots. Historical results below describe earlier versions and their environments.

## Fix2 validation

The supplied Windows Server 2016 log confirms successful installation of the Python dependencies under Python 3.10.11 x64, followed by pywebview selecting MSHTML and the application's intentional `desktop_renderer_unavailable` rejection. That log alone does not distinguish an absent WebView2 Runtime from an unsuitable .NET Framework installation.

The new prerequisite check reports both versions and installs the signed Microsoft Evergreen Runtime only when .NET is suitable and the Runtime is missing or too old. Registry permission failures do not trigger an installation. Installer success is followed by a registry recheck; reboot requests stop setup without rebooting the machine.

`python -m unittest discover -s tests -p 'test_*.py' -v`: **64 tests passed** on Linux. The 14 additional tests cover per-user and per-machine registration, invalid/outdated versions, .NET failures, installation and post-install checks, restart requests, registry access errors, rejection after failed signature verification, offline installer handling, installer exit codes and cached-setup integration. Registry access, PowerShell verification and EXE execution are simulated in these tests; no Windows installer was run in this environment.

The existing Linux installation was refreshed through `setup_desktop.py`. Actual Windows Authenticode verification, Runtime download/installation and native WebView2 execution still require validation on Windows. No live account credentials are used by these tests.

## Previous compatibility patch validation

Validated with Python 3.12 on Debian Linux from the final `publisher` directory:

- Clean installation with `python3 setup_desktop.py`, followed by a cached second run: passed.
- `python -m pip check`: passed, no broken requirements.
- `python -m unittest discover -s tests -p 'test_*.py' -v`: **50 tests passed**. The 8 added startup tests cover Windows WebView2 selection without importing Qt, rejection of MSHTML, the renderer passed to window startup, both software-rendering flags, early import failures, native process exit codes, setup failure handling and local virtual-environment selection.
- `python tests/media_smoke.py`: passed with real FFmpeg frame extraction, MP4 encoding and watermarking.
- `QT_QPA_PLATFORM=offscreen QTWEBENGINE_CHROMIUM_FLAGS=--disable-gpu python tests/native_smoke.py`: passed using real Qt WebEngine and the real JavaScript/Python bridge. Scanning, editing, file preparation, scheduling, simulated delivery, pause and settings were exercised. The test closes its window and uses temporary data.
- `node --check ui/app.js` and parsing all Python sources with the Python 3.10 grammar: passed.
- Windows x64 wheels for pythonnet 3.0.5, cryptg 0.6.0, Pillow 12.2.0 and imageio-ffmpeg 0.8.0 were downloaded for Python 3.12. This verifies artifact availability, not Windows execution.

**Not executed here:** the Windows CMD launcher, native WebView2 on Windows Server 2016, Windows DPAPI, real Telegram authentication/delivery, or Ollama model inference. Windows renderer selection in unit tests is simulated. The browser-specific Playwright test was not rerun for this patch; the native UI smoke test above was run. No live account credentials are required for these checks.

On a Windows machine with the documented prerequisites, run the existing test suite and `python tests/native_smoke.py` using `.venv\Scripts\python.exe` to validate WebView2 on that system. The native test selects the platform's renderer and simulates all Telegram operations.

The startup log records both setup and application subprocesses, including failures before `app.main()` and native exit codes. Successful startup does not establish live Telegram access.

## Original 1.2.2 validation notes (historical)

The release was checked in a Linux build environment. It contains a Windows launcher and Windows-specific implementations, but **Windows startup, DPAPI on Windows, real login to Telegram and delivery to a live channel have not been exercised here**. Version 1.2 replaces the Bot API bot-token connection with a personal-account MTProto connection (Telethon) and adds an interactive login wizard. No real api_id, api_hash, phone number, session, channel or user files are included.

## Automated coverage

Run from the project directory after installing `requirements.txt`:

```sh
python -m unittest discover -s tests -p 'test_*.py' -v
python tests/media_smoke.py
```

The 42 unit/integration tests cover the previous 28 behaviors — full TXT preservation and link insertion, source preservation, deduplication, unknown SVB exclusion, Tehran scheduling, one fake delivery at the scheduled slot, recovery after interrupted work, ambiguous-delivery handling, rate-limit rescheduling, file changes, sensitive-text blocking, blocked-job state protections, concurrent settings changes, exclusion during analysis, missed slots, caption limits, secret-free UI snapshots, single-instance locking, image watermarking, multimodal AI input structure and one-pass custom templates with publication year. New coverage for 1.2: full login flow with Persian-digit and local phone-number normalization, protected session and credential persistence, two-step password flow, wrong code/password errors, login-busy and cancel, logout that wipes both secret files and stops publishing, enable/verify requiring a logged-in account, session-invalid failures resetting the client, jobs failing cleanly when no account is logged in, MTProto error translation (flood waits, invalid code/password, invalid app credentials, revoked session, write permission, network), resolution of marked -100 channel ids from dialogs, creator/admin permission checks, supergroup membership, send kind mapping and input normalization.

The media smoke test uses FFmpeg to create an ordinary synthetic clip, extract three frames and generate a branded MP4 without changing the source. It passed in this environment for 1.2.

Both Telegram login and delivery are simulated with fake clients in automated tests; media encoding and filesystem/SQLite operations are real. This testing does not establish live Telegram connectivity, account safety against real flood limits, semantic accuracy of an installed Ollama model, Windows hardware compatibility or performance on large libraries/videos. First deployment should use a few ordinary sample files and an owned test channel.

## Install-path check (1.2.1)

`requirements.txt` selects `cryptg==0.6.0` on Python 3.11–3.13 and `cryptg==0.5.2` on Python 3.10 via environment markers. Both versions publish prebuilt `win_amd64` wheels (0.5.2 ships cp310, 0.6.0 ships cp311–cp313), so no Rust/C compiler is needed on end-user machines. Resolution was verified with `pip install --dry-run -r requirements.txt` (pip 26.2.2; Python 3.12 selects 0.6.0, already satisfied). All project sources parse with `ast.parse(feature_version=(3, 10))`, matching the documented Python 3.10–3.13 range. The launcher (`setup_desktop.py`) retries the dependency install without the optional cryptg accelerator if the full install fails, installs cryptg best-effort afterwards, and its cached-dependency check no longer requires cryptg to be importable — a skipped cryptg cannot cause a reinstall loop.

## Startup diagnostics (1.2.2)

`app.py` no longer hides renderer failures. When `initialize_desktop()` raises, the console prints a per-module probe (shiboken6, PySide6 QtCore/QtWidgets/QtWebEngineWidgets, QtPy, pywebview qt backend with its resolved renderer) and the full traceback is appended to `startup-error.log` next to the launcher; targeted hints cover a missing Visual C++ runtime, a non-Latin install path, GPU-driver errors and a conflicting Qt on PATH. If `webview.start` fails after the renderer check, the app relaunches itself once with `QTWEBENGINE_CHROMIUM_FLAGS=--disable-gpu` before reporting. The probe path was exercised in the build environment, which has no Qt system libraries: it reported OK for shiboken6/QtCore and FAIL with the precise missing-library names (libEGL.so.1, libXtst.so.6) for QtWidgets/QtWebEngine, matching the known sandbox limitation, and wrote the expected log file.

## Visual check

```sh
python tests/browser_smoke.py
node --check ui/app.js
```

The visual check runs Playwright with Chromium against the real UI and local Python API with safe synthetic fixtures. It checks the library, the new account card in its connected state (name, username, phone, logout button), the login wizard steps with inline errors, settings, live unsaved caption preview, custom-template editing, search, scheduling and pause; widths 900, 1100 and 1440 have no horizontal document overflow. It asserts compact header button dimensions and absence of JavaScript errors. Screenshots are generated in `tests/artifacts/`, which is excluded from the download ZIP. Both commands passed in this environment.

## Native check

```sh
QT_QPA_PLATFORM=offscreen QTWEBENGINE_CHROMIUM_FLAGS='--no-sandbox --disable-gpu' python tests/native_smoke.py
```

The native check opens a real pywebview/PySide6 window, drives the JavaScript-to-Python bridge through scan, edit, prepare, schedule, simulated account delivery and pause, and asserts the account card state. It was updated for 1.2 but **could not be executed in this build environment**: installing PySide6 succeeded, but the sandbox lacks the Qt WebEngine system libraries (libXtst and friends) and has no root access to install them. The same test passed for the previous release in the development environment; re-run it on a machine with the Qt system libraries before shipping. `--no-sandbox` is used only for this headless test invocation, not by the shipped launchers.

## Security notes

The stored secrets are `api-credentials.dat` (api_id, api_hash, phone) and `account-session.dat` (StringSession). On Windows they are wrapped with DPAPI for the current user; the launcher writes them with restrictive permissions elsewhere. The login code and password are only held in memory during the interactive login. Snapshots never include the session string or api_hash (tested). Logout deletes both files and disables publishing. The old `bot-token.dat` from previous versions is no longer read; users should delete it manually if it exists.

## Package hygiene

The distributable excludes virtual environments, runtime databases, tokens, sessions, generated screenshots and cache directories. Fonts include their OFL notices. Telethon (MIT), cryptg (CC0 1.0), pyaes (MIT) and rsa (Apache-2.0) are installed by the launcher from their published packages and are indexed in `THIRD_PARTY_NOTICES.md`.
