![Ultimate Downloader](banner_2x1.png)

A powerful Google Colab-based tool for downloading media from multiple sources directly to Google Drive with automatic Plex-friendly organization.

## ✨ Features

- **Multi-Source Downloads**: Gofile, Pixeldrain, Mega.nz, YouTube, Twitch, Vimeo, and more
- **35+ Premium Hosts via Real-Debrid**: MediaFire, 1fichier, Rapidgator, Nitroflare, etc.
- **Parallel Downloads**: Download up to 5 files concurrently for Gofile, Pixeldrain, Real-Debrid, and direct HTTP links
- **Session Resume**: Automatically resume interrupted downloads after runtime restart
- **Queue Management**: Preview, reorder, and select which files to download
- **Download History**: Persistent log of completed downloads for debugging
- **Real-Debrid Integration**: Unrestrict premium links and process magnet links
- **Smart Media Sorting**: Automatically organizes into Plex-compatible folder structures
  - TV Shows: `Show Name/Season XX/Show Name - S01E01.mkv`
  - Movies: `Movie Name/Movie Name.mkv`
- **Archive Extraction**: Handles RAR, ZIP, 7Z with sequential extraction to save disk space
- **Subtitle Preservation**: Keeps `.srt`, `.ass`, `.sub`, `.vtt` files regardless of size
- **Duplicate Prevention**: Skips already-downloaded files across sessions
- **Progress Tracking**: Real-time progress bar with speed display

---

## 🚀 Quick Start

### 1. Open in Google Colab

Create a new Colab notebook and paste this one-liner into a cell:

```python
import requests; exec(requests.get("https://raw.githubusercontent.com/xersbtt/ultimate-downloader-colab/main/ultimate_downloader.py").text)
```

Run the cell and the UI will appear automatically.

### 2. Configure API Keys (Optional)

**Option A: Manual Entry**  
Enter your API keys directly in the UI fields when the widget appears.

**Option B: Colab Secrets (Recommended)**  
Store your keys securely in Colab Secrets:
1. Click the 🔑 key icon in Colab's left sidebar
2. Add secrets named `GOFILE_TOKEN` and `RD_TOKEN`
3. Keys will auto-populate on each run

### 3. Paste Your Links

Enter your download links in the text area (one per line):
```
https://gofile.io/d/abc123
https://pixeldrain.com/u/xyz789
magnet:?xt=urn:btih:...
https://www.youtube.com/playlist?list=...
```

### 4. Click "Start Download"

Files will be organized and saved to your Google Drive.

---

## ⚙️ Configuration Options

### UI Fields

| Field | Description |
|-------|-------------|
| **Gofile Token** | API token for private Gofile folders |
| **RD Token** | Real-Debrid API key for premium links/magnets |
| **Show Name** | Override auto-detected show name for all files |
| **Playlist Range** | Select specific YouTube playlist items: `1,3,5-10` or leave empty for all. **Note:** Ignored when downloading multiple playlist URLs (single videos are unaffected). |
| **Parallel DLs** | Number of concurrent downloads (1-5, applies to Gofile/Pixeldrain/RD/HTTP) |

### Drive Folders

Files are saved to these folders in your Google Drive:
- `My Drive/TV Shows/` - Files with detected episode patterns (S01E01, Ep 1, 第5集, etc.)
- `My Drive/Movies/` - Files without episode patterns (including downloads from Gofile, Pixeldrain, RD, etc.)
- `My Drive/YouTube/` - YouTube downloads without episode patterns
- `My Drive/Ultimate Downloader/` - Config files (session.json, history.json, yt_history.txt)

---

## 📺 Supported Sources

| Source | Features | Download Mode |
|--------|----------|---------------|
| **Gofile** | Public/private folders, cookie auth | Parallel |
| **Pixeldrain** | Direct file downloads | Parallel |
| **Real-Debrid** | Link unrestricting, magnet processing | Parallel |
| **Direct HTTP** | Any direct download URL | Parallel |
| **Mega.nz** | Full download support | Sequential |
| **YouTube** | Videos, playlists, subtitles | Sequential |
| **Twitch** | VODs and clips | Sequential |
| **Vimeo** | Video downloads | Sequential |
| **TikTok** | Video downloads | Sequential |
| **Dailymotion** | Video downloads | Sequential |
| **SoundCloud** | Audio downloads | Sequential |

---

## 🎬 Episode Detection Patterns

The downloader recognizes these naming patterns:

| Pattern | Example | Result |
|---------|---------|--------|
| Standard | `Show.Name.S01E05.mkv` | Season 01, Episode 05 |
| Asian Drama | `Drama EP01.mkv` | Season 01, Episode 01 |
| Chinese | `电视剧 第5集.mkv` | Season 01, Episode 05 |
| Vietnamese | `Phim Tập 3.mkv` | Season 01, Episode 03 |
| Korean | `드라마 5화.mkv` | Season 01, Episode 05 |
| German | `Serie Folge 2.mkv` | Season 01, Episode 02 |
| Spanish | `Serie Capitulo 4.mkv` | Season 01, Episode 04 |
| Pipe/Dash | `Show Name \| 7.mkv` | Season 01, Episode 07 |
| Asian Multi-Part | `Movie 上篇.mkv` | Adds `-pt1` suffix |

> **Tip**: When using "Show Name" override with playlists, the playlist position (1, 2, 3...) will be used as the episode number if no pattern matches.

---

## 🍪 YouTube Cookies (Experimental)

> ⚠️ **Warning**: Cookie authentication is experimental and may cause issues.

For Premium quality or members-only content:

1. Export `cookies.txt` from your browser (using a cookies.txt extension)
2. Click ⚙️ Settings → **📤 Upload Cookies**
3. Select your cookies file

**Known Issues:**
- Cookies may trigger "Requested format is not available" errors
- Session expiry or IP mismatch can cause authentication failures
- **Fix**: Click **🗑️ Clear Cookies** in Settings to remove problematic cookies

---

## 🔤 Subtitle Languages

When using the Queue Preview, you can select which subtitle languages to download:
- Default: English + Vietnamese
- Available: 12 languages (en, vi, zh, ja, ko, th, id, es, fr, de, pt, ru)
- Works for both video downloads and subtitles-only mode

---

## 📋 Buttons

| Button | Action |
|--------|--------|
| **Start Download** | Download videos and organize to Drive |
| **Download Subtitles Only** | Fetch subtitles from YouTube without downloading videos |
| **Resume Previous Session** | Resume interrupted session (appears when session exists) |
| **🔄 Restart Runtime** | Restart Colab runtime (appears after failures for easy resume) |
| **📜 (History)** | View last 10 downloads from history log |
| **⚙️ (Settings)** | Manage cookies, view API key status, clear data files |

---

## 🔧 Troubleshooting

| Issue | Solution |
|-------|----------|
| "Gofile Error: error-notFound" | Link expired or requires authentication |
| "RD Timeout" | Torrent not cached, try a different magnet |
| "Mega Error" | Link invalid or requires login |
| Files not detected as TV | Use "Show Name" override field |
| YouTube videos skipping | Clear YT archive via ⚙️ Settings |
| **"Requested format is not available"** | **yt-dlp auto-updates on each run. If persists, clear cookies via ⚙️ Settings** |

---

## 📁 File Structure

```
Ultimate Downloader/
├── ultimate_downloader.py          # Latest version (always current)
├── ultimate_downloader_v4.30.py    # Versioned snapshot
├── CHANGELOG.md                    # Version history
├── README.md                       # This file
├── LICENSE                         # MIT License
└── archive/                        # Previous versions
```

---

## 📜 License

This project is licensed under the [MIT License](LICENSE).

- ✅ Free to use, modify, and distribute
- ✅ Commercial use allowed
- ✅ Just include the copyright notice

---

## 🙏 Credits

Built with:
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) - Video downloading
- [aria2](https://aria2.github.io/) - Multi-connection downloads
- [megatools](https://megatools.megous.com/) - Mega.nz support
