# 🚀 Ultimate Downloader

A powerful Google Colab-based tool for downloading media from multiple sources directly to Google Drive with automatic Plex-friendly organization.

## ✨ Features

- **Multi-Source Downloads**: Gofile, Pixeldrain, Mega.nz, YouTube, Twitch, Vimeo, and more
- **35+ Premium Hosts via Real-Debrid**: MediaFire, 1fichier, Rapidgator, Nitroflare, etc.
- **Parallel Downloads**: Download up to 5 files concurrently (configurable)
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

Create a new Colab notebook and paste the entire contents of `ultimate_downloader_v4.27.py` into a cell, then run it.

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
| **Playlist Range** | Select specific items: `1,3,5-10` or leave empty for all |
| **Parallel DLs** | Number of concurrent downloads (1-5) |

### Drive Folders

Files are saved to these folders in your Google Drive:
- `My Drive/TV Shows/` - Detected TV episodes
- `My Drive/Movies/` - Detected movies
- `My Drive/YouTube/` - YouTube downloads without episode patterns
- `My Drive/Ultimate Downloader/` - Config files (session.json, yt_history.txt)

---

## 📺 Supported Sources

| Source | Features |
|--------|----------|
| **Gofile** | Public/private folders, cookie auth |
| **Pixeldrain** | Direct file downloads |
| **Mega.nz** | Full download support |
| **Real-Debrid** | Link unrestricting, magnet processing |
| **YouTube** | Videos, playlists, subtitles |
| **Twitch** | VODs and clips |
| **Vimeo** | Video downloads |
| **TikTok** | Video downloads |
| **Dailymotion** | Video downloads |
| **SoundCloud** | Audio downloads |

---

## 🎬 Episode Detection Patterns

The downloader recognizes these naming patterns:

| Pattern | Example | Result |
|---------|---------|--------|
| Standard | `Show.Name.S01E05.mkv` | Season 01, Episode 05 |
| Asian Drama | `Drama EP01.mkv` | Season 01, Episode 01 |
| Chinese | `电视剧 第5集.mkv` | Season 01, Episode 05 |
| Multi-Part | `Movie 上篇.mkv` | Adds `-pt1` suffix |

---

## 🍪 YouTube Authentication

For age-restricted or member-only content:

1. Export cookies from your browser (use a cookies.txt extension)
2. Upload to `/content/cookies.txt` in Colab
3. The downloader will auto-detect and use them

---

## 📋 Buttons

| Button | Action |
|--------|--------|
| **Start Download** | Download videos and organize to Drive |
| **Download Subtitles Only** | Fetch subtitles without downloading videos |
| **Resume Previous** | Resume interrupted session (appears when session exists) |

---

## 🔧 Troubleshooting

| Issue | Solution |
|-------|----------|
| "Gofile Error: error-notFound" | Link expired or requires authentication |
| "RD Timeout" | Torrent not cached, try a different magnet |
| "Mega Error" | Link invalid or requires login |
| Files not detected as TV | Use "Show Name" override field |
| YouTube age-restricted | Upload cookies.txt to /content/ |

---

## 📁 File Structure

```
Ultimate Downloader/
├── ultimate_downloader_v4.26.py   # Latest version
├── CHANGELOG.md                    # Version history
├── README.md                       # This file
├── LICENSE                         # CC BY-NC-ND 4.0
└── archive/                        # Previous versions
```

---

## 📜 License

This project is licensed under [CC BY-NC-ND 4.0](LICENSE).

- ✅ You may view and use this code for personal, non-commercial purposes
- ❌ You may not modify, distribute derivatives, or use commercially
- ✅ You must give appropriate credit to the author

---

## 🙏 Credits

Built with:
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) - Video downloading
- [aria2](https://aria2.github.io/) - Multi-connection downloads
- [megatools](https://megatools.megous.com/) - Mega.nz support
