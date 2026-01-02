# Ultimate Downloader Changelog

Comprehensive changelog documenting all changes from v1.0 to v4.29.

---

## v4.29 (Latest)
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
  - **🍪 Cookie Upload**: Upload cookies.txt directly from the UI for YouTube Premium/age-restricted content
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
- **Multi-Part Detection**: Recognizes Chinese multi-part suffixes (上篇, 中篇, 下篇) and Part 1/2
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
- **Asian Drama Episode Pattern**: Recognizes `Ep01`, `E01`, `Episode 01` formats (implies Season 1)
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

