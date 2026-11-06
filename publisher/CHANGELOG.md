# Server 2016 Fix2: missing WebView2 prerequisite

- Detect and report .NET Framework and WebView2 separately before loading the Windows renderer.
- When .NET is suitable but WebView2 is missing, download Microsoft's Evergreen x64 installer and require a valid Microsoft Authenticode signature before execution. Support the official standalone installer placed beside the launcher for offline distribution.
- Recheck Runtime registration after installation, preserve existing installations and handle installation errors or reboot requests without launching the app prematurely.
- Keep the Python dependency cache independent of whether the .NET runtime can load. Add 14 regression tests for prerequisite detection and setup decisions. Windows installer execution remains unverified in the Linux development environment.

# Server 2016 compatibility patch for 1.2.2

- Use Microsoft Edge WebView2 on Windows, including Server 2016 Desktop Experience, instead of requiring Qt 6.10. Keep Qt WebEngine on Linux and reject legacy MSHTML fallback.
- Install pythonnet on Windows and Qt dependencies only on non-Windows platforms. Check the Windows renderer after dependency installation, including cached installations.
- Capture setup output, early import errors and native process exit codes in startup-error.log. Keep the command window open on failure and validate the selected Python version and architecture.
- Apply software rendering flags to both WebView2 and Qt. Add a Persian Server 2016 setup guide and regression coverage for startup behavior.
- Windows execution and real Telegram delivery require validation on the target system; Linux tests do not establish Windows readiness.

# 1.2.2

- Diagnose startup failures instead of hiding them: when the Qt desktop renderer cannot initialize, the console now prints a step-by-step probe (shiboken6, PySide6 QtCore/QtWidgets/QtWebEngine, QtPy, the pywebview qt backend) showing the exact error of every failing module, writes the full traceback to `startup-error.log` next to START-LIONXSEC.cmd and prints targeted hints (missing Visual C++ runtime, non-Latin install path, GPU driver, conflicting Qt on PATH).
- If the desktop window itself fails after the renderer check, the app automatically retries once with GPU acceleration disabled before showing the final error (same effect as START-LIONXSEC-SOFTWARE.cmd).
- No changes to login, publishing or interface behavior.

# 1.2.1

- Fix first-run setup on Python 3.10: requirements now install cryptg 0.6.0 on Python 3.11–3.13 and the cryptg 0.5.2 prebuilt wheel on Python 3.10 via environment markers. Previously the hard `cryptg==0.6.0` pin aborted the whole installation on Python 3.10 with "No matching distribution found ... Requires-Python >=3.11".
- Make launcher setup resilient: if the optional cryptg accelerator still cannot be installed, dependencies are retried without it and the app starts anyway (Telethon then uses its pure-Python AES, a little slower). The cached-dependency check no longer requires cryptg to be importable, so a skipped cryptg cannot cause a reinstall loop.
- No changes to login, publishing, storage or interface behavior. Documented Python range stays 3.10–3.13 (64-bit).

# 1.2.0

- Add personal-account login with Telegram app credentials: api_id and api_hash from my.telegram.org, the phone number, the login code sent by Telegram and optional two-step verification password. The accepted session is stored locally as a StringSession protected with Windows DPAPI (chmod 600 fallback elsewhere).
- Publish through the logged-in personal account via MTProto (Telethon): channel check requires creator or post permission, private -100 channels are resolved from recent dialogs, media is sent as photo/video/document with the same caption pipeline.
- Remove the BotFather bot-token method entirely: the bot-token card, its validation, the Bot API client and requests-toolbelt are gone. Publishing now requires a logged-in account.
- Add an in-app login wizard in the settings page with three steps (credentials + phone, code, password), cancel support, inline error messages, connected-state display (name, username, phone) and a logout action that wipes the stored session and credentials and stops publishing.
- Map Telethon failures onto stable, secret-free codes: invalid/expired code, wrong password, invalid app credentials, revoked session, permission denied, flood waits with retry_after, connection failures and ambiguous delivery kept conservative.
- Verify before scheduling: enabling the schedule re-checks the account session and channel permission; a session invalidated mid-run fails pending jobs and forces a fresh connection next time.
- Ship Telethon 1.45.0 with the cryptg 0.6.0 accelerator in requirements; the launcher's dependency check includes both.
- Keep the existing intake, caption-template, scheduling, recovery and content-protection behavior unchanged; extend tests to 42, covering the full login flow, 2FA, cancellation, logout cleanup, secret-free snapshots, MTProto error mapping and resolution of marked channel ids.

# 1.1.0

- Select PySide6/Qt WebEngine explicitly. Refuse legacy MSHTML fallback, which could display a white, broken layout and fail to execute the interface JavaScript.
- Install the Qt runtime through the launcher; add an optional software-rendering launcher for GPU driver issues. Require 64-bit Windows 10/11.
- Add editable caption templates with title, body, filename, publication year and brand placeholders, plus live preview while editing a post.
- Preserve the automatic date/channel/site footer and Telegram caption length limit. Keep template variables inert and validate them before saving.
- Clarify that Telegram app API credentials are different from a BotFather bot token. Personal-account login is not included.
- Hold personal email collections for protection instead of converting them into promotional captions.
- Preserve existing AppData settings and queue data. Startup still pauses publishing.
