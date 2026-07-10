# Changelog

All notable changes to the Ultimate Downloader will be documented in this file.

---

## v6.3 (Latest)
**Theme: TorBox Share-Link Support**

### ✨ New Features
- **TorBox folder links resolve natively**: Paste a `torbox.app/download?id=...&type=...` link (the TorBox site's "Copy JDownloader Folder Links" button) and the queue expands it into every file in the torrent — with real filenames, so episode detection and TMDB matching work. Works for torrents, usenet, and web downloads, and resolves via the TorBox API regardless of which debrid service is toggled (uses the saved TB key)
- **TorBox CDN links get real names**: Bare `store-*.tb-cdn.io/dld/<uuid>` links are now downloaded directly (no pointless re-unrestricting through the debrid path) and named from the server's `Content-Disposition` header instead of the opaque UUID, so they too can match episodes
- **Cached TorBox files download in parallel**: When a TorBox torrent/usenet/web item is already cached, its per-file direct links are pre-fetched and the files join the parallel aria2 pool (respecting the Parallel DLs slider) instead of downloading one-by-one. Uncached items still go through the sequential flow that polls TorBox's caching progress
- **Auto Retry failed batches**: New optional "Auto Retry" field next to the Parallel DLs slider. Set it to N and a batch that ends with failures automatically re-runs the 🔁 Retry Failed path — until nothing is left failed or N extra passes have run, whichever comes first. Empty = off (unchanged behaviour). Each pass starts with a 5-second countdown (`Auto Retry 2/3 starting in 5s...`) during which a kernel interrupt (Ctrl+M I) cancels the chain; interrupting mid-download also stops it. The retry budget refreshes each time a batch is started or 🔁 Retry Failed is clicked manually
- **Overlap Drive moves with downloads (opt-in)**: New ⚙️ Settings → Performance toggle. Finished files are handed to a dedicated mover thread so the download pool starts the next file immediately, instead of each worker idling through its own Drive move. The mover queue is capped at 3 finished files so Colab's local disk can't silently fill (workers block until the mover catches up), and moves stay single-threaded so Drive FUSE writes never compete. On interrupt, queued moves are marked failed with the local file kept — 🔁 Retry re-moves them without re-downloading. Off by default: the classic move-then-next behaviour doubles as backpressure when Colab disk or debrid concurrent slots are tight. Status shows `📤 N moving` alongside download progress

### 🐛 Bug Fixes
- **Episode-range pack names mapped every file to episode 1**: Filenames like `Show.EP01-70.2160p...` (episode-range in the torrent/folder name, files named `01.mp4`…`70.mp4`) matched the range's `EP01` as the episode for *every* file — so file 1 organised to `S01E01` and the remaining files were skipped as duplicates of it. Ranges (`EP01-70`, `E01-E24`) are now recognised as pack markers: per-file numbers drive episode detection, and the range still anchors the show-name split (verified over the detection corpora with zero behaviour changes elsewhere)

---

## v6.2
**Theme: TMDB Metadata Matching & Batch Controls**

### ✨ New Features
- **Stop a running batch**: Use **Runtime → Interrupt execution** (shortcut Ctrl+M I / ⌘+M I) to stop cleanly — active aria2/megadl downloads are terminated, not-yet-started items stay pending, and the session is saved so Resume/Retry picks up where you left off. A hint is shown during downloads. (A widget "Stop" button can't be used here: the download runs inside a widget callback that blocks the kernel's shell thread, so a click wouldn't be delivered until the batch already finished — and there's no per-cell ■ button because it's not a cell execution.)
- **🔁 Retry Failed Button**: Appears after a batch completes with failures (or is interrupted) — one click re-runs the resume machinery (fresh link re-resolution included) without restarting the runtime
- **TMDB Metadata Matching**: Filenames are matched against TMDB at queue time for canonical show/movie names, automatic year detection, and correct Plex folder naming
  - **Absolute-episode → season mapping**: high-count anime episodes (e.g. `One Piece - 1085`) are converted to the correct `SxxEyy` using TMDB per-season episode counts — only when the filename has no explicit season marker
  - Romaji anime titles matched via TMDB alternative titles (e.g. "Ore dake Level Up na Ken" → "Solo Leveling")
  - Queue preview annotates matched files (`→ Show Name (Year)`); a summary line reports match count
  - Persistent query cache on Drive (`tmdb_cache.json`, capped at 500 entries) avoids repeat API calls across sessions
  - Configure via ⚙️ Settings or Colab Secret `TMDB_API_KEY`; toggle with the "TMDB matching" checkbox
  - Fully optional: with no key (or no match) behaviour is identical to v6.1 filename parsing; Force Name always wins
  - **Manual match correction**: a "🎬 Fix Match" row in the queue preview lets you correct or clear the auto-match per item — select rows, then paste a TMDB URL / `tv:12345` / `movie:12345`, or type a title to search (via TMDB multi-search). "Clear Match" forces filename parsing for that item. Corrections are marked (`✎` / `✖ no TMDB`) in the queue and **persist across Stop/Resume** (saved with the session)
  - AniList integration deferred to a future release

---

## v6.1
**Theme: Per-Download Progress Bars & UI Polish**

### ✨ New Features
- **Per-Download Progress Bars**: Each parallel download now gets its own progress bar (filename, live %, speed) inside a collapsible "📥 N active downloads" accordion, in addition to the overall batch bar
  - Bars turn ✅/⏭️/❌ on completion/skip/failure and linger for 2 seconds before being removed
  - Overall progress bar is now fractional (`completed + Σ active%`) for every batch size, so single downloads and small batches show smooth continuous movement instead of staircase jumps

### 🐛 Bug Fixes
- **Race conditions in progress tracking**: The per-task bar cleanup could raise `KeyError`/`RuntimeError` from a monitor-thread/main-thread race, aborting the sequential download phase. Fixed with `.pop()`/`.get()` instead of `del`/direct indexing, and the monitor thread is now joined before the main thread touches shared bar state
- **Stale "Finishing..." bars**: Per-download bars used to freeze in a completed state for the entire sequential (YouTube/Mega/magnet) phase instead of clearing once the parallel phase ended
- **Progress bar colors not rendering**: An inline `bar_color` style was overriding the `bar_style` (info/warning/success/danger) classes, so status colors never showed
- **Widget leak**: Removed per-download bars are now `.close()`d instead of just dropped from the tracking dict, so long sessions don't accumulate orphaned widget models

### 🎨 UI
- Aligned the Debrid dropdown with the Year field in the main input row, and the Debrid dropdown with the Gofile field in ⚙️ Settings

---

## v6.0
**Theme: TorBox Debrid Integration & Reliability Overhaul**

### ✨ New Features
- **TorBox as Full Debrid Service**: TorBox is now fully integrated as an alternative to Real-Debrid
  - Select between Real-Debrid, TorBox, or None via the Debrid Service toggle in the main UI
  - **Magnet links**: Routed through TorBox torrents API (`resolve_tb_magnet_files`) with file selection in queue preview
  - **Premium file hosts**: 35+ hosts (MediaFire, 1fichier, Rapidgator, etc.) routed through TorBox Web Downloads
  - **MEGA links**: Tried via TorBox first, falls back to megadl if TorBox fails
  - **Generic links**: Unknown HTTP links attempted through TorBox web download when TorBox is selected
  - RD-specific direct links (`real-debrid.com/d/`) always use Real-Debrid regardless of toggle
- **Debrid-Agnostic Download Pipeline**: `resolve_all_links()`, `_run_download_pipeline()`, `execute_selected_tasks()`, and `execute_batch()` now support both debrid services through a unified routing layer
- **TorBox Session Resume**: TB links re-resolved with fresh tokens on session resume (same as RD/Gofile/Pixeldrain); the API key is read from the widget/Colab Secrets rather than stored on Drive (see Security below)

### 🔧 Improvements
- Added `get_active_debrid()` helper to centralise debrid service selection logic
- `DEBRID_SUPPORTED_HOSTS` used for both RD and TB host routing (replaces `RD_SUPPORTED_HOSTS` references)
- Queue preview displays 📦 icon for TorBox-resolved links (`tb`, `tb_host`, `tb_magnet_file`)

### 🐛 Bug Fixes (code review pass)
- **MediaFire/1fichier links silently dropped**: Without a debrid service selected, resolved MediaFire and 1fichier tasks were excluded from the download partition and never downloaded (while the summary reported success). Tasks are now partitioned by exclusion (`SEQUENTIAL_LINK_TYPES`) so new resolver types can never be silently dropped
- **Resume skipped selected torrent files**: Resuming a session containing `magnet_file`/`tb_magnet_file` tasks dropped them; they are now passed through to the pipeline
- **Parallel download file mix-up**: The aria2 "renamed file" fallback could grab another worker's in-progress file. It now prefers the path aria2 itself reports (`Download complete:`), skips files with `.aria2` control files, and only accepts files created after the download attempt started
- **Duplicate handling unified**: An existing file in Drive is now consistently kept (previously non-archive downloads silently overwrote it while archive extraction kept it). Subtitles still refresh, since re-downloading them is an explicit action
- **Batch episode detection now works for torrent files**: The episode cache is keyed on filenames stripped of the queue's " (123.4 MB)" size suffix, so download-time lookups match
- **Year override survives resume**: Sequential (YouTube/Mega/debrid) session saves omitted the year field
- **Queue preview button state**: Resolve/Quick Download stay disabled while the queue preview is open (previously re-enabled immediately, allowing the pending queue to be clobbered)
- **History view**: Fixed literal `\n` printed in the download history listing
- **Debrid downloads reported honest status**: Magnet links and selected torrent files (RD & TorBox) were marked `done` unconditionally regardless of outcome, hiding failures in the summary and preventing resume from retrying them. `process_rd_link`/`process_tb_link` now return success, and the magnet-file processors set per-file status. Files already present in Drive are marked `skipped` (via a `DUPLICATE_SKIP` sentinel) instead of `failed`, which also fixes parallel duplicates being mislabelled as failures

### 🔒 Security
- **Credentials no longer written to Drive in plaintext**: `settings.json` no longer stores the FShare password and `session.json` no longer stores Gofile/RD/TorBox tokens. On resume, tokens are re-read from the widgets/Colab Secrets (legacy session files still work as a fallback). Use Colab Secrets (`FSHARE_PASSWORD`, `RD_TOKEN`, `TB_TOKEN`, `GOFILE_TOKEN`) for persistence
- **Hostname-based URL routing**: Link routing now compares `urlparse` hostnames instead of substring matching, so `evil.com/?x=mega.nz` can no longer masquerade as a supported host

### ⚡ Performance
- **Single-pass archive extraction**: Archives are extracted with one `unrar`/`7z` invocation instead of one process per file (per-file extraction re-decompressed solid RAR archives from the start each time — O(N²))
- **Concurrent link resolution**: Independent resolvers (Gofile, Pixeldrain, and MediaFire/1fichier when no debrid service is active) run in a thread pool during Resolve Links; rate-limited services (debrid APIs, FShare) stay sequential
- **Throttled session saves**: Per-task session writes to Drive FUSE are throttled to one per 5 seconds, with a guaranteed final save at batch end
- **Numeric progress tracking**: Download progress/speed is stored as numbers (`download_stats`) instead of formatting strings and regex-parsing them back every 0.5s
- **Keep-alive thread fixes**: `start_keep_alive()` is idempotent (no more duplicate threads per batch) and stops promptly instead of after up to 2 minutes

### 🧹 Maintainability
- Extracted `detect_episode_info()` — the episode/show-name parsing is now a pure function (no widget access), verified byte-identical against the previous behaviour across the test filename corpus
- Deduplicated RD/TorBox torrent flows via shared helpers (`_update_torrent_progress`, `_make_torrent_file_task`, `_group_tasks_by_torrent`, `_tb_fetch_item`, `_tb_extract_download_url`, `_reset_progress_bar`)
- `check_and_load_secrets()` collapsed from five copy-pasted blocks into one loop
- Removed dead code: unused `btn_subs` widget, `enable_retry` parameter, `DownloadTask.retry_count`, `queue_mode` global; session loading now tolerates unknown fields from older versions
- `google.colab` import guarded so the module can be imported outside Colab (enables unit-testing the pure logic)
- Settings save/load failures now print a warning when Drive is mounted (previously always silent)
- Moved `ultimate_downloader_v5.5.py` into `archive/` alongside the other historical versions

---

## v5.5
**Theme: FShare VIP & OK.ru Support**

### ✨ New Features
- **OK.ru (Odnoklassniki) Support**: Download videos from ok.ru
  - Uses yt-dlp's native Odnoklassniki extractor for reliable video downloads
  - Supports single videos (best quality, auto-merged with ffmpeg)
  - Queue preview shows video titles fetched via yt-dlp metadata extraction
  - Works with subtitle downloads when available
  - Some content may be region-restricted — use a proxy if needed

### ⚠️ Experimental Features
- **FShare VIP Download Support**: Download files from fshare.vn using your VIP account
  - Supports both single file links (`fshare.vn/file/...`) and folder links (`fshare.vn/folder/...`)
  - Web-based session scraping (official FShare API is suspended)
  - Persistent session caching — logs in once and reuses across all links in a batch
  - Folder listing uses free API endpoint (no download quota cost)
  - **Deferred download link resolution**: Folder files are listed instantly during Resolve Links, but download links are only resolved when you click Start Download — review and remove unwanted files first to save your daily download limit
  - Smart error handling: Detects `policydownload` restriction and auto-stops after 3 consecutive failures to avoid wasting requests
  - Folder pagination support — automatically fetches all pages (FShare paginates at 50 items)
  - FShare credentials configurable via ⚙️ Settings or Colab Secrets (`FSHARE_EMAIL`, `FSHARE_PASSWORD`)

### ⚠️ Known Limitations (FShare)
- FShare web scraping is inherently fragile and may break when FShare updates their website
- Login may fail on the first 1-2 attempts (click Resolve Links again)
- `policydownload` errors are transient server-side restrictions — wait and retry
- Each resolved single-file download link counts toward your daily FShare download limit

---

## v5.4
**Theme: Colab Stability**

### 🐛 Bug Fixes
- **Colab Anti-Idle Not Working**: Fixed keep-alive failing to prevent disconnection after ~45 minutes
  - Root cause: `Javascript('void(0)')` runs a no-op in the output cell context, which Colab ignores for idle detection
  - Fix: Now simulates clicking the Colab connect button via JavaScript, which is the standard method to reset the idle timer
  - Reduced keep-alive interval from 5 minutes to 2 minutes for more reliable coverage
  - Added console logging (`Colab keep-alive: HH:MM:SS`) for debugging in browser DevTools
- **Real-Debrid Magnet Rate Limiting**: Fixed multiple magnet links triggering RD fair-use policy blocks
  - Adaptive pacing: magnets resolve at full speed until RD returns `too_many_requests`, then auto-paces at 2s intervals
  - Rate-limited requests retry with exponential backoff (5s → 10s → 20s), up to 4 attempts
  - Added 2-second delay between `unrestrict/link` API calls when processing multi-file torrents

---

## v5.3
**Theme: Episode Detection & Runtime Stability**

### ⚡ Performance
- **Progress-Reporting File Transfers**: Large files (100MB+) moved to Drive now show real-time progress
  - Transfer progress printed every 500MB with percentage, size, and speed
  - Final summary with total time and average speed (e.g., "✅ Transfer complete: 4200 MB in 85s (49.4 MB/s)")

### ✨ New Features
- **Colab Anti-Idle Keep-Alive**: Background thread prevents Google Colab from disconnecting during long downloads
  - Periodically executes a no-op JavaScript call to reset Colab's idle timer
  - Runs every 5 minutes during active downloads (zero performance impact)
  - Starts automatically when downloads begin, stops when they finish
  - Covers all download phases: link resolution, parallel downloads, and sequential processing

### 🐛 Bug Fixes
- **NNxNN Episode Format Not Detected**: Fixed auto-organise incorrectly mapping all files to S01E01 for filenames using `NNxNN` format like `Death Note - 01x05 - Tactics.mkv`
  - Added dedicated `sxe_nxn` regex for `NNxNN` season×episode format (matches `01x05`, `1x03`, `02x15`, etc.)
  - Correctly extracts both season and episode numbers (e.g., `02x07` → S02E07)
  - Fixed `sxe_underscore` and batch `extract_dash_numbers` matching the season part (`01`) by adding negative lookahead to skip `NNxNN` patterns
  - Added `extract_nxn_numbers` to batch analysis for multi-file NNxNN detection
- **Incorrect Part Suffix on SxxExx Episodes**: Fixed `(Part 1)` in filenames like `Cowboy Bebop - S01E25 - The Real Folk Blues (Part 1).mkv` incorrectly adding `-pt1` suffix to the episode number
  - `S01E25` already uniquely identifies the episode — Part 2 is typically `S01E26`, not `S01E25-pt2`
  - English "Part X" suffix now only applied when no standard `SxxExx` or `NxN` pattern is detected
  - CJK multi-part markers (上篇/下篇/中篇) still always apply as they genuinely split episodes
- **Season 00 Folder Named "Specials"**: `S00Exx` episodes now go into a `Specials` folder instead of `Season 00`, matching Plex/media server conventions

---

## v5.2
**Theme: Session Persistence & Detection Fixes**

### ✨ New Features
- **Queue Sort**: New "Sort A-Z/Z-A" button in queue preview to sort resolved links alphabetically by filename
  - Useful for batch downloads where links resolve in arbitrary order
  - Sorts case-insensitively; falls back to URL when filename is unavailable
- **Year Field**: New text input next to Force Name to append `(YYYY)` to folder names
  - TV shows: `Show Name (2008)/Season 01/Show Name - S01E01.mkv`
  - Movies: `Movie Name (2010)/Movie Name.mkv`
  - File names remain unchanged — only the folder gets the year suffix
  - Year value persists in session and restores on resume

### 🐛 Bug Fixes
- **MEGA Folder/File Links Not Downloading**: Fixed `mega.nz/folder/.../file/...` URLs silently reporting "Download Complete" without actually downloading anything
  - `megadl` doesn't support folder/file URL format and exits with code 0
  - Now tries Real-Debrid first; if RD fails (e.g. Colab IP blocked), falls back to megadl
  - Folder/file URLs are auto-converted to folder-only URLs for megadl compatibility
  - Post-download validation detects when megadl downloads nothing and reports failure
- **Session Resume Settings Loss**: Fixed media type (Movies/TV vs Anime) and category override (Auto/Movie/Series) not persisting when resuming a previous session
  - Both values now saved to `settings.json` (with auto-save on change) and `session.json`
  - Resume flow now restores media type and category before processing downloads
- **Version Suffix Detection**: Fixed `S01E01v2` pattern not being detected as a valid episode
  - The `v2`/`v3` version suffix (common in fansub re-releases) caused the regex word boundary to fail
  - Now correctly matches `S01E01v2.mkv` as Season 1, Episode 1
- **Resume Skipping Active Downloads**: Fixed downloads that were in-progress when runtime disconnected being silently skipped on resume
  - Tasks with `downloading` status are now included in resume (previously only `pending` and `failed` were retried)
- **4-Digit Space-Separated Episodes Not Detected**: Fixed auto-organise failing for filenames like `[Fabre-RAW] Detective Conan 0724 [NetflixJP] [1080]`
  - Space-separated number patterns were limited to 3 digits — expanded to support 4-digit episode numbers
  - Lookahead in space-number regex only accepted letters after the number — now also accepts `[` for fansub bracket tags like `[NetflixJP]`
  - Same fixes applied to batch detection, individual fallback, and bracket episode patterns

---

## v5.1
**Theme: Code Quality & Reliability**

### 🔧 Improvements
- **Refactored Download Pipeline**: Extracted shared download orchestration into `_run_download_pipeline()` function
  - Eliminated ~230 lines of code duplication between `execute_batch()` and `execute_selected_tasks()`
  - Both parallel and sequential download logic now uses the same code path
- **Configuration Constants**: Added `REQUEST_TIMEOUT`, `GOFILE_WEBSITE_TOKEN`, `KNOWN_RESOLUTIONS`, `YEAR_RANGE`
  - Eliminates magic numbers and centralizes configuration
- **Keyword-Only Arguments**: `save_session()` refactored to use explicit keyword arguments
  - Prevents positional argument ordering bugs and improves code readability

### 🐛 Bug Fixes
- **Mega Download Status**: `process_mega_link()` now returns success/failure boolean
  - Downloads correctly marked as "failed" when Mega errors occur (was always "done" before)
- **Pixeldrain Crash Fix**: Added null check in `resolve_pixeldrain()` to prevent crash on malformed URLs
- **Removed Dead Code**: Deleted unused `technical_pattern` regex variable from `clean_show_name()`

---

## v5.0
**Theme: Quick Download, Batch Detection & Global Episode Support**

### ✨ New Features
- **Quick Download Button**: New "Quick Download" button next to Resolve Links
  - Download immediately without queue preview — minimal friction from pasting to downloading
  - Respects auto-organise settings (Name, Category, Movies/TV toggle)
  - Subtitle settings in Settings panel (checkbox + language selector, persist across sessions)
- **Batch Episode Detection**: Smart detection analyzes all files in a batch together
  - Finds varying patterns (episode numbers) vs constants (resolution, codec)
  - Example: In `[1080P]...[01]..` through `[1080P]...[24]..`, identifies `[01]` as episode
  - Much more reliable than single-file heuristics
- **Fansub Episode Detection**: New `sxe_bracket` pattern for `[01]`, `[02]` format
  - Correctly ignores resolution tags `[1080P]`, codec suffixes `[HEVC-10b]`
  - High priority alongside S01E01 format

### 🐛 Bug Fixes
- **International Episode Detection**: Enhanced patterns for global naming
  - Added Japanese `第X話` (e.g., `第1085話` → Episode 1085)
  - Added Portuguese `Episodio X`, Vietnamese `Tập X`, Korean `X화`
  - Fixed "Part X" in movie titles incorrectly detected as episodes
  - Added 4-digit episode support, underscore-dash `_-_01_` format, space-separated `Show 01 Title` format
- **Show Name Extraction**: Stops stripping brackets when content contains spaces (multi-word = show name)
- **Subtitle Naming**: Fixed subtitle files including video ID in filename

---

## v4.34
**Theme: Archive.org Support & Category Override**

### ✨ New Features
- **Archive.org Support**: Download videos, audio, and documents from the Internet Archive
  - `/details/` pages use yt-dlp for stream selection
  - `/download/` direct links use aria2 for fast parallel downloads (up to 200MB/s!)
  - No DRM, no authentication required
- **Category Override**: New dropdown to force Movie or Series classification
  - **Auto**: Detect from filename (default behavior)
  - **Movie**: Force as movie regardless of episode patterns in filename
  - **Series**: Force as series (uses S01E01 if no episode detected)
  - Perfect for anime movies with episode-like numbers (e.g., "Dragon Ball - 1")

### 🔧 Improvements
- **Conditional Playlist Range Selector**: Moved playlist selection from main UI to queue preview
  - Now only appears when a YouTube playlist is detected
  - Reduces main UI clutter for the common case (single video downloads)
- **Streamlined Download Flow**: Renamed and reorganised buttons for clarity
  - "Start Download" → "Resolve Links" (reflects that it resolves and queues first)
  - "Download Subtitles Only" moved to queue preview as "Download Subtitles"
  - Queue now has separate "Start Download" and "Download Subtitles" buttons

### 🐛 Bug Fixes
- **Session Persistence**: Fixed subtitle selection and YouTube stats not persisting in Mega/RD sequential loops
  - All `save_session` calls now include `subtitle_langs.value` and cumulative stats
  - Resuming a session will now correctly restore the selected subtitle languages

---

## v4.33
**Theme: Streamlined Organization UI & Anime Mode**

### ✨ New Features
- **Auto-Organization Toggle**: Checkbox in main UI to enable/disable automatic file renaming
  - When disabled, files download to a single "Downloads" folder with original filenames
  - Force Name and Media Type options hide when disabled (not applicable)
  - Setting persists across sessions
- **Media Type Toggle**: Switch between "Movies/TV" and "Anime" modes
  - Movies/TV: Organises to `Movies/` and `TV Shows/` folders
  - Anime: Organises to `Anime Movies/` and `Anime Series/` folders
  - All folder paths are configurable in Settings
- **Force Name Enhancement**: "Force Name" field now works with all media types
  - For TV shows: Forces the show name (e.g., `Force Name - S01E01.mkv`)
  - For movies: Forces the folder and filename (e.g., `Force Name/Force Name.mkv`)

### 🔧 Improvements
- **Improved Movie Renaming**: Movie files are now renamed to match their folder name
  - Before: `The.Matrix.1999.1080p.BluRay.mkv` in folder `The Matrix/`
  - After: `The Matrix (1999).mkv` in folder `The Matrix (1999)/`
  - Folder names now include the year for better Plex/media server compatibility
- **Cleaner Main UI**: 
  - Moved API token fields to Settings panel
  - Removed duplicate auto-organise toggle from Settings
  - Reorganised main UI for better workflow

### 🐛 Bug Fixes
- **Session Resume Data Loss**: Fixed critical bug where YouTube download stats were reset to 0 when parallel downloads completed during a session, causing inaccurate resume counts
- **Subtitle Persistence**: Subtitle language selection is now saved to session and restored on resume

---

## v4.32
**Theme: Magnet File Selection & Progress Bar Improvements**

### ✨ New Features
- **Magnet File Selection**: Magnet links are now resolved during queue preview
  - Individual files from torrent are displayed with size info (e.g., "Episode.01.mkv (1.5 GB)")
  - Select/deselect specific files before downloading
  - Only downloads selected files (saves bandwidth and storage)
  - Automatically filters out small files (<1MB) except subtitles

### 🐛 Bug Fixes
- **Fixed magnet links not downloading**: Magnet links were being incorrectly stored with `link_type="rd"` instead of `link_type="magnet"`, causing them to be completely skipped during processing
- **Fixed aria2 progress not showing**: Removed `--console-log-level=warn` which was suppressing progress output

### 🔧 Improvements
- **Real-Debrid progress bar updates**: Progress bar now actively updates during magnet processing
  - Shows RD caching progress (e.g., "RD: 45% cached") while torrent is being cached
  - Shows download progress during file transfer (e.g., "DL: 67% (5.2MiB/s)")
  - Extended cache timeout from 1 minute to 4 minutes for larger torrents

---

## v4.31
**Theme: Configurable Download Directories & UI Polish**

### ✨ New Features
- **Configurable Download Directories**: Customise where downloads are saved
  - New input fields in Settings for TV Shows, Movies, and YouTube paths
  - Paths are relative to Google Drive root (e.g., `Media/TV Shows`)
  - **Desktop-like Folder Browser**: Click 📁 to browse Drive folders
    - Navigate into subfolders with ⬆️ Up and 📂 Open buttons
    - Create new folders at any level with ➕ Create
    - Select folders with ✓ Select button
  - **Persistent Settings**: Directory preferences saved to `settings.json`
    - Auto-saves when you change any directory input
    - Automatically loads on startup and after Drive mounts

### 🔧 Improvements
- **YouTube Titles in Queue Preview**: Queue now shows video/playlist titles instead of raw URLs
  - Playlists display item count (e.g., "📋 My Playlist (15 videos)")
  - Uses fast metadata extraction without starting downloads
- **Smarter Playlist Range**: Range selector now only ignored when downloading multiple playlist URLs
  - Single videos + 1 playlist = range still applies to the playlist
  - Multiple playlist URLs = range ignored (prevents confusion)
- **Dynamic Subtitle Selector**: Shows only available subtitle languages from YouTube videos
  - Single video: Fetches actual available manual subtitles (excludes auto-generated)
  - Multiple videos or playlists: Shows full language selector
  - Hidden when no subtitles are available on single videos

---

## v4.30
**Theme: Enhanced Progress Display & Download Resilience**

### ⚠️ Experimental Features
- **YouTube Cookies (Experimental)**: Re-added cookie upload/clear functionality with warnings
  - Cookies may cause "Requested format is not available" errors due to IP mismatch or session expiry
  - New **🗑️ Clear Cookies** button in Settings to quickly fix cookie-related errors
  - Marked as "(Experimental)" in the UI to set expectations

### ✨ New Features
- **Progress Display Improvements**: Enhanced download progress with real-time metrics
  - Aggregated download speed (MB/s) across all active downloads
  - Single downloads show real-time progress (0% → 100%)
  - Batch downloads show completed/total progress
  - Persistent speed display (no flickering)
- **Automatic Retry for Failed Downloads**: Resilient handling of transient failures
  - Failed downloads automatically retried up to 2 more times (3 total attempts)
  - Session auto-saved after failures for easy retry via "Resume Previous"
  - Failed files listed in summary
- **Conditional Subtitle Selector**: Only shows for streaming links (YouTube, Vimeo, etc.)
- **Auto-update yt-dlp**: Always upgrades to latest version on each run
- **Thread-safe output**: Print lock prevents interleaved messages during parallel downloads

### 🐛 Bug Fixes
- **Fixed episode numbering for Chinese release formats**: Files with trailing numbers like `[Jiang Hu] Three Kingdoms 2010 HD 01.mp4` now correctly detect episode numbers
  - Before: All files named `Show Name - S01E01.mp4` (all skipped as duplicates)
  - After: Correctly increments to `S01E01.mp4`, `S01E02.mp4`, etc.
- **New trailing number pattern**: Added `sxe_trailing` regex to catch formats not covered by existing patterns:
  - `HD 01`, `HD 02` (common Chinese release format)
  - Trailing numbers before extension: `filename 05.mp4`
  - Numbers after dashes: `Show Name - 03.mp4`
- **Smart filtering**: Trailing pattern excludes years (1900-2099) and resolutions (360, 480, 720, 1080, 2160, 4320) to avoid false positives

---

## v4.29
**Theme: YouTube Playlist Individual Video Tracking**

### 🐛 Bug Fixes
- **Fixed completion message showing "1 task"**: Playlists now report individual video counts instead of counting the playlist URL as one task
  - Before: "All 1 tasks completed successfully!" for a 43-video playlist
  - After: "All 35 downloads completed successfully!" or "⚠️ Completed with 35 success, 8 failed"
- **Fixed resume not offered after partial playlist failures**: When some videos in a playlist fail (e.g., auth errors), the session is now preserved for retry
  - `process_youtube_link` now returns `(success_count, fail_count, total_count)` tuple
  - YouTube task status is set to "failed" if any videos failed
  - Session only cleared when all downloads actually succeed
- **Fixed None entries in playlists**: Unavailable videos in playlist metadata are now counted as failures instead of causing silent issues
- **Fixed playlist range not preserved on resume**: Session now saves and restores `playlist_range` so resumed downloads use the original filter

### ✨ New Features
- **Settings Panel (⚙️ button)**: Comprehensive settings UI with:
  - **🔑 API Keys Status**: Shows ✅/❌ for Gofile and Real-Debrid tokens
  - **🗑️ Clear Data**: Delete history.json, yt_history.txt, or session.json
  - All clear actions require confirmation before deletion
- **Restart Runtime Button**: Appears when downloads have failures, enables seamless resume workflow
- **Subtitle Language Selection**: Queue Preview now includes language selector for YouTube subtitles (12 languages available)

### 🐛 Bug Fixes
- **Improved Filename Parsing**: Fixed issue where episode titles/info were sometimes incorrectly included in the parsed Show Name. The parser now strictly prioritizes the *earliest* detected episode pattern (e.g., "第1集") to cleaner split the show name from episode details.

### 🔧 Improvements
- Each YouTube video download now tracked individually for success/failure
- Completion message shows accurate counts from all download types combined
- **Improved show name cleaning**: Now strips common YouTube prefixes like VIETSUB, ENGSUB, THUYẾT MINH, etc.
- **UI Polish**: 
  - Renamed "Resume Previous" to "Resume Previous Session" for clarity
  - Fixed button widths to prevent text cutoff
  - Added YouTube download summary stats (succeeded/failed counts)

---

## v4.28
**Theme: YouTube Playlist Bug Fix & International Episode Patterns**

### 🐛 Bug Fixes
- **Fixed YouTube playlist duplicate detection**: All videos in a playlist were incorrectly marked as "Already exists" due to:
  - Shallow metadata extraction returning identical titles for all entries
  - Episode detection failing for non-English patterns (e.g., Vietnamese "Tập")
  - When `show_name_override` was set without episode match, all files defaulted to `S01E01`
- **Fixed task count in summary message**: Summary now correctly shows number of completed downloads instead of "All 0 tasks"

### ✨ New Features
- **Playlist Index Fallback**: When no episode pattern matches but `show_name_override` is set, playlist position (1, 2, 3...) is used as episode number
- **International Episode Patterns**: Added support for:
  - Vietnamese: `Tập 1`, `Tập phim 1`
  - Korean: `1화`
  - German: `Folge 1`
  - Spanish: `Capitulo 1`, `Cap 1`
  - Flexible pipe/dash patterns: `Show Name | 3`, `Show Name - 2`

---

## v4.27
**Theme: Queue Management, File Host Support & History Logging**

### ✨ New Features
- **Download History Log**: Persistent log of all completed downloads
  - Stores last 500 downloads in `history.json`
  - Records timestamp (UTC), filename, source, size, destination
  - New 📜 button in UI to view recent downloads
- **Batch Queue Management**: Preview and modify downloads before starting
  - Queue preview shows all resolved links with source icons
  - Select/deselect individual items with checkboxes
  - Move items up/down to reorder priority
  - Remove selected items from queue
  - "Start Selected" to download only chosen items
- **Session Resume Show Name**: Show name override now persists across sessions
  - Saved in session.json and restored on resume
- **Real-Debrid Host Routing**: 35+ file hosts now route through RD
  - MediaFire, 1fichier, Rapidgator, Nitroflare, etc.
  - Prefer RD when token available (premium speeds, no CAPTCHA)
  - Fallback to direct resolve for non-RD users
- **MediaFire Direct Support**: HTML parsing for non-RD users
- **1fichier Direct Support**: POST-based download for non-RD users

### 🔧 Improvements
- Queue icons: 🔥 MediaFire, 📦 1fichier added
- History shows formatted output with file sizes
- `RD_SUPPORTED_HOSTS` constant for easy maintenance

### 🐛 Bug Fixes
- Fixed: RD direct links now correctly parallelized in resume mode
- Fixed: Session resume now restores show_name_override field
- Improved: DownloadTask now uses UUID for tracking (prevents collisions with re-resolved URLs)

---

## v4.26
**Theme: Real-Debrid Parallel Downloads**

### ✨ New Features
- **Real-Debrid Parallel Downloads**: Direct RD links (`real-debrid.com/d/XXX`) now download in parallel
  - Added `resolve_rd_link()` function to unrestrict RD links during resolve phase
  - RD links get re-resolved on resume for fresh download URLs
  - Magnets remain sequential (need to wait for RD caching)

---

## v4.25
**Theme: Parallel Downloads & Session Resume**

### ✨ New Features
- **Parallel Downloads**: Download up to 5 files concurrently using `ThreadPoolExecutor`
  - New UI slider to control concurrent download count (1-5)
  - Applies to Gofile, Pixeldrain, and direct URL downloads
  - Thread-safe progress tracking with per-task status
- **Session Resume**: Save and resume interrupted downloads
  - Session state saved to `Ultimate Downloader/session.json` on Drive
  - New "Resume Previous" button appears when interrupted session detected
  - Failed tasks automatically retry on resume
  - Session cleared on successful batch completion
- **IP Bypass for Rate-Limited Sites**: Re-resolves Gofile/Pixeldrain URLs on resume
  - Stores original user URLs, not resolved API URLs
  - New runtime = new IP = bypasses Pixeldrain rate limits

### 🔧 Improvements
- New `DownloadTask` dataclass for structured task tracking
- Pre-resolve all links before downloading (faster batch start)
- Config files now stored in `My Drive/Ultimate Downloader/` folder
- Drive mounts automatically on script load (enables resume detection)
- Enhanced status display showing active download count and progress

### 🐛 Bug Fixes
- Fixed: Resume now properly installs required tools (aria2, yt-dlp, etc.)
- Fixed: Progress bar updates during parallel downloads

### ⚠️ Notes
- YouTube, MEGA, and Real-Debrid downloads remain sequential (tool limitations)
- Session file location: `My Drive/Ultimate Downloader/session.json`

---

## v4.24
**Theme: Code Quality & Colab Secrets Integration**

### ✨ New Features
- **Colab Secrets Integration**: API keys (Gofile, Real-Debrid) now auto-populate from Colab secrets via `get_colab_secret()` helper
- **Type Hints**: Added Python type hints to function signatures for better code documentation

### 🔧 Improvements
- Refactored hardcoded paths into constants (`COLAB_ROOT`, `DRIVE_BASE`)
- Replaced bare `except:` clauses with `except Exception:` for better debugging
- Added `normalize_playlist_range()` helper for cleaner playlist item parsing
- Code formatting and organization improvements

---

## v4.23
**Theme: Code Quality Improvements**

### 🔧 Improvements
- Minor code quality refinements
- Preparation for Colab secrets integration

---

## v4.22 (Gemini Version)
**Theme: Playlist Range Selection & API Reliability**

### ✨ New Features
- **Playlist Range Selection**: New UI field for custom playlist item selection (e.g., `1,3,5-10`)
- Replaced single `playlist_start` with flexible `playlist_items` syntax

### 🔧 Improvements
- Added timeouts (30s) to all API requests (Gofile, Pixeldrain, Real-Debrid)
- Added `reset_progress()` helper function for cleaner UI state management
- Improved `is_safe_path()` with stricter prefix checking (prevents `/content/temp_evil` matching `/content/temp`)

---

## v4.21
**Theme: Stability Improvements**

### 🔧 Improvements
- Minor bug fixes and stability improvements

---

## v4.20
**Theme: Refinements**

### 🔧 Improvements
- Various code refinements and optimizations

---

## v4.19 (Secure & Fixed)
**Theme: Security Hardening**

### 🔐 Security
- **Path Traversal Prevention**: Added `is_safe_path()` function to prevent directory traversal attacks in archives
- **Safer Subprocess Calls**: Replaced `shell=True` with list-based commands for security

### 🔧 Improvements
- Post-extraction duplicate check with warning messages
- Dynamic extension check for YouTube downloads (mkv vs srt based on mode)
- Improved package installation with proper executable mapping (`pkg_map`)

---

## v4.18g / v4.18s
**Theme: Variant Builds**
- `v4.18g`: General purpose variant
- `v4.18s`: Specialized/extended variant (larger file size)

---

## v4.17 (Progress+ Edition)
**Theme: Duplicate Prevention & Enhanced Progress**

### ✨ New Features
- **Playlist Start Option**: New UI field to start playlist downloads from a specific index
- **Duplicate Checking**: `check_duplicate_in_drive()` function prevents re-downloading existing files
- **Download Archive**: Uses yt-dlp's `download_archive` to track downloaded videos across sessions

### 🔧 Improvements
- Speed display in progress bar (e.g., "YT: 45% (5.2MB/s)")
- Extraction progress counter (`[3/10] -> filename.mkv`)
- Improved error messages with context and troubleshooting hints
- `clean_show_name()` now removes resolution tags (1080p, 4K) and codec info (x265, HEVC)

---

## v4.16
**Theme: Bug Fixes**

### 🐛 Bug Fixes
- Minor extraction and path handling fixes

---

## v4.15 (Smart Install)
**Theme: Optimized Dependency Installation**

### ⚡ Performance
- **Smart Tool Installation**: Analyzes links before installing to only install required dependencies
- Skips yt-dlp installation if no video hosting links detected
- Skips megatools if no mega.nz links detected
- Pre-check for already installed tools to avoid redundant apt-get calls

---

## v4.14 (Sequential Extraction)
**Theme: Restored Sequential Extraction**

### 🔧 Improvements
- Restored sequential extraction logic from v1.5 for better memory management
- Extract one file → move to Drive → delete temp → repeat
- Proper handling of `__MACOSX` junk directories
- Creates target directories before moving files (fixes "File Not Found" errors)

---

## v4.9 (Stable Restore)
**Theme: Cookie Support**

### ✨ New Features
- **Cookie File Support**: Detects `/content/cookies.txt` for authenticated YouTube downloads
- Real-time progress bar updates with Aria2 percentage parsing

### 🔧 Improvements
- Progress bar shows download percentage during Aria2 downloads

---

## v4.7 (Final Golden Copy)
**Theme: Major UI Overhaul**

### ✨ New Features
- **Show Name Override**: UI field to force a specific show name for all files
- **Mega.nz Support**: Full support via `megadl` command with progress tracking
- **Progress Bar**: Visual progress indicator for all download operations
- **Multi-Part Detection**: Recognises Chinese multi-part suffixes (上篇, 中篇, 下篇) and Part 1/2
- **Download Subtitles Only Button**: Separate mode for subtitle-only downloads
- **Asian Episode Pattern**: Supports `第X集` format for Chinese drama naming

### 🎨 UI Changes
- Complete UI redesign with ipywidgets VBox/HBox layout
- Separated buttons for video download vs subtitles only
- Version number in UI header

---

## v3.6 (Notebook Version)
**Theme: Jupyter Notebook Format**
- Converted script to `.ipynb` notebook format for better Colab integration

---

## v2.3
**Theme: YouTube Integration**

### ✨ New Features
- **YouTube Support**: Full integration via yt-dlp with playlist handling
- **YouTube Category**: Separate destination folder for YouTube downloads
- **Enhanced Name Cleaning**: `clean_show_name()` function removes [ENG SUB], brackets, and noise
- Automatic yt-dlp and ffmpeg installation

### 🔧 Improvements
- YouTube videos auto-renamed to Plex-friendly format when episode patterns detected
- Source parameter tracks origin (youtube, mega, generic) for smart routing

---

## v2.1
**Theme: Reliability & Throttling**

### ⚡ Performance
- **Adaptive Connection Limits**: 16 connections for most hosts, 4 for Pixeldrain (rate limit friendly)
- **Download Retry Logic**: 3 attempts with exponential backoff (2s, 4s, 8s)
- Added 5s pause between Pixeldrain downloads to avoid rate limiting

---

## v2.0
**Theme: Subtitle Preservation & Timing**

### ✨ New Features
- **Subtitle Preservation**: `KEEP_EXTENSIONS` set preserves `.srt`, `.ass`, `.sub`, `.vtt` files regardless of size
- **Execution Timer**: Mission report shows total elapsed time

### 🐛 Bug Fixes
- Fixed `.7z` extension detection (was using incorrect string slicing)
- Changed extension check from `lower_name[-4:]` to proper `os.path.splitext()`

---

## v1.9
**Theme: Asian Drama Support & Reporting**

### ✨ New Features
- **Asian Drama Episode Pattern**: Recognises `Ep01`, `E01`, `Episode 01` formats (implies Season 1)
- **Mission Report**: Detailed summary at end showing TV/Movie/Failed counts with file lists
- **Junk Filter**: Configurable `MIN_FILE_SIZE_MB` (15MB) to skip sample files, NFOs, text files

### 🔧 Improvements
- Failed download tracking with `report_log["Failed"]`
- Cleans trailing "Season" text from show names

---

## v1.7
**Theme: Unified File Handler**

### 🔧 Improvements
- **Refactored Processing**: Created `handle_file_processing()` as universal entry point
- All file types (archives and direct files) now route through single handler
- Consistent Plex sorting for both extracted and direct-downloaded content
- Sanitizes Pixeldrain filenames from API response

---

## v1.6
**Theme: Movies Support**

### ✨ New Features
- **Movie Detection**: Files without TV patterns sorted as movies
- **Dual Folder Structure**: Separate `TV Shows` and `Movies` destination paths
- **Year-Based Parsing**: Extracts movie name from pre-year portion (e.g., "The.Matrix.1999.1080p" → "The Matrix")

### 🔧 Improvements
- `determine_destination_path()` replaces simpler `build_plex_tv_path()`
- Smart sorting applied to all download sources (Gofile, Pixeldrain, RD, direct)

---

## v1.5
**Theme: Plex TV Sorting & Stability**

### ✨ New Features
- **Plex TV Auto-Sorting**: Automatic `Show Name/Season XX/` folder structure
- **S##E## Detection**: Regex-based season/episode extraction from filenames
- **Filename Sanitization**: Removes filesystem-illegal characters, collapses spaces

### 🔧 Improvements
- Added request timeouts (15s session, 30s content)
- File size displayed in skip messages (e.g., "exists (~150.5 MB)")
- Fallback to direct download when RD fails on unsupported link
- 10-minute magnet timeout with explicit error message

### 🐛 Bug Fixes
- Improved archive extension detection for `.rar`, `.zip`, `.7z`
- Uses `ignore_errors=True` on temp directory cleanup

---

## v1.0 (Initial Release)
**Theme: Core Functionality**

### ✨ Features
- **Gofile Support**: Download files from Gofile.io with automatic token handling
- **Pixeldrain Support**: Direct file downloads with filename resolution from API
- **Real-Debrid Integration**: Unrestrict premium links and process magnet links
- **Magnet Link Processing**: Add to RD, wait for cache, download generated links
- **Archive Extraction**: Sequential RAR/ZIP/7Z extraction to save Colab disk space
- **Aria2 Downloader**: Multi-connection (16x) downloads with progress output
- **Google Drive Integration**: Automatic mounting and destination folder creation
- **ipywidgets UI**: Simple text fields for tokens and textarea for links

### 🔧 Technical Details
- Auto-installs required tools: aria2, unrar, p7zip-full
- Rate limit handling (429) with 30s backoff for Gofile
- Cookie-based authentication support for Gofile downloads

---

## Summary of Major Milestones

| Version | Key Feature |
|---------|-------------|
| v1.0 | Core download engine (Gofile, Pixeldrain, RD, Magnets) |
| v1.5 | Plex TV auto-sorting |
| v1.6 | Movie detection and dual-path sorting |
| v1.9 | Asian drama support and mission reports |
| v2.0 | Subtitle preservation |
| v2.1 | Retry logic and adaptive throttling |
| v2.3 | YouTube integration |
| v4.7 | Complete UI overhaul with progress bar |
| v4.15 | Smart dependency installation |
| v4.17 | Duplicate prevention and download archive |
| v4.19 | Security hardening (path traversal prevention) |
| v4.22 | Playlist range selection |
| v4.24 | Colab secrets integration and type hints |
| v4.25 | Parallel downloads and session resume |
| v4.27 | Queue management, file host routing, download history |
| v4.28 | YouTube playlist fix, international episode patterns |
| v4.29 | Playlist individual video tracking and resume fix |
| v4.30 | Trailing number episode detection for Chinese releases |
| v4.31 | Configurable download directories with folder browser |
| v4.32 | Critical fix for magnet link downloads |
| v4.33 | Optional auto-organization toggle, anime mode |
| v4.34 | Archive.org support |
| v5.0 | Quick Download, batch episode detection, fansub support |
| v5.1 | Code quality refactoring, download pipeline extraction |
| v5.2 | Queue sort, year field, MEGA & session persistence fixes |
| v5.3 | NNxNN episode detection, anti-idle keep-alive |
| v5.4 | Fixed Colab anti-idle, RD magnet rate limiting |
| v5.5 | FShare VIP support, OK.ru video support |
| v6.0 | TorBox debrid integration, security & reliability overhaul |

