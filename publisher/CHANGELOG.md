# 1.4.2 — Private channel selection and administrator permissions

- Fix permission checks to read Telethon ParticipantPermissions.post_messages instead of a nonexistent admin_rights field; preserve creator access and require actual post permission for channel administrators.
- Preserve network/session errors from permission queries instead of masking every failure as an authorization denial.
- List owned and writable public/private broadcast channels from paginated dialogs, using marked numeric IDs. Add a UI channel picker and verification flow; selection pauses publishing and updates the destination and branding fields.
- Resolve numeric destinations as integer peer IDs and remove the previous 500-dialog fallback limit.
- Add six regression tests with real Telethon permission objects and browser coverage for fetching, selecting and saving a private channel.

# 1.4.1 — Telegram login connection fix

- Correct the Telethon connection contract: connect() returns None on success. Await it and inspect is_connected() instead of treating its return value as a boolean failure. This applies to first login and reused publishing connections.
- Report network timeout, DNS and refused-connection errors separately without exposing raw exception details. Keep retry handling for these network errors.
- Add regression coverage through the real MTProto wrapper for successful None-returning connections, reconnection, disconnected state, code requests and retry after failure. 102 automated tests passed; no live Telegram account was used.

# 1.4.0 — Faster intake and TXT channel branding

- Remove internal semantic content gates and migrate previously blocked jobs to fresh analysis. Retain actual I/O, configuration and delivery-error handling.
- Replace full-file polling hashes with stat metadata; preserve sent-path history across this change. Shorten AI request timeouts and use local drafts on AI failure, with one failed attempt per scan before local fallback.
- Stream TXT output through channel-reference cleanup across UTF-8, UTF-16 and CP1256. Strip Telegram handles, links and explicit channel promotion lines; preserve email addresses and source files. Brand output filenames and text with the destination channel identity. Large texts no longer bypass cleanup.
- Add regression coverage for encoding, large-line boundaries, stat-only scanning, outage fallback and sent-path preservation.

# 1.3.3 — Optional content restrictions

- Add a persisted content restriction checkbox, enabled by default. Apply it consistently to intake, editing, templates and preparation. Provider refusals use a filename draft when disabled.
- Incorporate the supplied 1.3.2 archive. Fix path-scoped duplicate identities and preserve large-text encoding by copying bytes unchanged.
- Adapt regression assertions to the supplied no-fingerprint behavior; add filter persistence, toggle and provider-refusal coverage.

# 1.3.2 — No fingerprint gating, softer AI flow

- Remove the fingerprint change guard: files are prepared and published even if their bytes changed after analysis (file_changed is gone). The digest stays in the database only as metadata.
- Remove duplicate-content detection: identical files under different names are no longer marked duplicate_content; each path is judged on its own record. Sent files at the same path are still skipped until the result is cleared.
- An AI "review" decision now falls back to a filename-based draft instead of failing with unclear_content.
- The sensitive-data guard is unchanged and fully active: credentials, contact lists, combo/hits material and similar content stay blocked at analysis, preparation and caption layers.

# 1.3.1 — Unrestricted file types and sizes

- Accept every file extension: known types keep photo/video/text handling, unknown extensions are sent as plain documents with their original name. Header-based detection (JPEG/PNG/WebP, MP4/EBML, PDF) still overrides the extension.
- Remove the 49 MB application cap everywhere (folder scan and preparation). Telegram's own per-account limit (2 GB regular, 4 GB Premium) is now the only boundary; oversized uploads fail with Telegram's response.
- Read only the first 2 MB of text files for AI analysis instead of rejecting larger files; the full file is still published. Text files over 16 MB stream through untouched with signature links written around the copy.
- Keep the sensitive-data guard (credentials, contact lists) and the digest/fingerprint change check fully active. Scale video re-encode timeout with file size (15 min per 300 MB, capped at 2 h).

# 1.3.0 — OpenAI, clear and recheck

- Add OpenAI Responses API analysis with a configurable model, structured Persian title/caption/category and a date copied from the source. Keep the scheduled publication date tied to the system clock and selected timezone. Preserve optional local Ollama analysis.
- Add an in-app API key field, connection check and key removal. Store the key separately with Windows DPAPI; never return it in UI snapshots or include provider response bodies in errors. Identify missing keys, authentication failures, quota limits and network failures separately.
- Add clear/reset for all eligible unsent results or one post, followed by fresh analysis. Pause publishing during manual rescans; retain original files, sent history and in-flight/ambiguous deliveries.
- Re-read unchanged files on explicit scans, retry blocked TXT, update file type from signatures, reuse unsent records after edits/renames and refresh generated previews. Background scans still skip unchanged files and preserve manual captions.
- Narrow the email-list check so ordinary prose containing public contact addresses is not blocked just because it contains two addresses. Keep explicit sensitive-data checks and show an actionable reason. A reset requires fresh analysis before editing or preparing a post.
- Migrate existing databases without discarding settings or jobs. Add Persian setup/retry guidance and 25 regression tests; 89 unit/integration tests, browser UI, native Linux bridge and real FFmpeg checks passed. Live OpenAI, Telegram and Windows execution remain untested here.

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
