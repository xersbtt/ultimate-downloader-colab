import os
import re
import json
import requests
import subprocess
import shutil
import time
from typing import Optional, Tuple, List, Dict, Any
from dataclasses import dataclass, field, asdict
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from uuid import uuid4
import ipywidgets as widgets
from IPython.display import display, clear_output
from urllib.parse import urlparse, unquote
from google.colab import drive

# --- COLAB SECRETS HELPER ---
def get_colab_secret(key: str, default: str = "") -> str:
    """Retrieve a secret from Colab secrets, return default if not found."""
    try:
        from google.colab import userdata
        return userdata.get(key)
    except (ImportError, ModuleNotFoundError):
        return default
    except Exception:
        return default

# --- CONFIGURATION ---
COLAB_ROOT = "/content/"
DRIVE_BASE = f"{COLAB_ROOT}drive/My Drive/"
UD_CONFIG_PATH = f"{DRIVE_BASE}Ultimate Downloader/"  # Config folder for session & history files
DRIVE_TV_PATH = "TV Shows"
DRIVE_MOVIE_PATH = "Movies"
DRIVE_YOUTUBE_PATH = "YouTube"
MIN_FILE_SIZE_MB = 10
KEEP_EXTENSIONS = {'.srt', '.ass', '.sub', '.vtt'}
SESSION_FILE = f"{UD_CONFIG_PATH}session.json"
HISTORY_FILE = f"{UD_CONFIG_PATH}history.json"
MAX_CONCURRENT_DEFAULT = 3

# Real-Debrid supported file hosts (route through RD when token available)
RD_SUPPORTED_HOSTS = {
    '1fichier.com', '4shared.com', 'alfafile.net', 'clicknupload.org', 'ddownload.com',
    'dailymotion.com', 'dropbox.com', 'filefactory.com', 'hexupload.net', 'hitfile.net',
    'k2s.cc', 'keep2share.cc', 'mediafire.com', 'mega.nz', 'mixdrop.co', 'nitroflare.com',
    'oboom.com', 'rapidgator.net', 'redtube.com', 'scribd.com', 'sendspace.com',
    'solidfiles.com', 'soundcloud.com', 'streamtape.com', 'turbobit.net', 'ulozto.net',
    'upload.ee', 'uploaded.net', 'uptobox.com', 'userscloud.com', 'vidoza.net',
    'vimeo.com', 'wetransfer.com', 'wipfiles.net', 'worldbytez.com', 'youporn.com',
}

# --- DOWNLOAD TASK DATACLASS ---
@dataclass
class DownloadTask:
    url: str  # Direct download URL (may be resolved API URL)
    filename: str
    source: str
    link_type: str  # gofile, pixeldrain, direct, youtube, mega, rd
    id: str = field(default_factory=lambda: str(uuid4()))  # Unique ID for tracking
    status: str = "pending"  # pending, downloading, done, failed, skipped
    error: Optional[str] = None
    cookie: Optional[str] = None
    original_url: Optional[str] = None  # Original user-provided URL (for re-resolving on resume)

# --- THREAD SAFETY ---
progress_lock = Lock()
active_downloads: Dict[str, str] = {}  # task_id -> status string
stop_monitor = False  # Flag to stop progress monitor thread

# --- UI ELEMENTS ---
token_gf = widgets.Text(description='Gofile:', placeholder='Optional (Required for private)', value=get_colab_secret('GOFILE_TOKEN'))
token_rd = widgets.Text(description='RD Token:', placeholder='Real-Debrid API Key', value=get_colab_secret('RD_TOKEN'))
show_name_override = widgets.Text(description='Show Name:', placeholder='Optional (Forces Name)', style={'description_width': 'initial'})
playlist_selection = widgets.Text(description='Playlist Range:', placeholder='e.g. 1,3,5-10 (Empty = All)', style={'description_width': 'initial'}, layout=widgets.Layout(width='250px'))
concurrent_slider = widgets.IntSlider(value=MAX_CONCURRENT_DEFAULT, min=1, max=5, description='Parallel DLs:', style={'description_width': 'initial'})

text_area = widgets.Textarea(description='Links:', placeholder='Paste Links Here (Transfer.it, Mega, YouTube, etc.)...', layout=widgets.Layout(width='98%', height='150px'))
btn = widgets.Button(description="Start Download", button_style='success', icon='download')
btn_subs = widgets.Button(description="Download Subtitles Only", button_style='info', icon='closed-captioning')
btn_resume = widgets.Button(description="Resume Previous", button_style='warning', icon='play', layout=widgets.Layout(display='none'))
btn_history = widgets.Button(description="📜", button_style='', tooltip='View Download History', layout=widgets.Layout(width='40px'))
progress_bar = widgets.FloatProgress(value=0.0, min=0.0, max=100.0, description='Idle', bar_style='info', layout=widgets.Layout(width='98%'))
status_label = widgets.HTML(value="")

# --- QUEUE MANAGEMENT UI ---
queue_list = widgets.SelectMultiple(options=[], description='Queue:', layout=widgets.Layout(width='98%', height='200px'))
btn_queue_up = widgets.Button(description="▲ Up", button_style='', layout=widgets.Layout(width='60px'))
btn_queue_down = widgets.Button(description="▼ Down", button_style='', layout=widgets.Layout(width='60px'))
btn_queue_select_all = widgets.Button(description="Select All", button_style='info', layout=widgets.Layout(width='80px'))
btn_queue_select_none = widgets.Button(description="None", button_style='', layout=widgets.Layout(width='60px'))
btn_queue_remove = widgets.Button(description="Remove", button_style='danger', layout=widgets.Layout(width='70px'))
btn_queue_start = widgets.Button(description="▶ Start Selected", button_style='success', layout=widgets.Layout(width='120px'))
btn_queue_cancel = widgets.Button(description="Cancel", button_style='warning', layout=widgets.Layout(width='70px'))

queue_controls = widgets.HBox([btn_queue_up, btn_queue_down, btn_queue_select_all, btn_queue_select_none, btn_queue_remove, btn_queue_start, btn_queue_cancel])
queue_ui = widgets.VBox([
    widgets.HTML("<b>📋 Queue Preview</b> <small>(Select items to manage)</small>"),
    queue_list,
    queue_controls
], layout=widgets.Layout(display='none'))  # Hidden by default

input_ui = widgets.VBox([
    widgets.HTML("<h3>🚀 Ultimate Downloader v4.28</h3>"),
    widgets.HBox([token_gf, token_rd]),
    widgets.HBox([show_name_override, playlist_selection, concurrent_slider]),
    text_area,
    widgets.HBox([btn, btn_subs, btn_resume, btn_history]),
    queue_ui,
    progress_bar,
    status_label,
    widgets.HTML("<hr>")
])

# --- SESSION MANAGEMENT ---
def load_session() -> Optional[Dict[str, Any]]:
    """Load previous session from Drive if it exists."""
    try:
        if os.path.exists(SESSION_FILE):
            with open(SESSION_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        print(f"⚠️ Could not load session: {e}")
    return None

def save_session(tasks: List[DownloadTask], gofile_token: str = "", rd_token: str = "", show_name: str = ""):
    """Persist current download state to Drive."""
    try:
        session = {
            "version": "4.28",
            "started_at": datetime.now().isoformat(),
            "gofile_token": gofile_token,
            "rd_token": rd_token,
            "show_name_override": show_name,
            "tasks": [asdict(t) for t in tasks]
        }
        with open(SESSION_FILE, 'w') as f:
            json.dump(session, f, indent=2)
    except Exception as e:
        print(f"⚠️ Could not save session: {e}")

def clear_session():
    """Delete session file after successful completion."""
    try:
        if os.path.exists(SESSION_FILE):
            os.remove(SESSION_FILE)
    except Exception:
        pass

def check_resume_available():
    """Show/hide resume button based on session file existence."""
    if os.path.exists(SESSION_FILE):
        btn_resume.layout.display = 'inline-flex'
    else:
        btn_resume.layout.display = 'none'

# --- DOWNLOAD HISTORY ---
def log_download(filename: str, source: str, size_mb: float, destination: str, status: str = "success"):
    """Append download to persistent history log for debugging."""
    try:
        history = []
        if os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE, 'r') as f:
                history = json.load(f)
        
        entry = {
            "timestamp": datetime.now().isoformat(),
            "filename": filename,
            "source": source,
            "size_mb": round(size_mb, 2),
            "destination": destination,
            "status": status
        }
        history.insert(0, entry)  # Newest first
        history = history[:500]   # Keep last 500 entries
        
        with open(HISTORY_FILE, 'w') as f:
            json.dump(history, f, indent=2)
    except Exception:
        pass  # Silent fail for logging

def view_history(b=None):
    """Open history file location in output."""
    if os.path.exists(HISTORY_FILE):
        print(f"📜 History file: {HISTORY_FILE}")
        print(f"   (Open in Google Drive to view)")
        try:
            with open(HISTORY_FILE, 'r') as f:
                history = json.load(f)
            print(f"\\n📊 Last 10 downloads (times in UTC):")
            for i, entry in enumerate(history[:10], 1):
                ts = entry.get('timestamp', '')[:16].replace('T', ' ')
                fn = entry.get('filename', 'Unknown')[:40]
                src = entry.get('source', '?')
                size = entry.get('size_mb', 0)
                print(f"   {i}. [{ts}] {fn} ({src}, {size:.1f}MB)")
        except Exception as e:
            print(f"   ⚠️ Could not read history: {e}")
    else:
        print("📜 No download history yet.")

# --- QUEUE MANAGEMENT ---
pending_queue: List[DownloadTask] = []  # Global queue state
queue_mode: str = ""  # "video" or "subs_only"

def update_queue_display():
    """Update the queue list widget with current pending_queue."""
    options = []
    for i, task in enumerate(pending_queue):
        source_icon = {"gofile": "📁", "pixeldrain": "💾", "rd": "⚡", "direct": "🔗", 
                       "youtube": "▶️", "mega": "☁️", "mediafire": "🔥", "1fichier": "📦"}.get(task.link_type, "📄")
        name = task.filename[:50] if task.filename else task.url[:50]
        options.append(f"{i+1}. {source_icon} {name}")
    queue_list.options = options
    queue_list.value = tuple(options)  # Select all by default

def show_queue_preview(tasks: List[DownloadTask], mode: str):
    """Show queue UI with resolved tasks."""
    global pending_queue, queue_mode
    pending_queue = tasks.copy()
    queue_mode = mode
    update_queue_display()
    queue_ui.layout.display = 'block'
    btn.disabled = True
    btn_subs.disabled = True
    print(f"📋 Queue loaded with {len(tasks)} items. Review and click 'Start Selected' to begin.")

def hide_queue():
    """Hide queue UI and reset state."""
    global pending_queue
    pending_queue = []
    queue_ui.layout.display = 'none'
    queue_list.options = []
    btn.disabled = False
    btn_subs.disabled = False

def queue_move_up(b=None):
    """Move selected items up in the queue."""
    global pending_queue
    selected = list(queue_list.value)
    if not selected:
        return
    indices = [int(s.split('.')[0]) - 1 for s in selected]
    indices.sort()
    for idx in indices:
        if idx > 0 and idx - 1 not in indices:
            pending_queue[idx], pending_queue[idx-1] = pending_queue[idx-1], pending_queue[idx]
    update_queue_display()
    # Re-select moved items
    new_selected = [queue_list.options[max(0, i-1)] for i in indices]
    queue_list.value = tuple(new_selected)

def queue_move_down(b=None):
    """Move selected items down in the queue."""
    global pending_queue
    selected = list(queue_list.value)
    if not selected:
        return
    indices = [int(s.split('.')[0]) - 1 for s in selected]
    indices.sort(reverse=True)
    for idx in indices:
        if idx < len(pending_queue) - 1 and idx + 1 not in indices:
            pending_queue[idx], pending_queue[idx+1] = pending_queue[idx+1], pending_queue[idx]
    update_queue_display()
    # Re-select moved items
    new_selected = [queue_list.options[min(len(pending_queue)-1, i+1)] for i in indices]
    queue_list.value = tuple(new_selected)

def queue_select_all(b=None):
    """Select all items in queue."""
    queue_list.value = tuple(queue_list.options)

def queue_select_none(b=None):
    """Deselect all items in queue."""
    queue_list.value = ()

def queue_remove_selected(b=None):
    """Remove selected items from queue."""
    global pending_queue
    selected = list(queue_list.value)
    if not selected:
        return
    indices_to_remove = {int(s.split('.')[0]) - 1 for s in selected}
    pending_queue = [t for i, t in enumerate(pending_queue) if i not in indices_to_remove]
    update_queue_display()
    if not pending_queue:
        hide_queue()
        print("📋 Queue is empty.")

def queue_cancel(b=None):
    """Cancel queue and return to link input."""
    hide_queue()
    print("❌ Queue cancelled.")

def start_from_queue(b=None):
    """Start downloading selected items from queue."""
    global pending_queue, queue_mode
    
    selected = list(queue_list.value)
    if not selected:
        print("⚠️ No items selected! Select items to download.")
        return
    
    # Get selected indices
    selected_indices = {int(s.split('.')[0]) - 1 for s in selected}
    selected_tasks = [t for i, t in enumerate(pending_queue) if i in selected_indices]
    
    if not selected_tasks:
        print("⚠️ No valid items selected!")
        return
    
    # Hide queue and start download
    hide_queue()
    print(f"🚀 Starting download of {len(selected_tasks)} selected items...")
    
    # Process the selected tasks
    execute_selected_tasks(selected_tasks, queue_mode)

# --- HELPER FUNCTIONS ---
def reset_progress():
    """Resets UI to idle state"""
    progress_bar.value = 0
    progress_bar.description = "Idle"
    progress_bar.bar_style = 'info'
    status_label.value = ""

def update_status(message: str):
    """Thread-safe status update."""
    with progress_lock:
        status_label.value = f"<small>{message}</small>"

def normalize_playlist_range(range_str):
    """Normalize playlist range string for yt-dlp's playlist_items option."""
    if not range_str or not range_str.strip():
        return None
    return range_str.replace(' ', '')

def sanitize_filename(name: str) -> str:
    name = unquote(name)
    name = re.sub(r'[<>:"/\\|?*]', '_', name) 
    name = re.sub(r'[\s_]+', ' ', name).strip()
    return name

def clean_show_name(name: str) -> str:
    name = re.sub(r'(?i)(?:\[?\s*(?:ENG\s*SUB|ENGSUB|FULL|WEB-?DL|WEBRip|BluRay|HDR|10bit|Atmos|DV|Vision|DDP\d\.\d|x265|HEVC|x264|H\.\d{3})\s*\]?)', '', name)
    name = re.sub(r'(?i)\b(2160p|1080p|720p|480p|4k|8k)\b', '', name)
    name = re.sub(r'[\[\]\(\)《》「」【】]', ' ', name)
    name = re.sub(r'[|._-]', ' ', name)
    name = re.sub(r'(?i)\s+\b(END|FINALE|FINAL)\b$', '', name)
    clean = re.sub(r'\s+', ' ', name).strip()
    return clean if clean else "Unknown Show"

def is_safe_path(base_dir: str, filename: str) -> bool:
    """Prevent directory traversal attacks with strict prefix checking"""
    try:
        target_path = os.path.realpath(os.path.join(base_dir, filename))
        base_path = os.path.realpath(base_dir)
        return target_path.startswith(base_path + os.sep) or target_path == base_path
    except Exception:
        return False

def check_duplicate_in_drive(filename: str, source: str = "generic", playlist_index: Optional[int] = None) -> bool:
    """Check if file already exists in Drive to avoid re-downloading"""
    dest_path, category = determine_destination_path(filename, source, dry_run=True, playlist_index=playlist_index)
    if os.path.exists(dest_path):
        file_size = os.path.getsize(dest_path) / (1024 * 1024)
        print(f"   ⏭️  SKIPPED (Already exists): {os.path.basename(dest_path)} ({file_size:.1f} MB)")
        return True
    return False

def determine_destination_path(filename: str, source: str = "generic", dry_run: bool = False, playlist_index: Optional[int] = None) -> Tuple[str, str]:
    filename = sanitize_filename(filename)
    part_suffix = ""
    if "上篇" in filename or re.search(r'(?i)(?:Part|Pt)\.?\s*1\b', filename): part_suffix = "-pt1"
    elif "下篇" in filename or re.search(r'(?i)(?:Part|Pt)\.?\s*2\b', filename): part_suffix = "-pt2"
    elif "中篇" in filename: part_suffix = "-pt2"

    manual_show_name = show_name_override.value.strip()
    show_name = "Unknown Show" 
    
    sxe_strict = re.search(r'(?i)\bS(\d{1,2})E(\d{1,2})\b', filename)
    # Added Vietnamese "Tập", Korean "화", and more flexible episode patterns
    sxe_loose = re.search(r'(?i)(?:\b(?:Ep?|Episode|Tập|Tập phim|Folge|Capitulo|Cap)[ .\-_]?(\d{1,3})\b|[|\-–—]\s*(?:Ep?|Episode|Tập)?\s*(\d{1,3})\s*[|\]]?)', filename)
    sxe_asian = re.search(r'(?:第(\d+)集|(\d+)화)', filename)

    season_num, episode_num = 1, 1
    is_tv = False
    episode_detected = False

    if sxe_strict:
        season_num, episode_num = int(sxe_strict.group(1)), int(sxe_strict.group(2))
        show_name = clean_show_name(filename[:sxe_strict.start()])
        is_tv = True
        episode_detected = True
    elif sxe_loose:
        # Handle multiple capture groups (first non-None wins)
        ep_num = sxe_loose.group(1) or sxe_loose.group(2)
        episode_num = int(ep_num) if ep_num else 1
        show_name = clean_show_name(filename[:sxe_loose.start()])
        if len(show_name) < 2: show_name = clean_show_name(os.path.splitext(filename[sxe_loose.end():])[0])
        is_tv = True
        episode_detected = True
    elif sxe_asian:
        # Handle multiple capture groups for Asian patterns
        ep_num = sxe_asian.group(1) or sxe_asian.group(2)
        episode_num = int(ep_num) if ep_num else 1
        show_name = clean_show_name(filename[:sxe_asian.start()])
        is_tv = True
        episode_detected = True
    
    if manual_show_name:
        show_name = manual_show_name
        is_tv = True
        # Use playlist_index as episode fallback when pattern detection fails
        if not episode_detected and playlist_index is not None:
            episode_num = playlist_index
    elif is_tv: pass
    else:
        year_match = re.search(r'\b(19|20)\d{2}\b', filename)
        if year_match: movie_name = clean_show_name(filename[:year_match.start()])
        elif source == "youtube":
            return os.path.join(f"{DRIVE_BASE}{DRIVE_YOUTUBE_PATH}", filename), "YouTube"
        else: movie_name = clean_show_name(os.path.splitext(filename)[0])
        full_dir = os.path.join(f"{DRIVE_BASE}{DRIVE_MOVIE_PATH}", movie_name)
        if not dry_run and not os.path.exists(full_dir): os.makedirs(full_dir, exist_ok=True)
        return os.path.join(full_dir, filename), "Movies"

    base_path = f"{DRIVE_BASE}{DRIVE_TV_PATH}"
    season_folder = f"Season {season_num:02d}"
    full_dir = os.path.join(base_path, show_name, season_folder)
    _, ext = os.path.splitext(filename)
    new_filename = f"{show_name} - S{season_num:02d}E{episode_num:02d}{part_suffix}{ext}"
    if not dry_run and not os.path.exists(full_dir): os.makedirs(full_dir, exist_ok=True)
    return os.path.join(full_dir, new_filename), "TV"

# --- CORE LOGIC ---
def setup_environment(needs_mega, needs_ytdlp, needs_aria):
    drive_path = f"{COLAB_ROOT}drive"
    if not os.path.exists(drive_path): drive.mount(drive_path)
    # Create media folders and config folder
    for p in [DRIVE_TV_PATH, DRIVE_MOVIE_PATH, DRIVE_YOUTUBE_PATH]:
        full_p = f"{DRIVE_BASE}{p}"
        if not os.path.exists(full_p): os.makedirs(full_p)
    if not os.path.exists(UD_CONFIG_PATH): os.makedirs(UD_CONFIG_PATH)
    
    if needs_ytdlp:
        try: import yt_dlp
        except ImportError:
            print("🛠️ Installing yt-dlp...")
            subprocess.run(["pip", "install", "yt-dlp"], check=True, stdout=subprocess.DEVNULL)
    else:
        print("⭐️ Skipping yt-dlp (Not needed)")

    pkg_map = {
        "unrar": "unrar", 
        "p7zip-full": "7z", 
        "megatools": "megadl", 
        "aria2": "aria2c", 
        "ffmpeg": "ffmpeg"
    }
    
    needed_pkgs = ["unrar", "p7zip-full"]
    if needs_mega: needed_pkgs.append("megatools")
    if needs_aria: needed_pkgs.append("aria2")
    if needs_ytdlp: needed_pkgs.append("ffmpeg")
    
    to_install = [pkg for pkg in needed_pkgs if not shutil.which(pkg_map[pkg])]

    if to_install:
        print(f"🛠️ Installing tools: {', '.join(to_install)}...")
        subprocess.run(["apt-get", "update", "-qq"], check=False)
        subprocess.run(["apt-get", "install", "-y"] + to_install, 
                       check=True, stdout=subprocess.DEVNULL)
    else:
        print("✅ Required tools already present.")
    
    check_resume_available()

def ytdl_hook(d):
    if d['status'] == 'downloading':
        try:
            p = d.get('_percent_str', '0%').replace('%','')
            speed = d.get('_speed_str', 'N/A')
            with progress_lock:
                progress_bar.value = float(p)
                progress_bar.description = f"YT: {p}% ({speed})"
        except Exception: pass
    elif d['status'] == 'finished':
        with progress_lock:
            progress_bar.value = 100
            progress_bar.description = "Done!"

def process_youtube_link(url, mode="video"):
    import yt_dlp
    print(f"   ▶️ Processing Video: {url}")
    with progress_lock:
        progress_bar.value = 0
        progress_bar.description = "Starting..."
    
    cookie_path = f"{COLAB_ROOT}cookies.txt"
    archive_path = f"{UD_CONFIG_PATH}yt_history.txt"
    playlist_items = normalize_playlist_range(playlist_selection.value)
    
    ydl_opts = {
        'outtmpl': f'{COLAB_ROOT}%(title)s.%(ext)s', 
        'quiet': True, 'no_warnings': True, 
        'restrictfilenames': False, 
        'ignoreerrors': True, 
        'writesubtitles': True, 
        'subtitleslangs': ['en.*', 'vi'], 
        'subtitlesformat': 'srt', 
        'progress_hooks': [ytdl_hook], 
        'noprogress': True,
        'download_archive': archive_path,
    }
    
    if playlist_items:
        ydl_opts['playlist_items'] = playlist_items
        print(f"   🎯 Playlist filter: {playlist_items}")
    
    if os.path.exists(cookie_path): 
        print(f"      🍪 Cookies detected! Using {cookie_path}")
        ydl_opts['cookiefile'] = cookie_path
    
    if mode == "video":
        ydl_opts['format'] = 'bestvideo+bestaudio/best'
        ydl_opts['merge_output_format'] = 'mkv'
    else:
        ydl_opts['skip_download'] = True
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try: 
                info = ydl.extract_info(url, download=False)
            except Exception as e:
                print(f"   ❌ YouTube Error: {str(e)[:100]}")
                return
            if not info: return 
            entries = list(info['entries']) if 'entries' in info else [info]
            
            total_items = len(entries)
            print(f"   📜 Processing {total_items} item(s)...")
            
            for i, entry in enumerate(entries, 1):
                if not entry: continue
                
                # For playlists, entries may have shallow metadata - extract full info per video
                video_url = entry.get('webpage_url') or entry.get('url') or entry.get('id')
                if not video_url:
                    print(f"      [{i}/{total_items}] ⚠️ Skipped: No valid URL found")
                    continue
                
                # If entry looks like shallow metadata (no formats), fetch full info
                if 'formats' not in entry and 'id' in entry:
                    try:
                        entry = ydl.extract_info(video_url, download=False) or entry
                    except Exception:
                        pass  # Fall back to shallow entry if extraction fails
                
                title = entry.get('title', 'Unknown')
                ext = 'mkv' if mode == "video" else 'srt'
                temp_filename = f"{title}.{ext}"
                if check_duplicate_in_drive(temp_filename, source="youtube", playlist_index=i):
                    continue
                
                print(f"      [{i}/{total_items}] Downloading: {title}")
                
                try:
                    before = set(os.listdir(COLAB_ROOT))
                    ydl.download([entry.get('webpage_url', entry.get('url'))])
                    after = set(os.listdir(COLAB_ROOT))
                    new_files = list(after - before)
                    
                    if not new_files: continue
                    for f in new_files:
                        if f.endswith(('.part', '.ytdl')): continue
                        handle_file_processing(os.path.join(COLAB_ROOT, f), source="youtube")
                except Exception as e:
                    print(f"      ❌ Failed to download {title}: {str(e)[:80]}")
    except Exception as e:
        print(f"   ❌ YouTube processing failed: {str(e)[:100]}")
    with progress_lock:
        progress_bar.description = "Idle"

def process_mega_link(url):
    print(f"   ☁️ Processing Mega: {url}")
    with progress_lock:
        progress_bar.description = "Mega DL..."
        progress_bar.value = 0
        progress_bar.bar_style = 'info'
    cmd = ['megadl', '--path', COLAB_ROOT, url]
    try:
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, universal_newlines=True)
        last_speed = ""
        for line in process.stdout:
            match = re.search(r'(\d+\.\d+)%', line)
            speed_match = re.search(r'(\d+\.?\d*\s*[KMG]B/s)', line)
            if match:
                try:
                    val = float(match.group(1))
                    speed_str = speed_match.group(1) if speed_match else last_speed
                    if speed_match: last_speed = speed_str
                    with progress_lock:
                        progress_bar.value = val
                        progress_bar.description = f"Mega: {int(val)}% ({speed_str})"
                except Exception: pass
        process.wait()
        if process.returncode == 0:
            print("   ✅ Mega Download Complete")
            with progress_lock:
                progress_bar.value = 100
            for f in os.listdir(COLAB_ROOT):
                if f not in ['sample_data', '.config', 'drive', 'temp_extract', 'cookies.txt']: 
                    handle_file_processing(os.path.join(COLAB_ROOT, f), source="mega")
        else: 
            print(f"   ❌ Mega Error (Code {process.returncode}) - Possible causes: Invalid link, auth required, or file not found")
    except Exception as e: 
        print(f"   ❌ Mega Execution Error: {e}")
    with progress_lock:
        progress_bar.bar_style = 'info'

def download_with_aria2(url: str, filename: str, dest_folder: str, cookie: Optional[str] = None, task_id: Optional[str] = None) -> Optional[str]:
    """Thread-safe aria2 download with progress tracking."""
    filename = sanitize_filename(filename)
    
    if check_duplicate_in_drive(filename):
        return None
    
    final_path = os.path.join(dest_folder, filename)
    if os.path.exists(final_path) and os.path.getsize(final_path) > 1024*1024: return final_path
    print(f"   ⬇️ Downloading: {filename}")
    
    with progress_lock:
        if task_id:
            active_downloads[task_id] = "starting"
    
    cmd = ['aria2c', url, '-d', dest_folder, '-o', filename, '-x', '16', '-s', '16', '-k', '1M', 
           '-c', '--file-allocation=none', '--user-agent', 'Mozilla/5.0', 
           '--connect-timeout=30', '--timeout=60', '--max-tries=3', '--retry-wait=2', '--console-log-level=warn']
    if cookie: cmd.extend(['--header', f'Cookie: accountToken={cookie}'])
    
    for attempt in range(1, 4):
        try:
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, universal_newlines=True)
            last_speed = ""
            for line in process.stdout:
                match = re.search(r'\((\d+)%\)', line)
                speed_match = re.search(r'DL:(\d+\.?\d*[KMG]iB/s)', line)
                if match:
                    try: 
                        val = float(match.group(1))
                        speed_str = speed_match.group(1) if speed_match else last_speed
                        if speed_match: last_speed = speed_str
                        with progress_lock:
                            if task_id:
                                active_downloads[task_id] = f"{int(val)}% ({speed_str})"
                    except Exception: pass
            process.wait()
            if process.returncode == 0 and os.path.exists(final_path): 
                with progress_lock:
                    if task_id:
                        active_downloads[task_id] = "done"
                return final_path
            else: 
                print(f"      ⚠️ Retry {attempt}/3 - Download incomplete")
                time.sleep(2**attempt)
        except Exception as e:
            print(f"      ❌ Download error (attempt {attempt}/3): {str(e)[:80]}")
            break
    
    print(f"   ❌ Download failed after 3 attempts - Check URL validity or network connection")
    with progress_lock:
        if task_id:
            active_downloads[task_id] = "failed"
    return None

def handle_file_processing(file_path, source="generic"):
    if not file_path or not os.path.exists(file_path): return
    filename = os.path.basename(file_path)
    _, ext = os.path.splitext(filename)

    if ext not in ['.rar', '.zip', '.7z']:
        processing_name = filename
        if ext == '.srt':
            parts = filename.split('.')
            if len(parts) >= 3 and len(parts[-2]) in [2, 3]: processing_name = ".".join(parts[:-2]) + ext
        
        final_dest, cat = determine_destination_path(processing_name, source)
        
        if ext == '.srt':
            parts = filename.split('.')
            lang = parts[-2] if len(parts) >= 3 and len(parts[-2]) in [2, 3] else ""
            base = os.path.splitext(final_dest)[0]
            final_dest = f"{base}.{lang}.srt" if lang else f"{base}.srt"
        if os.path.exists(final_dest): os.remove(final_dest)
        
        if not os.path.exists(os.path.dirname(final_dest)): os.makedirs(os.path.dirname(final_dest))
        size_mb = os.path.getsize(file_path) / (1024 * 1024)
        shutil.move(file_path, final_dest)
        print(f"   ✨ Moved to {cat}: {os.path.basename(final_dest)}")
        log_download(os.path.basename(final_dest), source, size_mb, final_dest)
        return

    print(f"   📦 Archive Detected: {filename}")
    extract_temp = f"{COLAB_ROOT}temp_extract"
    if os.path.exists(extract_temp): shutil.rmtree(extract_temp)
    os.makedirs(extract_temp)

    archive_files = []
    try:
        if '.rar' in ext:
            res = subprocess.run(['unrar', 'lb', file_path], capture_output=True, text=True)
            if res.returncode == 0: archive_files = res.stdout.strip().splitlines()
        else:
            res = subprocess.run(['7z', 'l', '-ba', '-slt', file_path], capture_output=True, text=True)
            if res.returncode == 0:
                for line in res.stdout.splitlines():
                    if line.strip().startswith('Path = '): archive_files.append(line.split(' = ')[1])
    except Exception as e:
        print(f"   ❌ Failed to read archive: {str(e)[:80]}")
        return
    
    total_files = len(archive_files)
    print(f"   📄 Extracting {total_files} files sequentially...")
    extracted_count = 0
    
    for f_path in archive_files:
        if f_path.endswith(('/', '\\')) or '__MACOSX' in f_path: continue
        
        if not is_safe_path(extract_temp, f_path):
            print(f"      ⚠️ SKIPPING UNSAFE PATH: {f_path}")
            continue

        extracted_count += 1
        with progress_lock:
            progress_bar.description = f"Extract: {extracted_count}/{total_files}"
            progress_bar.value = (extracted_count / total_files) * 100
        
        cmd = []
        if '.rar' in ext: cmd = ['unrar', 'x', '-o+', file_path, f_path, extract_temp]
        else: cmd = ['7z', 'x', '-y', file_path, f'-o{extract_temp}', f_path]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        extracted_full = os.path.join(extract_temp, f_path)
        if os.path.exists(extracted_full) and not os.path.isdir(extracted_full):
            if os.path.getsize(extracted_full) < MIN_FILE_SIZE_MB * 1024 * 1024 and not f_path.endswith(tuple(KEEP_EXTENSIONS)):
                os.remove(extracted_full); continue
            final_dest, cat = determine_destination_path(f_path, source)
            
            if os.path.exists(final_dest):
                print(f"      -> ⚠️ Duplicate in Drive (Deleted): {os.path.basename(final_dest)}")
                os.remove(extracted_full)
                continue

            if not os.path.exists(os.path.dirname(final_dest)): os.makedirs(os.path.dirname(final_dest))
            size_mb = os.path.getsize(extracted_full) / (1024 * 1024)
            shutil.move(extracted_full, final_dest)
            print(f"      [{extracted_count}/{total_files}] -> {os.path.basename(final_dest)}")
            log_download(os.path.basename(final_dest), source, size_mb, final_dest)
        
        if os.path.exists(extract_temp): shutil.rmtree(extract_temp, ignore_errors=True)
        os.makedirs(extract_temp)

    os.remove(file_path)
    if os.path.exists(extract_temp): shutil.rmtree(extract_temp)
    with progress_lock:
        progress_bar.description = "Idle"
    print(f"   ✅ Extraction complete: {extracted_count} files processed")

def get_gofile_session(token: Optional[str]) -> Tuple[requests.Session, dict]:
    s = requests.Session()
    s.headers.update({'User-Agent': 'Mozilla/5.0'})
    t = {'token': token, 'wt': "4fd6sg89d7s6"}
    if not token:
        try: 
            r = s.post("https://api.gofile.io/accounts", json={}, timeout=30)
            t['token'] = r.json()['data']['token'] if r.status_code == 200 else None
        except Exception: pass
    return s, t

def resolve_gofile(url, s, t) -> List[Tuple[str, str]]:
    try:
        match = re.search(r'gofile\.io/d/([a-zA-Z0-9]+)', url)
        if not match: return []
        r = s.get(f"https://api.gofile.io/contents/{match.group(1)}", 
                  params={'wt': t['wt']}, headers={'Authorization': f"Bearer {t['token']}"}, timeout=30)
        data = r.json()
        if data['status'] == 'ok': return [(c['link'], c['name']) for c in data['data']['children'].values() if c.get('link')]
        else:
            print(f"   ❌ Gofile Error: {data.get('status', 'unknown')} - Check if link is valid or requires authentication")
    except Exception as e:
        print(f"   ❌ Gofile API Error: {str(e)[:80]}")
    return [] 

def resolve_pixeldrain(url, s) -> List[Tuple[str, str]]:
    try:
        fid = re.search(r'pixeldrain\.com/u/([a-zA-Z0-9]+)', url).group(1)
        name = s.get(f"https://pixeldrain.com/api/file/{fid}/info", timeout=30).json().get('name', f"pixeldrain_{fid}")
        return [(f"https://pixeldrain.com/api/file/{fid}?download", sanitize_filename(name))]
    except Exception as e:
        print(f"   ❌ Pixeldrain Error: {str(e)[:80]} - File may not exist or be private")
    return []

def process_rd_link(link, key):
    h = {"Authorization": f"Bearer {key}"}
    if "magnet:?" in link:
        print("   🧲 Resolving Magnet...")
        try:
            r = requests.post("https://api.real-debrid.com/rest/1.0/torrents/addMagnet", data={"magnet": link}, headers=h, timeout=30).json()
            if 'error' in r:
                print(f"   ❌ RD Magnet Error: {r.get('error', 'Unknown')} - Check token or magnet validity")
                return
            requests.post(f"https://api.real-debrid.com/rest/1.0/torrents/selectFiles/{r['id']}", data={"files": "all"}, headers=h, timeout=30)
            for _ in range(30):
                i = requests.get(f"https://api.real-debrid.com/rest/1.0/torrents/info/{r['id']}", headers=h, timeout=30).json()
                if i['status'] == 'downloaded':
                    for l in i['links']:
                        process_rd_link(l, key)
                    return
                time.sleep(2)
            print("   ❌ RD Timeout - Torrent took too long to download")
        except Exception as e:
            print(f"   ❌ RD Magnet Error: {str(e)[:80]}")
        return
    try:
        d = requests.post("https://api.real-debrid.com/rest/1.0/unrestrict/link", data={"link": link}, headers=h, timeout=30).json()
        if 'error' in d:
            print(f"   ❌ RD Unrestrict Error: {d.get('error', 'Unknown')} - Check if link is supported")
            return
        f = download_with_aria2(d['download'], d['filename'], COLAB_ROOT)
        if f: handle_file_processing(f)
    except Exception as e:
        print(f"   ❌ RD Error: {str(e)[:80]}")

def resolve_rd_link(url: str, rd_key: str) -> List[Tuple[str, str]]:
    """Unrestrict a Real-Debrid link and return (download_url, filename) tuple."""
    if not rd_key:
        print(f"   ❌ RD Token required for: {url}")
        return []
    try:
        h = {"Authorization": f"Bearer {rd_key}"}
        d = requests.post("https://api.real-debrid.com/rest/1.0/unrestrict/link", 
                         data={"link": url}, headers=h, timeout=30).json()
        if 'error' in d:
            print(f"   ❌ RD Unrestrict Error: {d.get('error', 'Unknown')}")
            return []
        return [(d['download'], d['filename'])]
    except Exception as e:
        print(f"   ❌ RD Resolve Error: {str(e)[:80]}")
        return []

def resolve_mediafire(url: str, session: requests.Session) -> List[Tuple[str, str]]:
    """Resolve MediaFire link to direct download URL by parsing HTML."""
    try:
        resp = session.get(url, timeout=30)
        # Look for the download button href
        match = re.search(r'href="(https://download\d*\.mediafire\.com/[^"]+)"', resp.text)
        if match:
            download_url = match.group(1)
            # Extract filename from URL or page title
            filename_match = re.search(r'/([^/]+)$', download_url)
            if filename_match:
                filename = unquote(filename_match.group(1))
                print(f"   📁 MediaFire: {filename}")
                return [(download_url, sanitize_filename(filename))]
        # Try alternate pattern for older MediaFire pages
        match2 = re.search(r'aria-label="Download file"\s+href="([^"]+)"', resp.text)
        if match2:
            download_url = match2.group(1)
            filename = re.search(r'/([^/]+)$', download_url).group(1)
            return [(download_url, sanitize_filename(unquote(filename)))]
        print(f"   ⚠️ MediaFire: Could not find download link")
    except Exception as e:
        print(f"   ❌ MediaFire Error: {str(e)[:80]}")
    return []

def resolve_1fichier(url: str, session: requests.Session) -> List[Tuple[str, str]]:
    """Resolve 1fichier link to direct download URL."""
    try:
        # Get the page first to extract any needed info
        resp = session.get(url, timeout=30)
        
        # Extract filename from page
        filename_match = re.search(r'<title>([^<]+)</title>', resp.text)
        filename = "1fichier_download"
        if filename_match:
            title = filename_match.group(1)
            # Clean up title (remove "1fichier.com:" prefix if present)
            filename = re.sub(r'^.*?:\s*', '', title).strip()
            if not filename or filename == "1fichier.com":
                filename = "1fichier_download"
        
        # 1fichier requires a POST to download
        # Check if there's a waiting time (free downloads)
        if 'You must wait' in resp.text or 'Please wait' in resp.text:
            print(f"   ⚠️ 1fichier: Rate limited, try later or use premium")
            return []
        
        # Try to get the download link via POST
        # Note: 1fichier may require CAPTCHA for free downloads
        post_resp = session.post(url, data={'dl_no_ssl': 'on', 'dlinline': 'on'}, timeout=30, allow_redirects=False)
        
        if post_resp.status_code == 302:
            # Redirect to download URL
            download_url = post_resp.headers.get('Location', '')
            if download_url:
                print(f"   📁 1fichier: {filename}")
                return [(download_url, sanitize_filename(filename))]
        
        # Check response for direct link
        dl_match = re.search(r'href="(https://[^"]*1fichier[^"]*)"[^>]*>Click here', post_resp.text, re.IGNORECASE)
        if dl_match:
            return [(dl_match.group(1), sanitize_filename(filename))]
        
        print(f"   ⚠️ 1fichier: Could not extract download link (may require premium or CAPTCHA)")
    except Exception as e:
        print(f"   ❌ 1fichier Error: {str(e)[:80]}")
    return []

# --- PARALLEL DOWNLOAD WORKER ---
def download_worker(task: DownloadTask, gofile_token: str) -> DownloadTask:
    """Worker function for parallel downloads. Returns updated task."""
    task.status = "downloading"
    try:
        f = download_with_aria2(task.url, task.filename, COLAB_ROOT, task.cookie, task_id=task.id)
        if f:
            handle_file_processing(f, source=task.source)
            task.status = "done"
        else:
            task.status = "failed"
            task.error = "Download returned None"
    except Exception as e:
        task.status = "failed"
        task.error = str(e)[:100]
    return task

def resolve_all_links(urls: List[str], session: requests.Session, tokens: dict, rd_key: str) -> Tuple[List[DownloadTask], List[str], List[str], List[str]]:
    """
    Pre-resolve all links into DownloadTasks.
    Returns: (parallel_tasks, youtube_urls, mega_urls, rd_urls)
    """
    parallel_tasks: List[DownloadTask] = []
    youtube_urls: List[str] = []
    mega_urls: List[str] = []
    rd_urls: List[str] = []
    
    for url in urls:
        if "mega.nz" in url or "transfer.it" in url:
            mega_urls.append(url)
        elif any(h in url for h in ['youtube.com', 'youtu.be', 'vimeo.com', 'twitch.tv']):
            youtube_urls.append(url)
        elif "gofile.io" in url:
            resolved = resolve_gofile(url, session, tokens)
            for u, n in resolved:
                parallel_tasks.append(DownloadTask(
                    url=u, filename=n, source="gofile", link_type="gofile",
                    cookie=tokens.get('token'), original_url=url  # Store original for re-resolve
                ))
        elif "pixeldrain.com" in url:
            resolved = resolve_pixeldrain(url, session)
            for u, n in resolved:
                parallel_tasks.append(DownloadTask(
                    url=u, filename=n, source="pixeldrain", link_type="pixeldrain",
                    original_url=url  # Store original for re-resolve
                ))
        elif "mediafire.com" in url:
            # Prefer RD if available, fallback to direct resolve
            if rd_key:
                resolved = resolve_rd_link(url, rd_key)
                for u, n in resolved:
                    parallel_tasks.append(DownloadTask(
                        url=u, filename=n, source="mediafire", link_type="rd",
                        original_url=url
                    ))
            else:
                resolved = resolve_mediafire(url, session)
                for u, n in resolved:
                    parallel_tasks.append(DownloadTask(
                        url=u, filename=n, source="mediafire", link_type="mediafire",
                        original_url=url
                    ))
        elif "1fichier.com" in url:
            # Prefer RD if available, fallback to direct resolve
            if rd_key:
                resolved = resolve_rd_link(url, rd_key)
                for u, n in resolved:
                    parallel_tasks.append(DownloadTask(
                        url=u, filename=n, source="1fichier", link_type="rd",
                        original_url=url
                    ))
            else:
                resolved = resolve_1fichier(url, session)
                for u, n in resolved:
                    parallel_tasks.append(DownloadTask(
                        url=u, filename=n, source="1fichier", link_type="1fichier",
                        original_url=url
                    ))
        elif "magnet:?" in url:
            # Magnets stay sequential (need to wait for RD to cache)
            rd_urls.append(url)
        elif "real-debrid.com/d/" in url:
            # RD direct links can be parallelized
            resolved = resolve_rd_link(url, rd_key)
            for u, n in resolved:
                parallel_tasks.append(DownloadTask(
                    url=u, filename=n, source="rd", link_type="rd",
                    original_url=url  # Store original for re-resolve
                ))
        elif rd_key and any(host in url for host in RD_SUPPORTED_HOSTS):
            # Route through RD for any supported premium host
            resolved = resolve_rd_link(url, rd_key)
            for u, n in resolved:
                parallel_tasks.append(DownloadTask(
                    url=u, filename=n, source="rd_host", link_type="rd",
                    original_url=url
                ))
        elif rd_key and "http" in url:
            # Other links through RD - try unrestricting
            rd_urls.append(url)
        else:
            # Direct URL
            filename = os.path.basename(unquote(urlparse(url).path)) or "download"
            parallel_tasks.append(DownloadTask(
                url=url, filename=filename, source="direct", link_type="direct"
            ))
    
    return parallel_tasks, youtube_urls, mega_urls, rd_urls

def update_progress_display(tasks: List[DownloadTask]):
    """Update progress bar with parallel download status."""
    active = [t for t in tasks if t.status == "downloading"]
    done = sum(1 for t in tasks if t.status in ["done", "skipped"])
    total = len(tasks)
    
    # Calculate overall progress based on done tasks + partial progress of active tasks
    done_progress = (done / total) * 100 if total else 0
    
    # Add partial progress from active downloads
    active_progress = 0
    active_infos = []
    for t in active[:3]:
        status = active_downloads.get(t.id, "0%")
        active_infos.append(status)
        # Extract percentage from status like "45% (5.2MiB/s)"
        match = re.search(r'(\d+)%', status)
        if match:
            active_progress += float(match.group(1)) / total
    
    progress_bar.value = min(done_progress + active_progress, 100)
    progress_bar.bar_style = 'warning' if active else 'success' if done == total else 'info'
    
    if active:
        active_str = " | ".join(active_infos) if active_infos else "starting..."
        progress_bar.description = f"⚡ [{done}/{total}]"
        status_label.value = f"<small>🔄 <b>{len(active)} active:</b> {active_str}</small>"
    elif done == total:
        progress_bar.description = f"✅ Done [{done}/{total}]"
        status_label.value = ""
    else:
        progress_bar.description = f"DL [{done}/{total}]"

def progress_monitor(tasks: List[DownloadTask], interval: float = 0.5):
    """Background thread to update progress display periodically."""
    global stop_monitor
    while not stop_monitor:
        try:
            update_progress_display(tasks)
            time.sleep(interval)
        except Exception:
            pass

def execute_selected_tasks(selected_tasks: List[DownloadTask], mode: str):
    """Execute download for selected tasks from queue."""
    clear_output(wait=True)
    display(input_ui)
    btn.disabled = True
    btn_subs.disabled = True
    btn_resume.disabled = True
    
    try:
        gofile_token = token_gf.value.strip()
        rd_key = token_rd.value.strip()
        max_workers = concurrent_slider.value
        
        # Separate by type
        parallel_tasks = [t for t in selected_tasks if t.link_type in ['gofile', 'pixeldrain', 'direct', 'rd']]
        youtube_urls = [t.url for t in selected_tasks if t.link_type == 'youtube']
        mega_urls = [t.url for t in selected_tasks if t.link_type == 'mega']
        rd_urls = [t.url for t in selected_tasks if t.link_type == 'magnet']
        
        all_tasks = selected_tasks.copy()
        
        total_parallel = len(parallel_tasks)
        total_sequential = len(youtube_urls) + len(mega_urls) + len(rd_urls)
        print(f"📊 Starting: {total_parallel} parallel + {total_sequential} sequential\n")
        
        # --- PARALLEL DOWNLOADS ---
        if parallel_tasks:
            print(f"⚡ Starting {total_parallel} parallel downloads (max {max_workers} concurrent)...")
            
            global stop_monitor
            stop_monitor = False
            import threading
            monitor_thread = threading.Thread(target=progress_monitor, args=(parallel_tasks,), daemon=True)
            monitor_thread.start()
            
            try:
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    future_to_task = {
                        executor.submit(download_worker, task, gofile_token): task 
                        for task in parallel_tasks
                    }
                    
                    for future in as_completed(future_to_task):
                        task = future_to_task[future]
                        try:
                            result = future.result()
                            for i, t in enumerate(all_tasks):
                                if t.id == result.id:
                                    all_tasks[i] = result
                                    break
                            save_session(all_tasks, gofile_token, rd_key, show_name_override.value.strip())
                        except Exception as e:
                            print(f"   ❌ Task failed: {str(e)[:80]}")
            finally:
                stop_monitor = True
            
            print(f"✅ Parallel downloads complete!")
        
        # --- SEQUENTIAL DOWNLOADS ---
        if youtube_urls:
            print(f"\n▶️ Processing {len(youtube_urls)} YouTube links...")
            for i, url in enumerate(youtube_urls, 1):
                print(f"   [{i}/{len(youtube_urls)}] {url[:60]}...")
                process_youtube_link(url, mode)
        
        if mega_urls:
            print(f"\n☁️ Processing {len(mega_urls)} Mega links...")
            for i, url in enumerate(mega_urls, 1):
                print(f"   [{i}/{len(mega_urls)}] {url[:60]}...")
                process_mega_link(url)
        
        if rd_urls and rd_key:
            print(f"\n⚡ Processing {len(rd_urls)} RD Magnet links...")
            for i, url in enumerate(rd_urls, 1):
                print(f"   [{i}/{len(rd_urls)}] {url[:60]}...")
                process_rd_link(url, rd_key)
        
        # Summary
        done_count = sum(1 for t in all_tasks if t.status == 'done')
        failed_count = sum(1 for t in all_tasks if t.status == 'failed')
        
        if failed_count > 0:
            print(f"\n⚠️ Completed with {done_count} success, {failed_count} failed")
        else:
            print(f"\n✅ All {done_count} tasks completed successfully!")
            clear_session()
    
    except Exception as e:
        print(f"\n❌ Critical Error: {e}")
    finally:
        btn.disabled = False
        btn_subs.disabled = False
        btn_resume.disabled = False
        reset_progress()
        check_resume_available()


def execute_batch(mode: str, resume: bool = False):
    clear_output(wait=True)
    display(input_ui)
    btn.disabled = True
    btn_subs.disabled = True
    btn_resume.disabled = True
    print(f"\n🚀 Initializing... (Mode: {mode}, Resume: {resume})")
    
    try:
        gofile_token = token_gf.value.strip()
        rd_key = token_rd.value.strip()
        max_workers = concurrent_slider.value
        
        # Load from session or parse new URLs
        if resume:
            session_data = load_session()
            if not session_data:
                print("❌ No session to resume!")
                return
            
            gofile_token = session_data.get('gofile_token', gofile_token)
            rd_key = session_data.get('rd_token', rd_key)
            # Restore show name override from session
            saved_show_name = session_data.get('show_name_override', '')
            if saved_show_name:
                show_name_override.value = saved_show_name
                print(f"   🎬 Restored show name: {saved_show_name}")
            all_tasks = [DownloadTask(**t) for t in session_data.get('tasks', [])]
            
            # Filter to only pending/failed tasks
            pending_tasks = [t for t in all_tasks if t.status in ['pending', 'failed']]
            print(f"📂 Resuming {len(pending_tasks)} of {len(all_tasks)} tasks...")
            
            # Install required tools first
            needs_pixeldrain_gofile_rd = any(t.link_type in ['gofile', 'pixeldrain', 'rd'] for t in pending_tasks)
            needs_ytdlp = any(t.link_type == 'youtube' for t in pending_tasks)
            needs_mega = any(t.link_type == 'mega' for t in pending_tasks)
            needs_aria = any(t.link_type in ['gofile', 'pixeldrain', 'direct', 'rd'] for t in pending_tasks)
            setup_environment(needs_mega, needs_ytdlp, needs_aria)
            
            # Re-resolve Gofile/Pixeldrain/RD URLs to get fresh API tokens (bypasses IP rate limits)
            if needs_pixeldrain_gofile_rd:
                print("🔄 Re-resolving links with fresh session...")
                s, t = get_gofile_session(gofile_token)
                
                for task in pending_tasks:
                    if task.original_url and task.link_type in ['gofile', 'pixeldrain', 'rd']:
                        try:
                            if task.link_type == 'gofile':
                                resolved = resolve_gofile(task.original_url, s, t)
                                if resolved:
                                    task.url = resolved[0][0]  # Update with fresh API URL
                                    task.cookie = t.get('token')
                            elif task.link_type == 'pixeldrain':
                                resolved = resolve_pixeldrain(task.original_url, s)
                                if resolved:
                                    task.url = resolved[0][0]  # Update with fresh API URL
                            elif task.link_type == 'rd':
                                resolved = resolve_rd_link(task.original_url, rd_key)
                                if resolved:
                                    task.url = resolved[0][0]  # Update with fresh API URL
                        except Exception as e:
                            print(f"   ⚠️ Could not re-resolve {task.filename}: {e}")
            
            # Separate by type for processing
            parallel_tasks = [t for t in pending_tasks if t.link_type in ['gofile', 'pixeldrain', 'direct', 'rd']]
            youtube_urls = [t.url for t in pending_tasks if t.link_type == 'youtube']
            mega_urls = [t.url for t in pending_tasks if t.link_type == 'mega']
            rd_urls = [t.url for t in pending_tasks if t.link_type == 'magnet']  # Only magnets go sequential
        else:
            urls = [x.strip() for x in text_area.value.split('\n') if x.strip()]
            if not urls:
                print("❌ No links provided!")
                btn.disabled = False
                btn_subs.disabled = False
                return
            
            needs_ytdlp = any(h in u for u in urls for h in ['youtube.com', 'youtu.be', 'twitch.tv', 'tiktok.com', 'vimeo.com', 'dailymotion.com', 'soundcloud.com'])
            needs_mega = any("mega.nz" in u or "transfer.it" in u for u in urls)
            needs_aria = not (needs_ytdlp and not needs_mega) or any(h in u for u in urls for h in ["gofile.io", "pixeldrain.com", "magnet:", "real-debrid"])
            
            setup_environment(needs_mega, needs_ytdlp, needs_aria)
            
            s, t = get_gofile_session(gofile_token)
            
            print(f"🔍 Resolving {len(urls)} links...")
            parallel_tasks, youtube_urls, mega_urls, rd_urls = resolve_all_links(urls, s, t, rd_key)
            
            # Create session-compatible task list for saving
            all_tasks = parallel_tasks.copy()
            for url in youtube_urls:
                all_tasks.append(DownloadTask(url=url, filename="", source="youtube", link_type="youtube"))
            for url in mega_urls:
                all_tasks.append(DownloadTask(url=url, filename="", source="mega", link_type="mega"))
            for url in rd_urls:
                all_tasks.append(DownloadTask(url=url, filename="", source="rd", link_type="rd"))
            
            # Save initial session
            save_session(all_tasks, gofile_token, rd_key, show_name_override.value.strip())
            
            # Show queue preview instead of immediate download
            show_queue_preview(all_tasks, mode)
            return  # Wait for user to click "Start Selected"
        
        # This code only runs for RESUME mode (preview was skipped)
        total_parallel = len(parallel_tasks)
        total_sequential = len(youtube_urls) + len(mega_urls) + len(rd_urls)
        print(f"📊 Tasks: {total_parallel} parallel + {total_sequential} sequential\n")
        
        # --- PARALLEL DOWNLOADS ---
        if parallel_tasks:
            print(f"⚡ Starting {total_parallel} parallel downloads (max {max_workers} concurrent)...")
            
            # Start progress monitor thread
            global stop_monitor
            stop_monitor = False
            import threading
            monitor_thread = threading.Thread(target=progress_monitor, args=(parallel_tasks,), daemon=True)
            monitor_thread.start()
            
            try:
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    future_to_task = {
                        executor.submit(download_worker, task, gofile_token): task 
                        for task in parallel_tasks
                    }
                    
                    for future in as_completed(future_to_task):
                        task = future_to_task[future]
                        try:
                            result = future.result()
                            
                            # Update task in all_tasks and save session
                            for i, t in enumerate(all_tasks):
                                if t.id == result.id:
                                    all_tasks[i] = result
                                    break
                            save_session(all_tasks, gofile_token, rd_key, show_name_override.value.strip())
                            
                        except Exception as e:
                            print(f"   ❌ Task failed: {str(e)[:80]}")
                            task.status = "failed"
                            task.error = str(e)[:100]
            finally:
                # Stop progress monitor
                stop_monitor = True
                time.sleep(0.6)  # Let monitor thread exit
            
            # Final progress update
            update_progress_display(parallel_tasks)
            print(f"✅ Parallel downloads complete\n")
        
        # --- SEQUENTIAL DOWNLOADS (YouTube, Mega, RD) ---
        if youtube_urls:
            print(f"▶️ Processing {len(youtube_urls)} YouTube links...")
            for url in youtube_urls:
                process_youtube_link(url, mode)
                # Mark as done in session
                for t in all_tasks:
                    if t.url == url:
                        t.status = "done"
                        break
                save_session(all_tasks, gofile_token, rd_key)
        
        if mega_urls:
            print(f"☁️ Processing {len(mega_urls)} Mega links...")
            for url in mega_urls:
                process_mega_link(url)
                for t in all_tasks:
                    if t.url == url:
                        t.status = "done"
                        break
                save_session(all_tasks, gofile_token, rd_key)
        
        if rd_urls:
            print(f"🔓 Processing {len(rd_urls)} RD links...")
            for url in rd_urls:
                if rd_key:
                    process_rd_link(url, rd_key)
                else:
                    print("   ❌ RD Token Required for magnets/premium links")
                for t in all_tasks:
                    if t.url == url:
                        t.status = "done"
                        break
                save_session(all_tasks, gofile_token, rd_key)
        
        # Check for failures
        failed_count = sum(1 for t in all_tasks if t.status == "failed")
        done_count = sum(1 for t in all_tasks if t.status == "done")
        
        if failed_count > 0:
            print(f"\n⚠️ Completed with {failed_count} failures (session saved for retry)")
        else:
            print(f"\n✅ All {done_count} tasks completed successfully!")
            clear_session()
        
    except Exception as e: 
        print(f"\n❌ Critical Error: {e}")
    finally: 
        btn.disabled = False
        btn_subs.disabled = False
        btn_resume.disabled = False
        reset_progress()
        check_resume_available()

# --- BINDINGS ---
btn.on_click(lambda b: execute_batch("video"))
btn_subs.on_click(lambda b: execute_batch("subs_only"))
btn_resume.on_click(lambda b: execute_batch("video", resume=True))
btn_history.on_click(view_history)

# Queue control bindings
btn_queue_up.on_click(queue_move_up)
btn_queue_down.on_click(queue_move_down)
btn_queue_select_all.on_click(queue_select_all)
btn_queue_select_none.on_click(queue_select_none)
btn_queue_remove.on_click(queue_remove_selected)
btn_queue_cancel.on_click(queue_cancel)
btn_queue_start.on_click(lambda b: start_from_queue())

# --- INITIAL SETUP ---
def early_mount_drive():
    """Mount Drive on script load to enable session resume detection."""
    drive_path = f"{COLAB_ROOT}drive"
    if not os.path.exists(drive_path):
        try:
            print("📂 Mounting Google Drive for session detection...")
            drive.mount(drive_path)
        except Exception as e:
            print(f"⚠️ Could not mount Drive: {e}")
    check_resume_available()

# Mount drive and check for existing session on load
early_mount_drive()
display(input_ui)

