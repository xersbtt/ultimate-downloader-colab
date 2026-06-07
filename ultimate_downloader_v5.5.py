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
    except Exception as e:
        # This catches SecretNotFoundError and NotebookAccessError
        return default

def check_and_load_secrets():
    """Re-check secrets and populate fields if they were empty on initial load."""
    try:
        from google.colab import userdata
        # Try to load RD_TOKEN if field is empty
        if not token_rd.value:
            try:
                rd_val = userdata.get('RD_TOKEN')
                if rd_val:
                    token_rd.value = rd_val
                    print("🔑 RD_TOKEN loaded from Colab Secrets")
            except Exception:
                pass
        # Try to load GOFILE_TOKEN if field is empty
        if not token_gf.value:
            try:
                gf_val = userdata.get('GOFILE_TOKEN')
                if gf_val:
                    token_gf.value = gf_val
                    print("🔑 GOFILE_TOKEN loaded from Colab Secrets")
            except Exception:
                pass
        # Try to load FSHARE_EMAIL if field is empty
        if not token_fshare_email.value:
            try:
                fs_email = userdata.get('FSHARE_EMAIL')
                if fs_email:
                    token_fshare_email.value = fs_email
                    print("🔑 FSHARE_EMAIL loaded from Colab Secrets")
            except Exception:
                pass
        # Try to load FSHARE_PASSWORD if field is empty
        if not token_fshare_password.value:
            try:
                fs_pass = userdata.get('FSHARE_PASSWORD')
                if fs_pass:
                    token_fshare_password.value = fs_pass
                    print("🔑 FSHARE_PASSWORD loaded from Colab Secrets")
            except Exception:
                pass
    except (ImportError, ModuleNotFoundError):
        pass

# --- CONFIGURATION ---
COLAB_ROOT = "/content/"
DRIVE_BASE = f"{COLAB_ROOT}drive/My Drive/"
UD_CONFIG_PATH = f"{DRIVE_BASE}Ultimate Downloader/"  # Config folder for session & history files
DRIVE_TV_PATH = "TV Shows"
DRIVE_MOVIE_PATH = "Movies"
DRIVE_YOUTUBE_PATH = "YouTube"
DRIVE_DOWNLOADS_PATH = "Downloads"
DRIVE_ANIME_SERIES_PATH = "Anime Series"
DRIVE_ANIME_MOVIES_PATH = "Anime Movies"
MIN_FILE_SIZE_MB = 10
KEEP_EXTENSIONS = {'.srt', '.ass', '.sub', '.vtt'}
SESSION_FILE = f"{UD_CONFIG_PATH}session.json"
HISTORY_FILE = f"{UD_CONFIG_PATH}history.json"
SETTINGS_FILE = f"{UD_CONFIG_PATH}settings.json"
FSHARE_COOKIE_FILE = f"{UD_CONFIG_PATH}fshare_cookies.json"
COOKIE_PATH = f"{COLAB_ROOT}cookies.txt"
MAX_CONCURRENT_DEFAULT = 3

# API Configuration
REQUEST_TIMEOUT = 30  # Default timeout for HTTP requests
GOFILE_WEBSITE_TOKEN = "4fd6sg89d7s6"  # Website token for Gofile API - update if authentication fails

# Known resolution values (for filename parsing)
KNOWN_RESOLUTIONS = {360, 480, 540, 720, 1080, 1440, 2160, 4320}
YEAR_RANGE = range(1900, 2100)

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
    retry_count: int = 0  # Number of retry attempts

# --- THREAD SAFETY ---
progress_lock = Lock()
print_lock = Lock()  # Prevent interleaved print output from parallel threads
active_downloads: Dict[str, str] = {}  # task_id -> status string (e.g. "45% (5.2MiB/s)")
download_stats: Dict[str, Dict[str, Any]] = {}  # task_id -> {start_time, speed_bytes, last_update}
stop_monitor = False  # Flag to stop progress monitor thread
batch_start_time: Optional[float] = None  # Track when batch started for overall ETA
last_display_speed: float = 0.0  # Persist last known speed to prevent flickering

# --- COLAB KEEP-ALIVE ---
_keep_alive_stop = False
_rd_magnet_delay = 0  # Adaptive delay (seconds) between RD addMagnet calls; auto-set when rate-limited

def _keep_alive_worker(interval: int = 120):
    """Background thread: simulate Colab interaction to prevent idle timeout."""
    from IPython.display import display as ipy_display, Javascript
    global _keep_alive_stop
    while not _keep_alive_stop:
        try:
            ipy_display(Javascript('''
                (function() {
                    // Click the connect button to reset Colab's idle timer
                    var btn = document.querySelector("colab-connect-button");
                    if (btn) { btn.click(); }
                    console.log("Colab keep-alive: " + new Date().toLocaleTimeString());
                })();
            '''))
        except Exception:
            pass
        time.sleep(interval)

def start_keep_alive():
    """Start background keep-alive thread to prevent Colab idle disconnection."""
    global _keep_alive_stop
    _keep_alive_stop = False
    import threading
    t = threading.Thread(target=_keep_alive_worker, daemon=True)
    t.start()

def stop_keep_alive():
    """Stop the background keep-alive thread."""
    global _keep_alive_stop
    _keep_alive_stop = True

# --- UI ELEMENTS ---
token_gf = widgets.Text(description='Gofile:', placeholder='Optional', value=get_colab_secret('GOFILE_TOKEN'), style={'description_width': '80px'}, layout=widgets.Layout(width='270px'))
token_rd = widgets.Text(description='RD Token:', placeholder='Real-Debrid API Key', value=get_colab_secret('RD_TOKEN'), style={'description_width': '100px'}, layout=widgets.Layout(width='290px'))
token_fshare_email = widgets.Text(description='FShare:', placeholder='Email', value=get_colab_secret('FSHARE_EMAIL'), style={'description_width': '80px'}, layout=widgets.Layout(width='250px'))
token_fshare_password = widgets.Password(description='Password:', placeholder='FShare Password', value=get_colab_secret('FSHARE_PASSWORD'), style={'description_width': '80px'}, layout=widgets.Layout(width='250px'))
show_name_override = widgets.Text(description='Name:', placeholder='Optional (Forces folder/file name)', layout=widgets.Layout(width='280px'))
year_input = widgets.Text(description='Year:', placeholder='e.g. 2025', style={'description_width': '35px'}, layout=widgets.Layout(width='120px'))
media_type_toggle = widgets.ToggleButtons(
    options=['Movies/TV', 'Anime'],
    value='Movies/TV',
    description='',
    tooltips=['Organise to Movies and TV Shows folders', 'Organise to Anime Movies and Anime Series folders']
)
category_override = widgets.Dropdown(
    options=['Auto', 'Movie', 'Series'],
    value='Auto',
    description='Category:',
    tooltip='Auto: detect from filename. Movie/Series: force category regardless of filename pattern.',
    style={'description_width': '60px'},
    layout=widgets.Layout(width='140px')
)
playlist_selection = widgets.Text(description='Playlist:', placeholder='e.g. 1,3,5-10 (Empty=All)', style={'description_width': '60px'}, layout=widgets.Layout(width='220px'))
concurrent_slider = widgets.IntSlider(value=MAX_CONCURRENT_DEFAULT, min=1, max=5, description='Parallel DLs:', style={'description_width': '80px'})
# Auto-organisation checkbox for main UI
auto_organize_checkbox = widgets.Checkbox(value=True, description='Auto-organise', tooltip='Auto-rename and organise files. Uncheck to save with original filenames to Downloads.', indent=False, layout=widgets.Layout(width='130px'))

text_area = widgets.Textarea(description='Links:', placeholder='Paste Links Here (Transfer.it, Mega, YouTube, etc.)...', layout=widgets.Layout(width='98%', height='150px'))
btn = widgets.Button(description="Resolve Links", button_style='success', icon='search')
btn_quick = widgets.Button(description="Quick Download", button_style='primary', icon='bolt', tooltip='Download immediately without queue preview', layout=widgets.Layout(width='140px'))
btn_subs = widgets.Button(description="Download Subtitles", button_style='info', icon='closed-captioning', layout=widgets.Layout(width='150px'))
btn_resume = widgets.Button(description="Resume Previous Session", button_style='warning', icon='play', layout=widgets.Layout(display='none', width='180px'))
btn_restart = widgets.Button(description="🔄 Restart Runtime", button_style='danger', tooltip='Restart runtime then Resume Previous Session', layout=widgets.Layout(display='none'))
btn_history = widgets.Button(description="📜", button_style='', tooltip='View Download History', layout=widgets.Layout(width='40px'))
btn_settings = widgets.Button(description="⚙️", button_style='', tooltip='Settings & Manage Files', layout=widgets.Layout(width='40px'))
btn_about = widgets.Button(description="ℹ️", button_style='', tooltip='About', layout=widgets.Layout(width='40px'))
progress_bar = widgets.FloatProgress(value=0.0, min=0.0, max=100.0, description='Idle', bar_style='info', layout=widgets.Layout(width='98%'))
status_label = widgets.HTML(value="")

# --- SETTINGS/MANAGEMENT UI ---
btn_clear_history = widgets.Button(description="Clear Download History", button_style='warning', tooltip='Delete history.json', layout=widgets.Layout(width='180px'))
btn_clear_ytarchive = widgets.Button(description="Clear YT Archive", button_style='warning', tooltip='Delete yt_history.txt (allows re-downloading videos)', layout=widgets.Layout(width='150px'))
btn_clear_session = widgets.Button(description="Clear Session", button_style='danger', tooltip='Delete session.json', layout=widgets.Layout(width='120px'))
btn_settings_close = widgets.Button(description="Close", button_style='', layout=widgets.Layout(width='70px'))
settings_status = widgets.HTML(value="")

# Cookie UI (experimental)
btn_upload_cookies = widgets.Button(description="📤 Upload Cookies", button_style='info', tooltip='Upload cookies.txt for YouTube Premium (experimental)', layout=widgets.Layout(width='140px'))
btn_clear_cookies = widgets.Button(description="🗑️ Clear Cookies", button_style='warning', tooltip='Delete cookies.txt (fixes format errors)', layout=widgets.Layout(width='130px'))
cookie_status = widgets.HTML(value="")

# Quick Download subtitle settings
quick_dl_subs_checkbox = widgets.Checkbox(value=False, description='Include Subtitles in Quick Downloads', indent=False, layout=widgets.Layout(width='250px'))
quick_dl_subtitle_langs = widgets.SelectMultiple(
    options=[('English', 'en'), ('Vietnamese', 'vi'), ('Chinese', 'zh'), ('Japanese', 'ja'), ('Korean', 'ko'), 
             ('Thai', 'th'), ('Indonesian', 'id'), ('Spanish', 'es'), ('French', 'fr'), ('German', 'de'), ('Portuguese', 'pt'), ('Russian', 'ru')],
    value=['en', 'vi'],
    description='Languages:',
    layout=widgets.Layout(width='220px', height='80px'),
    style={'description_width': '70px'}
)

# Secrets status UI
secrets_status = widgets.HTML(value="")

# Confirmation UI elements
confirm_message = widgets.HTML(value="")
btn_confirm_yes = widgets.Button(description="Yes, Delete", button_style='danger', layout=widgets.Layout(width='100px'))
btn_confirm_cancel = widgets.Button(description="Cancel", button_style='', layout=widgets.Layout(width='80px'))
confirm_box = widgets.HBox([confirm_message, btn_confirm_yes, btn_confirm_cancel], 
                           layout=widgets.Layout(display='none', padding='5px', border='1px solid #f0ad4e', margin='5px 0'))

# Track which action is pending confirmation
pending_action = {'type': None}

# Directory configuration widgets with browse buttons
dir_tv_input = widgets.Text(value=DRIVE_TV_PATH, description='TV Shows:', layout=widgets.Layout(width='250px'), style={'description_width': '80px'})
dir_movie_input = widgets.Text(value=DRIVE_MOVIE_PATH, description='Movies:', layout=widgets.Layout(width='250px'), style={'description_width': '80px'})
dir_youtube_input = widgets.Text(value=DRIVE_YOUTUBE_PATH, description='YouTube:', layout=widgets.Layout(width='250px'), style={'description_width': '80px'})
dir_downloads_input = widgets.Text(value=DRIVE_DOWNLOADS_PATH, description='Downloads:', layout=widgets.Layout(width='250px'), style={'description_width': '80px'})
dir_anime_series_input = widgets.Text(value=DRIVE_ANIME_SERIES_PATH, description='Anime Series:', layout=widgets.Layout(width='250px'), style={'description_width': '80px'})
dir_anime_movies_input = widgets.Text(value=DRIVE_ANIME_MOVIES_PATH, description='Anime Movies:', layout=widgets.Layout(width='250px'), style={'description_width': '80px'})

btn_browse_tv = widgets.Button(description='📁', tooltip='Browse Drive folders', layout=widgets.Layout(width='35px'))
btn_browse_movie = widgets.Button(description='📁', tooltip='Browse Drive folders', layout=widgets.Layout(width='35px'))
btn_browse_youtube = widgets.Button(description='📁', tooltip='Browse Drive folders', layout=widgets.Layout(width='35px'))
btn_browse_downloads = widgets.Button(description='📁', tooltip='Browse Drive folders', layout=widgets.Layout(width='35px'))
btn_browse_anime_series = widgets.Button(description='📁', tooltip='Browse Drive folders', layout=widgets.Layout(width='35px'))
btn_browse_anime_movies = widgets.Button(description='📁', tooltip='Browse Drive folders', layout=widgets.Layout(width='35px'))

# Folder browser state
browser_state = {'current_path': '', 'target_widget': None, 'active': False}

# Browser UI widgets
browser_path_label = widgets.HTML("")
browser_folder_list = widgets.Select(options=[], description='', layout=widgets.Layout(width='320px', height='120px'))
btn_browser_up = widgets.Button(description='⬆️ Up', layout=widgets.Layout(width='60px'))
btn_browser_open = widgets.Button(description='� Open', layout=widgets.Layout(width='70px'))
btn_browser_select = widgets.Button(description='✓ Select', button_style='success', layout=widgets.Layout(width='70px'))
btn_browser_close = widgets.Button(description='✕', button_style='danger', layout=widgets.Layout(width='35px'))
new_folder_input = widgets.Text(placeholder='New folder name', layout=widgets.Layout(width='150px'))
btn_create_folder = widgets.Button(description='➕ Create', button_style='info', layout=widgets.Layout(width='90px'))

browser_ui = widgets.VBox([
    widgets.HBox([browser_path_label, btn_browser_close]),
    browser_folder_list,
    widgets.HBox([btn_browser_up, btn_browser_open, btn_browser_select]),
    widgets.HBox([new_folder_input, btn_create_folder])
], layout=widgets.Layout(display='none', border='1px solid #888', padding='5px', margin='5px 0'))

def get_folders_in_path(path):
    """Get list of folders in the given path."""
    folders = []
    try:
        full_path = os.path.join(DRIVE_BASE, path) if path else DRIVE_BASE
        for item in os.listdir(full_path):
            item_path = os.path.join(full_path, item)
            if os.path.isdir(item_path) and not item.startswith('.'):
                folders.append(item)
        folders.sort()
    except Exception:
        pass
    return folders

def update_browser_ui():
    """Update the browser UI with current path contents."""
    path = browser_state['current_path']
    display_path = f"📁 /{path}" if path else "📁 / (Drive Root)"
    browser_path_label.value = f"<b>{display_path}</b>"
    folders = get_folders_in_path(path)
    browser_folder_list.options = folders if folders else ['(empty)']
    browser_folder_list.value = folders[0] if folders else None

def open_browser(target_widget):
    """Open the folder browser for the given input widget."""
    def handler(b):
        # Check if Drive is mounted
        if not os.path.exists(DRIVE_BASE):
            dir_status.value = "<small style='color:orange'>⚠️ Mount Drive first (run a download)</small>"
            return
        browser_state['target_widget'] = target_widget
        browser_state['current_path'] = ''
        browser_state['active'] = True
        browser_ui.layout.display = 'block'
        update_browser_ui()
        dir_status.value = "<small>Navigate folders, then click ✓ Select</small>"
    return handler

def on_browser_up(b):
    """Navigate up one directory level."""
    path = browser_state['current_path']
    if path:
        parent = os.path.dirname(path)
        browser_state['current_path'] = parent
        update_browser_ui()

def on_browser_open(b):
    """Navigate into the selected folder."""
    selected = browser_folder_list.value
    if selected and selected != '(empty)':
        path = browser_state['current_path']
        new_path = os.path.join(path, selected) if path else selected
        browser_state['current_path'] = new_path
        update_browser_ui()

def on_browser_select(b):
    """Select the current folder or selected subfolder."""
    selected = browser_folder_list.value
    path = browser_state['current_path']
    # If a folder is selected, use that; otherwise use current path
    if selected and selected != '(empty)':
        final_path = os.path.join(path, selected) if path else selected
    else:
        final_path = path
    if browser_state['target_widget']:
        browser_state['target_widget'].value = final_path
    browser_ui.layout.display = 'none'
    browser_state['active'] = False
    dir_status.value = f"<small style='color:green'>✓ Set to: {final_path}</small>"

def on_browser_close(b):
    """Close the folder browser."""
    browser_ui.layout.display = 'none'
    browser_state['active'] = False
    dir_status.value = ""

def on_create_folder(b):
    """Create new folder in current browsing path."""
    folder_name = new_folder_input.value.strip()
    if not folder_name:
        dir_status.value = "<small style='color:orange'>⚠️ Enter a folder name</small>"
        return
    try:
        path = browser_state['current_path']
        base = os.path.join(DRIVE_BASE, path) if path else DRIVE_BASE
        new_path = os.path.join(base, folder_name)
        if os.path.exists(new_path):
            dir_status.value = f"<small style='color:orange'>⚠️ '{folder_name}' already exists</small>"
            return
        os.makedirs(new_path)
        new_folder_input.value = ""
        update_browser_ui()
        # Select the new folder
        browser_folder_list.value = folder_name
        dir_status.value = f"<small style='color:green'>✅ Created '{folder_name}'</small>"
    except Exception as e:
        dir_status.value = f"<small style='color:red'>❌ Error: {e}</small>"

btn_browse_tv.on_click(open_browser(dir_tv_input))
btn_browse_movie.on_click(open_browser(dir_movie_input))
btn_browse_youtube.on_click(open_browser(dir_youtube_input))
btn_browse_downloads.on_click(open_browser(dir_downloads_input))
btn_browse_anime_series.on_click(open_browser(dir_anime_series_input))
btn_browse_anime_movies.on_click(open_browser(dir_anime_movies_input))
btn_browser_up.on_click(on_browser_up)
btn_browser_open.on_click(on_browser_open)
btn_browser_select.on_click(on_browser_select)
btn_browser_close.on_click(on_browser_close)
btn_create_folder.on_click(on_create_folder)

# Organised folder config (shown when auto-organise is enabled)
organized_dir_config = widgets.VBox([
    widgets.HBox([dir_tv_input, btn_browse_tv]),
    widgets.HBox([dir_movie_input, btn_browse_movie]),
    widgets.HBox([dir_youtube_input, btn_browse_youtube]),
    widgets.HBox([dir_anime_series_input, btn_browse_anime_series]),
    widgets.HBox([dir_anime_movies_input, btn_browse_anime_movies]),
    browser_ui
])

# Simple downloads folder config (shown when auto-organise is disabled)
downloads_dir_config = widgets.VBox([
    widgets.HBox([dir_downloads_input, btn_browse_downloads]),
    browser_ui
], layout=widgets.Layout(display='none'))

dir_config_row = widgets.VBox([
    organized_dir_config,
    downloads_dir_config
])
dir_status = widgets.HTML("")

settings_buttons = widgets.HBox([btn_clear_history, btn_clear_ytarchive, btn_clear_session, btn_settings_close])
cookie_row = widgets.HBox([btn_upload_cookies, btn_clear_cookies, cookie_status])
api_keys_row = widgets.HBox([token_gf, token_rd])
fshare_keys_row = widgets.HBox([token_fshare_email, token_fshare_password])
settings_ui = widgets.VBox([
    widgets.HTML("<b>⚙️ Settings & File Management</b>"),
    widgets.HTML("<small><b>🔑 API Keys:</b></small>"),
    api_keys_row,
    widgets.HTML("<small><b>🇻🇳 FShare Account:</b></small>"),
    fshare_keys_row,
    secrets_status,
    widgets.HTML("<small><b>📁 Download Directories (relative to Google Drive):</b></small>"),
    dir_config_row,
    dir_status,
    widgets.HTML("<small><b>🍪 YouTube Cookies (Experimental):</b></small>"),
    cookie_row,
    widgets.HTML("<small><b>⚡ Quick Download Options:</b></small>"),
    widgets.HBox([quick_dl_subs_checkbox, quick_dl_subtitle_langs]),
    widgets.HTML("<small><b>🗑️ Clear Data:</b></small>"),
    settings_buttons,
    confirm_box,
    settings_status
], layout=widgets.Layout(display='none', padding='10px', border='1px solid #ccc', margin='5px 0'))

# --- ABOUT UI ---
btn_about_close = widgets.Button(description="Close", button_style='', layout=widgets.Layout(width='80px'))
about_ui = widgets.VBox([
    widgets.HTML("""
        <div style='padding: 10px;'>
            <h3>ℹ️ About Ultimate Downloader</h3>
            <p><strong>Version:</strong> 5.5</p>
            <p><strong>Author:</strong> xersbtt</p>
            <p><strong>Repository:</strong> <a href='https://github.com/xersbtt/ultimate-downloader-colab' target='_blank'>github.com/xersbtt/ultimate-downloader-colab</a></p>
            <hr>
            <p><strong>Copyright © 2025-2026 xersbtt</strong></p>
            <p><small>
                Permission is hereby granted, free of charge, to any person obtaining a copy
                of this software and associated documentation files (the "Software"), to deal
                in the Software without restriction, including without limitation the rights
                to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
                copies of the Software, and to permit persons to whom the Software is
                furnished to do so, subject to the following conditions:<br><br>
                The above copyright notice and this permission notice shall be included in all
                copies or substantial portions of the Software.<br><br>
                THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
                IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
                FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
            </small></p>
            <p><strong>Licence:</strong> MIT</p>
        </div>
    """),
    btn_about_close
], layout=widgets.Layout(display='none', padding='10px', border='1px solid #ccc', margin='5px 0'))

def toggle_about(b=None):
    """Toggle about panel visibility."""
    if about_ui.layout.display == 'none':
        about_ui.layout.display = 'block'
        settings_ui.layout.display = 'none'  # Close settings if open
    else:
        about_ui.layout.display = 'none'

btn_about.on_click(toggle_about)
btn_about_close.on_click(lambda b: setattr(about_ui.layout, 'display', 'none'))

# Helper functions to get current directory paths from widgets
def get_tv_path():
    """Get TV shows path from widget or default."""
    return dir_tv_input.value.strip() or DRIVE_TV_PATH

def get_movie_path():
    """Get movies path from widget or default."""
    return dir_movie_input.value.strip() or DRIVE_MOVIE_PATH

def get_youtube_path():
    """Get YouTube path from widget or default."""
    return dir_youtube_input.value.strip() or DRIVE_YOUTUBE_PATH

def get_downloads_path():
    """Get Downloads path from widget or default."""
    return dir_downloads_input.value.strip() or DRIVE_DOWNLOADS_PATH

def get_anime_series_path():
    """Get Anime Series path from widget or default."""
    return dir_anime_series_input.value.strip() or DRIVE_ANIME_SERIES_PATH

def get_anime_movies_path():
    """Get Anime Movies path from widget or default."""
    return dir_anime_movies_input.value.strip() or DRIVE_ANIME_MOVIES_PATH

def is_auto_organize_enabled():
    """Check if auto-organization is enabled."""
    return auto_organize_checkbox.value

def is_anime_mode_enabled():
    """Check if anime mode is enabled."""
    return media_type_toggle.value == 'Anime'

# --- SETTINGS PERSISTENCE ---
def save_dir_settings():
    """Save directory settings to settings.json."""
    try:
        if not os.path.exists(UD_CONFIG_PATH):
            os.makedirs(UD_CONFIG_PATH, exist_ok=True)
        settings = {
            'tv_path': dir_tv_input.value.strip(),
            'movie_path': dir_movie_input.value.strip(),
            'youtube_path': dir_youtube_input.value.strip(),
            'downloads_path': dir_downloads_input.value.strip(),
            'anime_series_path': dir_anime_series_input.value.strip(),
            'anime_movies_path': dir_anime_movies_input.value.strip(),
            'auto_organize': auto_organize_checkbox.value,
            'media_type': media_type_toggle.value,
            'category': category_override.value,
            'quick_dl_subs': quick_dl_subs_checkbox.value,
            'quick_dl_langs': list(quick_dl_subtitle_langs.value),
            'fshare_email': token_fshare_email.value.strip(),
            'fshare_password': token_fshare_password.value.strip()
        }
        with open(SETTINGS_FILE, 'w') as f:
            json.dump(settings, f)
    except Exception:
        pass  # Silently fail if Drive not mounted yet

def load_dir_settings(skip_ui_state=False):
    """Load directory settings from settings.json.
    
    Args:
        skip_ui_state: If True, skip loading UI state values (media_type, auto_organize)
                       to avoid overriding user's current selections.
    """
    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, 'r') as f:
                settings = json.load(f)
            if settings.get('tv_path'):
                dir_tv_input.value = settings['tv_path']
            if settings.get('movie_path'):
                dir_movie_input.value = settings['movie_path']
            if settings.get('youtube_path'):
                dir_youtube_input.value = settings['youtube_path']
            if settings.get('downloads_path'):
                dir_downloads_input.value = settings['downloads_path']
            if settings.get('anime_series_path'):
                dir_anime_series_input.value = settings['anime_series_path']
            if settings.get('anime_movies_path'):
                dir_anime_movies_input.value = settings['anime_movies_path']
            if settings.get('fshare_email'):
                token_fshare_email.value = settings['fshare_email']
            if settings.get('fshare_password'):
                token_fshare_password.value = settings['fshare_password']
            # Only load UI state on initial load, not when re-loading after Drive mount
            if not skip_ui_state:
                if 'auto_organize' in settings:
                    auto_organize_checkbox.value = settings['auto_organize']
                if settings.get('media_type'):
                    media_type_toggle.value = settings['media_type']
                if settings.get('category'):
                    category_override.value = settings['category']
                if 'quick_dl_subs' in settings:
                    quick_dl_subs_checkbox.value = settings['quick_dl_subs']
                if 'quick_dl_langs' in settings:
                    quick_dl_subtitle_langs.value = tuple(settings['quick_dl_langs'])
            update_main_ui_visibility()
    except Exception:
        pass  # Use defaults if file doesn't exist or is invalid

def update_folder_config_visibility():
    """Show/hide appropriate folder config based on auto-organise checkbox."""
    if auto_organize_checkbox.value:
        organized_dir_config.layout.display = 'block'
        downloads_dir_config.layout.display = 'none'
    else:
        organized_dir_config.layout.display = 'none'
        downloads_dir_config.layout.display = 'block'

def update_main_ui_visibility():
    """Show/hide Force Name and Media Type based on auto-organise checkbox."""
    if auto_organize_checkbox.value:
        organize_options_row.layout.display = 'flex'
    else:
        organize_options_row.layout.display = 'none'
    update_folder_config_visibility()

def on_auto_organize_change(change):
    """Handle auto-organise checkbox change."""
    if change['type'] == 'change' and change['name'] == 'value':
        update_main_ui_visibility()
        save_dir_settings()

# Auto-save when directory inputs change
def on_dir_change(change):
    """Save settings when any directory input changes."""
    if change['type'] == 'change' and change['name'] == 'value':
        save_dir_settings()

auto_organize_checkbox.observe(on_auto_organize_change, names='value')
dir_tv_input.observe(on_dir_change, names='value')
dir_movie_input.observe(on_dir_change, names='value')
dir_youtube_input.observe(on_dir_change, names='value')
dir_downloads_input.observe(on_dir_change, names='value')
dir_anime_series_input.observe(on_dir_change, names='value')
dir_anime_movies_input.observe(on_dir_change, names='value')
media_type_toggle.observe(on_dir_change, names='value')
category_override.observe(on_dir_change, names='value')
quick_dl_subs_checkbox.observe(on_dir_change, names='value')
quick_dl_subtitle_langs.observe(on_dir_change, names='value')
token_fshare_email.observe(on_dir_change, names='value')
token_fshare_password.observe(on_dir_change, names='value')

# Try to load settings on startup (will work if Drive already mounted)
load_dir_settings()


# --- QUEUE MANAGEMENT UI ---
queue_list = widgets.SelectMultiple(options=[], description='Queue:', layout=widgets.Layout(width='98%', height='200px'))
btn_queue_up = widgets.Button(description="▲ Up", button_style='', layout=widgets.Layout(width='60px'))
btn_queue_down = widgets.Button(description="▼ Down", button_style='', layout=widgets.Layout(width='60px'))
btn_queue_select_all = widgets.Button(description="Select All", button_style='info', layout=widgets.Layout(width='80px'))
btn_queue_select_none = widgets.Button(description="None", button_style='', layout=widgets.Layout(width='60px'))
btn_queue_remove = widgets.Button(description="Remove", button_style='danger', layout=widgets.Layout(width='70px'))
btn_queue_sort = widgets.Button(description="Sort A-Z", button_style='', icon='sort-alpha-asc', tooltip='Sort queue alphabetically by filename', layout=widgets.Layout(width='90px'))
queue_sort_ascending = True  # Track current sort direction
btn_queue_start = widgets.Button(description="▶ Start Download", button_style='success', layout=widgets.Layout(width='130px'))
btn_queue_start_subs = widgets.Button(description="📝 Download Subtitles", button_style='info', layout=widgets.Layout(width='170px', display='none'))  # Hidden by default
btn_queue_cancel = widgets.Button(description="Cancel", button_style='warning', layout=widgets.Layout(width='70px'))

# Subtitle language selector
subtitle_langs = widgets.SelectMultiple(
    options=[('English', 'en'), ('Vietnamese', 'vi'), ('Chinese', 'zh'), ('Japanese', 'ja'), 
             ('Korean', 'ko'), ('Thai', 'th'), ('Indonesian', 'id'), ('Spanish', 'es'), 
             ('French', 'fr'), ('German', 'de'), ('Portuguese', 'pt'), ('Russian', 'ru')],
    value=['en', 'vi'],
    description='Subtitles:',
    layout=widgets.Layout(width='200px', height='80px')
)

queue_controls = widgets.HBox([btn_queue_up, btn_queue_down, btn_queue_sort, btn_queue_select_all, btn_queue_select_none, btn_queue_remove, btn_queue_start, btn_queue_start_subs, btn_queue_cancel])
queue_options = widgets.HBox([subtitle_langs])  # Uses description for alignment like queue_list
# Playlist range selector (shown only for YouTube playlists)
playlist_options = widgets.HBox([
    widgets.HTML("<small><b>🎯 Playlist Range:</b></small>"),
    playlist_selection
], layout=widgets.Layout(display='none'))  # Hidden by default
queue_ui = widgets.VBox([
    widgets.HTML("<b>📋 Queue Preview</b> <small>(Select items to manage)</small>"),
    queue_list,
    playlist_options,
    queue_options,
    queue_controls
], layout=widgets.Layout(display='none'))  # Hidden by default

# Conditional row for organization options (shown when auto-organise is enabled)
# Initial display based on current checkbox value
organize_options_row = widgets.HBox([show_name_override, year_input, media_type_toggle, category_override], 
    layout=widgets.Layout(display='flex' if auto_organize_checkbox.value else 'none'))

input_ui = widgets.VBox([
    widgets.HTML("<h3>🚀 Ultimate Downloader v5.5</h3>"),
    widgets.HBox([auto_organize_checkbox]),
    organize_options_row,
    widgets.HBox([concurrent_slider]),
    text_area,
    widgets.HBox([btn, btn_quick, btn_resume, btn_restart, btn_history, btn_settings, btn_about]),
    settings_ui,
    about_ui,
    queue_ui,
    progress_bar,
    status_label,
    widgets.HTML("<hr>")
])

# Ensure UI visibility is correct after all widgets are created
update_main_ui_visibility()

# --- SESSION MANAGEMENT ---
# Cumulative YouTube download counters (persist across resume)
yt_success_cumulative = 0
yt_fail_cumulative = 0

def load_session() -> Optional[Dict[str, Any]]:
    """Load previous session from Drive if it exists."""
    try:
        if os.path.exists(SESSION_FILE):
            with open(SESSION_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        print(f"⚠️ Could not load session: {e}")
    return None

def save_session(
    tasks: List[DownloadTask],
    *,  # Force all following parameters to be keyword-only
    gofile_token: str = "",
    rd_token: str = "",
    show_name: str = "",
    year: str = "",
    playlist_range: str = "",
    yt_success: int = 0,
    yt_fail: int = 0,
    subtitle_langs_value: list = None
):
    """Persist current download state to Drive."""
    try:
        session = {
            "version": "5.5",
            "started_at": datetime.now().isoformat(),
            "gofile_token": gofile_token,
            "rd_token": rd_token,
            "show_name_override": show_name,
            "year": year,
            "playlist_range": playlist_range,
            "yt_success": yt_success,
            "yt_fail": yt_fail,
            "subtitle_langs": list(subtitle_langs_value) if subtitle_langs_value else ['en', 'vi'],
            "media_type": media_type_toggle.value,
            "category": category_override.value,
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

def check_secrets_status():
    """Check Colab secrets status and update display."""
    gf_status = "✅" if token_gf.value.strip() else "❌"
    rd_status = "✅" if token_rd.value.strip() else "❌"
    fs_status = "✅" if token_fshare_email.value.strip() and token_fshare_password.value.strip() else "❌"
    secrets_status.value = f"<span style='font-size:12px'>{gf_status} Gofile &nbsp; {rd_status} Real-Debrid &nbsp; {fs_status} FShare</span>"

def check_cookie_status():
    """Check if cookies.txt exists and update status display."""
    if os.path.exists(COOKIE_PATH):
        cookie_status.value = "<span style='color:green'>✅ Loaded</span>"
    else:
        cookie_status.value = "<span style='color:gray'>❌ None</span>"

def upload_cookies(b=None):
    """Upload cookies.txt file for YouTube authentication (experimental)."""
    try:
        from google.colab import files
        from IPython.display import clear_output
        settings_status.value = "<span style='color:blue'>📤 Select cookies.txt file...</span>"
        uploaded = files.upload()
        clear_output(wait=True)
        display(input_ui)
        if uploaded:
            for filename in uploaded.keys():
                shutil.move(filename, COOKIE_PATH)
                settings_status.value = f"<span style='color:green'>✅ Cookies uploaded from {filename}</span>"
                break
        else:
            settings_status.value = "<span style='color:gray'>Upload cancelled</span>"
        check_cookie_status()
    except ImportError:
        settings_status.value = "<span style='color:red'>❌ Cookie upload only works in Google Colab</span>"
    except Exception as e:
        settings_status.value = f"<span style='color:red'>❌ Upload failed: {str(e)[:40]}</span>"

def clear_cookies(b=None):
    """Delete cookies.txt file to fix authentication/format errors."""
    try:
        if os.path.exists(COOKIE_PATH):
            os.remove(COOKIE_PATH)
            settings_status.value = "<span style='color:green'>✅ Cookies cleared! Downloads will use anonymous access.</span>"
        else:
            settings_status.value = "<span style='color:gray'>ℹ️ No cookies file to clear.</span>"
        check_cookie_status()
    except Exception as e:
        settings_status.value = f"<span style='color:red'>❌ Error: {str(e)[:50]}</span>"

def toggle_settings(b=None):
    """Toggle settings panel visibility."""
    if settings_ui.layout.display == 'none':
        settings_ui.layout.display = 'block'
        settings_status.value = ""
        confirm_box.layout.display = 'none'
        pending_action['type'] = None
        # Refresh status indicators
        check_secrets_status()
        check_cookie_status()
    else:
        settings_ui.layout.display = 'none'

def close_settings(b=None):
    """Close settings panel."""
    settings_ui.layout.display = 'none'
    confirm_box.layout.display = 'none'
    pending_action['type'] = None

def restart_runtime(b=None):
    """Restart Colab runtime for fresh session."""
    try:
        from google.colab import runtime
        print("🔄 Restarting runtime... Use 'Resume Previous' after restart.")
        runtime.unassign()
    except ImportError:
        print("❌ Runtime restart only available in Google Colab")
    except Exception as e:
        print(f"❌ Could not restart: {e}")

def show_confirmation(action_type: str, message: str):
    """Show confirmation dialog for a pending action."""
    pending_action['type'] = action_type
    confirm_message.value = f"<span style='color:#856404'>⚠️ {message}</span>"
    confirm_box.layout.display = 'flex'
    settings_status.value = ""

def cancel_confirmation(b=None):
    """Cancel the pending confirmation."""
    pending_action['type'] = None
    confirm_box.layout.display = 'none'
    settings_status.value = "<span style='color:gray'>Cancelled.</span>"

def confirm_action(b=None):
    """Execute the confirmed action."""
    action = pending_action['type']
    pending_action['type'] = None
    confirm_box.layout.display = 'none'
    
    if action == 'history':
        _do_clear_history()
    elif action == 'ytarchive':
        _do_clear_ytarchive()
    elif action == 'session':
        _do_clear_session()

def request_clear_history(b=None):
    """Request confirmation to clear download history."""
    show_confirmation('history', "Delete download history? This action cannot be undone.")

def request_clear_ytarchive(b=None):
    """Request confirmation to clear YT archive."""
    show_confirmation('ytarchive', "Delete YT archive? This allows re-downloading previously downloaded videos.")

def request_clear_session(b=None):
    """Request confirmation to clear session."""
    show_confirmation('session', "Delete session file? This removes resume capability.")

def _do_clear_history():
    """Actually clear the download history file."""
    try:
        if os.path.exists(HISTORY_FILE):
            os.remove(HISTORY_FILE)
            settings_status.value = "<span style='color:green'>✅ Download history cleared!</span>"
        else:
            settings_status.value = "<span style='color:gray'>ℹ️ No history file to clear.</span>"
    except Exception as e:
        settings_status.value = f"<span style='color:red'>❌ Error: {str(e)[:50]}</span>"

def _do_clear_ytarchive():
    """Actually clear the yt-dlp download archive."""
    archive_path = f"{UD_CONFIG_PATH}yt_history.txt"
    try:
        if os.path.exists(archive_path):
            os.remove(archive_path)
            settings_status.value = "<span style='color:green'>✅ YT archive cleared! You can now re-download previous videos.</span>"
        else:
            settings_status.value = "<span style='color:gray'>ℹ️ No YT archive file to clear.</span>"
    except Exception as e:
        settings_status.value = f"<span style='color:red'>❌ Error: {str(e)[:50]}</span>"

def _do_clear_session():
    """Actually clear the session file."""
    try:
        if os.path.exists(SESSION_FILE):
            os.remove(SESSION_FILE)
            btn_resume.layout.display = 'none'
            settings_status.value = "<span style='color:green'>✅ Session cleared!</span>"
        else:
            settings_status.value = "<span style='color:gray'>ℹ️ No session file to clear.</span>"
    except Exception as e:
        settings_status.value = f"<span style='color:red'>❌ Error: {str(e)[:50]}</span>"



# --- QUEUE MANAGEMENT ---
pending_queue: List[DownloadTask] = []  # Global queue state
queue_mode: str = ""  # "video" or "subs_only"

def update_queue_display():
    """Update the queue list widget with current pending_queue."""
    options = []
    for i, task in enumerate(pending_queue):
        source_icon = {"gofile": "📁", "pixeldrain": "💾", "rd": "⚡", "direct": "🔗", 
                       "youtube": "▶️", "mega": "☁️", "mediafire": "🔥", "1fichier": "📦",
                       "magnet": "🧲", "magnet_file": "🧲", "archive": "📚",
                       "fshare": "🇻🇳", "okru": "🟠"}.get(task.link_type, "📄")
        name = task.filename[:50] if task.filename else task.url[:50]
        options.append(f"{i+1}. {source_icon} {name}")
    queue_list.options = options
    queue_list.value = tuple(options)  # Select all by default

def show_queue_preview(tasks: List[DownloadTask], mode: str):
    """Show queue UI with resolved tasks."""
    global pending_queue, queue_mode
    pending_queue = tasks.copy()
    queue_mode = mode
    
    # Run batch episode analysis to improve episode detection accuracy
    # This analyzes all filenames together to find the varying number (episode) vs constants
    filenames = [t.filename for t in tasks if t.filename]
    if len(filenames) >= 2:
        batch_results = analyze_batch_episodes(filenames)
        if batch_results:
            print(f"   🎯 Batch analysis detected episode numbers in {len(batch_results)} files")
    
    update_queue_display()
    
    # Hide subtitle and playlist options initially to prevent flash of old content
    queue_options.layout.display = 'none'
    playlist_options.layout.display = 'none'
    queue_ui.layout.display = 'block'
    
    # Check for YouTube/streaming links
    youtube_tasks = [t for t in tasks if t.link_type == 'youtube']
    has_streaming = len(youtube_tasks) > 0
    
    # Default full subtitle options
    DEFAULT_SUBS = [('English', 'en'), ('Vietnamese', 'vi'), ('Chinese', 'zh'), ('Japanese', 'ja'), 
                    ('Korean', 'ko'), ('Thai', 'th'), ('Indonesian', 'id'), ('Spanish', 'es'), 
                    ('French', 'fr'), ('German', 'de'), ('Portuguese', 'pt'), ('Russian', 'ru')]
    
    if has_streaming:
        # Check if any URL is a playlist OR there are multiple YouTube videos
        # After playlist expansion, check original_url for playlist detection
        has_playlist = any(
            ('list=' in (t.original_url or t.url) or '/playlist' in (t.original_url or t.url))
            for t in youtube_tasks
        )
        has_multiple_videos = len(youtube_tasks) > 1
        
        if has_playlist or has_multiple_videos:
            # For playlists or multiple videos, show full selector (can't efficiently check all)
            subtitle_langs.options = DEFAULT_SUBS
            subtitle_langs.value = ['en', 'vi']
            queue_options.layout.display = 'block'
            btn_queue_start_subs.layout.display = 'inline-block'
            if has_playlist:
                # Show playlist range selector for actual playlists (non-expanded)
                # Only show if there are unexpanded playlist URLs (original_url == url)
                has_unexpanded = any(
                    (t.original_url is None or t.original_url == t.url) and
                    ('list=' in t.url or '/playlist' in t.url)
                    for t in youtube_tasks
                )
                if has_unexpanded:
                    playlist_options.layout.display = 'flex'
                    print("📋 Playlist detected - use Playlist Range to select specific videos (e.g. 1,3,5-10)")
            else:
                print("📋 Multiple videos detected - full subtitle languages available")
        else:
            # For a single video only, fetch actual available subtitles
            print("🔍 Checking available subtitles...")
            available_subs = get_youtube_subtitles(youtube_tasks[0].url) if youtube_tasks else {}
            
            if available_subs:
                # Update subtitle selector with available languages
                subtitle_langs.options = [(name, code) for code, name in sorted(available_subs.items(), key=lambda x: x[1])]
                # Pre-select English and Vietnamese if available
                preselect = [code for code in ['en', 'vi', 'en-US', 'en-GB'] if code in available_subs]
                subtitle_langs.value = preselect[:2] if preselect else []
                queue_options.layout.display = 'block'
                btn_queue_start_subs.layout.display = 'inline-block'
                print(f"   ✓ Found {len(available_subs)} subtitle languages available")
            else:
                # No subtitles found - hide selector and button
                queue_options.layout.display = 'none'
                btn_queue_start_subs.layout.display = 'none'
                print("   ℹ️ No manual subtitles available for this video")
    else:
        queue_options.layout.display = 'none'
        btn_queue_start_subs.layout.display = 'none'
    
    btn.disabled = True
    print(f"📋 Queue loaded with {len(tasks)} items. Review and click 'Start Download' or 'Download Subtitles' to begin.")

def hide_queue():
    """Hide queue UI and reset state."""
    global pending_queue
    pending_queue = []
    queue_ui.layout.display = 'none'
    queue_list.options = []
    playlist_options.layout.display = 'none'
    btn_queue_start_subs.layout.display = 'none'
    btn.disabled = False

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

def queue_sort_alpha(b=None):
    """Sort queue items alphabetically by filename, toggling between A-Z and Z-A."""
    global pending_queue, queue_sort_ascending
    # Preserve selection: capture selected task IDs before sorting
    selected_ids = set()
    selected = list(queue_list.value)
    if selected:
        for s in selected:
            idx = int(s.split('.')[0]) - 1
            if 0 <= idx < len(pending_queue):
                selected_ids.add(pending_queue[idx].id)
    pending_queue.sort(key=lambda t: (t.filename or t.url).lower(), reverse=not queue_sort_ascending)
    update_queue_display()
    # Restore selection by matching task IDs to new positions
    if selected_ids:
        new_selected = [opt for i, opt in enumerate(queue_list.options)
                        if i < len(pending_queue) and pending_queue[i].id in selected_ids]
        queue_list.value = tuple(new_selected)
    # Toggle direction for next click
    queue_sort_ascending = not queue_sort_ascending
    if queue_sort_ascending:
        btn_queue_sort.description = "Sort A-Z"
        btn_queue_sort.icon = "sort-alpha-asc"
    else:
        btn_queue_sort.description = "Sort Z-A"
        btn_queue_sort.icon = "sort-alpha-desc"

def queue_cancel(b=None):
    """Cancel queue and return to link input."""
    hide_queue()
    print("❌ Queue cancelled.")

def start_from_queue(b=None, mode="video"):
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
    if mode == "subs_only":
        print(f"📝 Starting subtitle download of {len(selected_tasks)} selected items...")
    else:
        print(f"🚀 Starting download of {len(selected_tasks)} selected items...")
    
    # Process the selected tasks with the specified mode
    execute_selected_tasks(selected_tasks, mode)

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
    """Strip leading bracketed content that looks like technical tags (not show names).
    Technical tags: resolutions, codecs, release groups (usually single words or known patterns)
    Show names: usually contain spaces (multiple words)
    """
    # Keep stripping technical-looking brackets from the start
    while True:
        # Check if next bracket is a technical tag (no spaces inside, or matches known patterns)
        match = re.match(r'^\s*\[([^\]]*)\]', name)
        if not match:
            break
        content = match.group(1)
        # If bracket contains spaces, it's likely a show name - stop stripping
        if ' ' in content and not re.match(r'(?i)^(WEB-?DL|Dolby\s*Vision|10\s*bit)$', content):
            break
        # Strip this bracket
        name = name[match.end():]
    
    # Also strip leading parenthetical technical tags like (Hi10), (480p), (DragonFox)
    while True:
        match = re.match(r'^\s*\(([^)]*)\)', name)
        if not match:
            break
        content = match.group(1)
        # If parentheses contain spaces, might be show name - stop stripping
        if ' ' in content:
            break
        # Known technical patterns to strip
        if re.match(r'(?i)^(Hi10|10bit|x264|x265|HEVC|AVC|\d{3,4}p|WEB-?DL|BluRay|[A-Za-z0-9_-]{1,15})$', content):
            name = name[match.end():]
            continue
        # Unknown single word - stop to be safe
        break
    
    # Remove common YouTube prefixes (VIETSUB, ENGSUB, THUYẾT MINH, etc.)
    name = re.sub(r'(?i)^\s*(?:VIETSUB|VietSub|ENGSUB|EngSub|ENG\s*SUB|VIET\s*SUB|THUYẾT\s*MINH|RAW|FULL|HD)\s*[|｜:：\-–—]\s*', '', name)
    # Remove technical tags in brackets or standalone
    name = re.sub(r'(?i)(?:\[?\s*(?:ENG\s*SUB|ENGSUB|FULL|WEB-?DL|WEBRip|BluRay|HDR|10bit|Atmos|DV|Vision|DDP\d\.\d|x265|HEVC|x264|H\.\d{3})\s*\]?)', '', name)
    name = re.sub(r'(?i)\b(2160p|1080p|720p|480p|4k|8k)\b', '', name)
    name = re.sub(r'[\[\]\(\)《》「」【】]', ' ', name)
    # Remove trailing pipe/separator sections (e.g., "Show Name | Episode Info |" -> "Show Name")
    name = re.sub(r'\s*[|｜]\s*$', '', name)
    name = re.sub(r'[|｜._-]', ' ', name)
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

# --- BATCH EPISODE DETECTION ---
# Global cache for batch analysis results
_batch_episode_cache: Dict[str, int] = {}  # filename -> detected episode number

def analyze_batch_episodes(filenames: List[str]) -> Dict[str, int]:
    """
    Analyze a batch of filenames to detect episode numbers by finding varying patterns.
    
    Strategy: Find number patterns that vary sequentially across files (likely episodes)
    vs patterns that are constant (resolutions, codecs, etc).
    
    Returns dict mapping filename -> detected episode number (or None if not found).
    """
    global _batch_episode_cache
    _batch_episode_cache.clear()
    
    if len(filenames) < 2:
        return {}  # Need at least 2 files for batch analysis
    
    # Extract all bracketed numbers from each file with their positions
    def extract_bracket_numbers(filename: str) -> List[Tuple[int, int, str]]:
        """Returns list of (position_index, number_value, matched_text) for bracketed numbers."""
        results = []
        for i, m in enumerate(re.finditer(r'\[(\d{1,4})\]', filename)):
            num = int(m.group(1))
            results.append((i, num, m.group(0)))
        return results
    
    # Also extract dash-separated numbers like "Show - 01" or "Show_-_01_"
    def extract_dash_numbers(filename: str) -> List[Tuple[int, int, str]]:
        """Returns list of (position_index, number_value, matched_text) for dash-separated numbers."""
        results = []
        # Pattern handles: "- 01 ", "- 01.", "_-_01_", "- 01(", "- 0724 " (4-digit)
        # Negative lookahead (?![xX]\d) skips NNxNN patterns (e.g., "- 01x05")
        for i, m in enumerate(re.finditer(r'[-–—]_?(\d{1,4})(?![xX]\d)(?:[_\s\.(\[]|$)', filename)):
            num = int(m.group(1))
            results.append((i + 100, num, m.group(0)))  # offset to distinguish from brackets
        return results
    
    # Also extract space-separated numbers like "Show Name 01 Title" or "Slam Dunk 100"
    def extract_space_numbers(filename: str) -> List[Tuple[int, int, str]]:
        """Returns list of (position_index, number_value, matched_text) for space-separated episode numbers."""
        results = []
        # Match numbers that are surrounded by spaces (or start of string), followed by more text
        # Avoid matching years (1900-2099) or resolutions (1080, 720, etc.)
        # Accept [A-Za-z\[] after space to handle fansub bracket tags like [NetflixJP]
        for i, m in enumerate(re.finditer(r'(?:^|\s)(\d{1,4})(?=\s+[A-Za-z\[])', filename)):
            num = int(m.group(1))
            # Skip resolutions and other technical numbers
            if num in (360, 480, 540, 720, 1080, 1440, 2160, 4320):
                continue
            if 1900 <= num <= 2099:  # Skip years
                continue
            results.append((i + 200, num, m.group(0)))  # offset to distinguish
        return results
    
    # Extract NNxNN season-episode patterns like "01x05", "1x03", "02x15"
    def extract_nxn_numbers(filename: str) -> List[Tuple[int, int, str]]:
        """Returns list of (position_index, episode_value, matched_text) for NNxNN patterns."""
        results = []
        for i, m in enumerate(re.finditer(r'(?i)\b(\d{1,2})x(\d{1,4})\b', filename)):
            ep_num = int(m.group(2))  # Extract episode number (after x)
            results.append((i + 300, ep_num, m.group(0)))  # offset to distinguish
        return results
    
    # Pre-clean filenames to remove file size info like "(126.7 MiB)"
    def clean_for_analysis(filename: str) -> str:
        return re.sub(r'\s*\([^)]*[MG]i?B\s*\)\s*$', '', filename)
    
    # Collect patterns from all files
    all_patterns = []
    for fname in filenames:
        clean_name = clean_for_analysis(fname)
        patterns = extract_bracket_numbers(clean_name) + extract_dash_numbers(clean_name) + extract_space_numbers(clean_name) + extract_nxn_numbers(clean_name)
        all_patterns.append((fname, patterns))  # Keep original filename as key
    
    if not all_patterns or not all_patterns[0][1]:
        return {}  # No patterns found
    
    # Find which position index has varying values (likely episode numbers)
    # Group by position index
    position_values: Dict[int, List[Tuple[str, int]]] = {}
    for fname, patterns in all_patterns:
        for pos_idx, num_val, _ in patterns:
            if pos_idx not in position_values:
                position_values[pos_idx] = []
            position_values[pos_idx].append((fname, num_val))
    
    # Find positions where values vary AND form a reasonable sequence
    episode_position = None
    best_score = 0
    
    for pos_idx, file_nums in position_values.items():
        if len(file_nums) < len(filenames) * 0.8:
            continue  # Skip if not present in most files
        
        values = [n for _, n in file_nums]
        unique_values = set(values)
        
        # Skip if all same value (constant like 1080, 264, etc.)
        if len(unique_values) == 1:
            continue
        
        # Skip known non-episode numbers (resolutions, years, bit depths)
        if any(v in (360, 480, 540, 720, 1080, 1440, 2160, 4320, 264, 265, 10) for v in unique_values):
            if len(unique_values) == 1 or max(unique_values) > 500:
                continue
        
        # Check if values form a reasonable episode sequence
        sorted_vals = sorted(unique_values)
        is_sequential = all(sorted_vals[i+1] - sorted_vals[i] <= 2 for i in range(len(sorted_vals)-1))
        starts_low = min(unique_values) <= 10  # Episodes usually start from 1-10
        reasonable_range = max(unique_values) <= 500  # Episodes rarely exceed 500
        
        # Score this position
        score = 0
        if is_sequential: score += 3
        if starts_low: score += 2
        if reasonable_range: score += 1
        if len(unique_values) > 1: score += 1
        
        if score > best_score:
            best_score = score
            episode_position = pos_idx
    
    if episode_position is None:
        return {}
    
    # Map filenames to their episode numbers at the detected position
    result = {}
    for fname, patterns in all_patterns:
        for pos_idx, num_val, _ in patterns:
            if pos_idx == episode_position:
                result[fname] = num_val
                break
    
    _batch_episode_cache = result
    return result

def get_batch_episode(filename: str) -> Optional[int]:
    """Get batch-detected episode number for a filename, if available."""
    return _batch_episode_cache.get(filename)

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
    
    # If auto-organise is disabled, just return Downloads folder with original filename
    if not is_auto_organize_enabled():
        downloads_dir = os.path.join(DRIVE_BASE, get_downloads_path())
        if not dry_run and not os.path.exists(downloads_dir):
            os.makedirs(downloads_dir, exist_ok=True)
        return os.path.join(downloads_dir, filename), "Downloads"
    
    # CJK multi-part markers always apply (these genuinely split one episode into parts)
    part_suffix = ""
    if "上篇" in filename: part_suffix = "-pt1"
    elif "下篇" in filename: part_suffix = "-pt2"
    elif "中篇" in filename: part_suffix = "-pt2"
    # English "Part X" detected separately — only applied when no SxxExx/NxN pattern exists
    # (e.g. "S01E25 - The Real Folk Blues (Part 1)" → Part 2 is S01E26, not S01E25-pt2)
    english_part_suffix = ""
    if re.search(r'(?i)(?:Part|Pt)\.?\s*1\b', filename): english_part_suffix = "-pt1"
    elif re.search(r'(?i)(?:Part|Pt)\.?\s*2\b', filename): english_part_suffix = "-pt2"

    manual_show_name = show_name_override.value.strip()
    manual_year = year_input.value.strip()
    show_name = "Unknown Show" 
    
    sxe_strict = re.search(r'(?i)\bS(\d{1,2})E(\d{1,4})(?:v\d+)?\b', filename)
    # NNxNN pattern: matches 01x05, 1x03, 02x15, etc. (common TV naming convention)
    sxe_nxn = re.search(r'(?i)\b(\d{1,2})x(\d{1,4})\b', filename)
    # Added Vietnamese "Tập", Korean "화", Portuguese "Episodio", and more flexible episode patterns
    sxe_loose = re.search(r'(?i)(?:\b(?:Ep?|Episode|Episodio|Tập|Tập phim|Folge|Capitulo|Cap)[ .\-_]?(\d{1,4})\b|[|\-–—]\s*(?:Ep?|Episode|Tập)?\s*(\d{1,4})\s*[|\]]?)', filename)
    sxe_asian = re.search(r'(?:第(\d+)[集話]|(\d+)화)', filename)
    
    # Bracketed episode pattern: matches [01], [02], [0724], etc. common in fansub releases
    # Filters out resolution tags [1080P/720P/etc], codec tags [HEVC-10b/x265/etc], and source tags
    sxe_bracket = None
    bracket_matches = list(re.finditer(r'\[(\d{1,4})\]', filename))
    for bm in bracket_matches:
        num = int(bm.group(1))
        # Skip if it looks like a resolution (360, 480, 720, 1080, 2160, etc.) 
        # or a year (1900-2099) or a bit depth suffix like "10b" in [HEVC-10b]
        if num in (360, 480, 540, 720, 1080, 1440, 2160, 4320):
            continue
        if 1900 <= num <= 2099:
            continue
        # Check if this bracket is part of a codec tag like [HEVC-10b] or [x264-10bit]
        # Look for pattern where number is preceded by hyphen inside the bracket context
        bracket_content_before = filename[max(0, bm.start()-20):bm.start()]
        if re.search(r'\[[^\]]*-$', bracket_content_before):
            continue  # Skip, it's likely a suffix like -10b
        # This looks like a valid episode number
        sxe_bracket = bm
        break
    
    # Trailing number pattern: catches "HD 01", "Show Name 05", "filename - 03" before extension
    # Uses negative lookbehind to avoid matching years (19xx, 20xx) and resolutions (1080, 720, etc.)
    base_name = os.path.splitext(filename)[0]  # Remove extension for cleaner matching
    sxe_trailing = re.search(r'(?<![12]\d{2})(?<!x)\b(\d{1,4})\s*$', base_name)
    # Filter out likely years or resolutions captured by trailing pattern
    if sxe_trailing:
        num = int(sxe_trailing.group(1))
        # Reject if it looks like a year (1900-2099) or resolution (360, 480, 720, 1080, 2160, etc.)
        if 1900 <= num <= 2099 or num in (360, 480, 540, 720, 1080, 1440, 2160, 4320):
            sxe_trailing = None
    
    # Underscore-dash pattern: handles _-_01_ or - 1042 format (common in high-episode anime)
    # Negative lookahead (?![xX]\d) prevents matching the season part of NNxNN patterns
    sxe_underscore = re.search(r'[-–—]_?(\d{1,4})(?![xX]\d)(?:_|\(|\s|$)', filename)
    
    # Space-separated pattern: handles "Show Name 01 Title" or "Show Name 0724 [Tag]" format
    # Exclude "Part X" which indicates movie sequels, not episodes
    sxe_space = re.search(r'(?:^|[\s_])(\d{1,4})(?=\s+[A-Za-z\[])', filename)
    if sxe_space:
        num = int(sxe_space.group(1))
        # Check if preceded by "Part" - indicates movie, not episode
        pre_match = filename[:sxe_space.start() + 1]
        if re.search(r'(?i)\bPart\s*$', pre_match):
            sxe_space = None
        elif num in (360, 480, 540, 720, 1080, 1440, 2160, 4320) or 1900 <= num <= 2099:
            sxe_space = None

    season_num, episode_num = 1, 1
    is_tv = False
    episode_detected = False

    # PRIORITY 1: Use batch-detected episode if available (most reliable)
    batch_ep = get_batch_episode(filename)
    if batch_ep is not None:
        episode_num = batch_ep
        is_tv = True
        episode_detected = True
        # For show name, find the episode marker position
        # Handle: [01], " 01 ", "0724 [Tag]", "- 01", "_-_01_", "01x05" (NNxNN)
        # Try NNxNN pattern first (extracts both season and episode marker position)
        nxn_marker = re.search(r'(?i)\b(\d{1,2})x0*' + str(batch_ep) + r'\b', filename)
        if nxn_marker:
            season_num = int(nxn_marker.group(1))
            show_name = clean_show_name(filename[:nxn_marker.start()])
        else:
            ep_marker = re.search(r'\[0*' + str(batch_ep) + r'\]|(?:^|\s)0*' + str(batch_ep) + r'(?=\s+[A-Za-z\[])|[-–—]_?0*' + str(batch_ep) + r'(?:[_\s\.\(\[]|$)', filename)
            if ep_marker:
                show_name = clean_show_name(filename[:ep_marker.start()])
            else:
                show_name = clean_show_name(filename)
    
    # PRIORITY 2: Fall back to regex pattern matching if batch detection didn't find it
    if not episode_detected:
        # Collect all valid matches and find the earliest one to split correctly
        matches = []
        if sxe_strict: matches.append({'m': sxe_strict, 'type': 'strict', 'idx': sxe_strict.start(), 'priority': 1})
        if sxe_nxn: matches.append({'m': sxe_nxn, 'type': 'nxn', 'idx': sxe_nxn.start(), 'priority': 1})
        # Bracketed episode numbers have high priority (common in fansub releases)
        if sxe_bracket: matches.append({'m': sxe_bracket, 'type': 'bracket', 'idx': sxe_bracket.start(), 'priority': 1})
        if sxe_loose: matches.append({'m': sxe_loose, 'type': 'loose', 'idx': sxe_loose.start(), 'priority': 2})
        if sxe_asian: matches.append({'m': sxe_asian, 'type': 'asian', 'idx': sxe_asian.start(), 'priority': 2})
        # Underscore pattern: use if higher priority patterns not found
        if sxe_underscore and not matches: 
            matches.append({'m': sxe_underscore, 'type': 'underscore', 'idx': sxe_underscore.start(), 'priority': 3})
        # Space pattern: use if higher priority patterns not found
        if sxe_space and not matches: 
            matches.append({'m': sxe_space, 'type': 'space', 'idx': sxe_space.start(), 'priority': 3})
        # Trailing pattern is lowest priority - only use if no other patterns found
        if sxe_trailing and not matches: 
            matches.append({'m': sxe_trailing, 'type': 'trailing', 'idx': sxe_trailing.start(), 'priority': 4})
    
        if matches:
            # Sort by start index to find the FIRST occurrence (splitting show name from episode info)
            best = min(matches, key=lambda x: x['idx'])
            match, m_type = best['m'], best['type']
            
            if m_type == 'strict':
                season_num, episode_num = int(match.group(1)), int(match.group(2))
            elif m_type == 'nxn':
                season_num, episode_num = int(match.group(1)), int(match.group(2))
            elif m_type == 'bracket':
                episode_num = int(match.group(1))
            elif m_type == 'loose':
                ep_num = match.group(1) or match.group(2)
                episode_num = int(ep_num) if ep_num else 1
            elif m_type == 'asian':
                ep_num = match.group(1) or match.group(2)
                episode_num = int(ep_num) if ep_num else 1
            elif m_type == 'underscore':
                episode_num = int(match.group(1))
            elif m_type == 'space':
                episode_num = int(match.group(1))
            elif m_type == 'trailing':
                episode_num = int(match.group(1))
                
            # For trailing pattern, use base_name (without extension) for show name extraction
            name_source = base_name if m_type == 'trailing' else filename
            show_name = clean_show_name(name_source[:match.start()])
            # If show name is too short/empty, try looking after the match (rare case)
            if len(show_name) < 2 and m_type == 'loose': 
                parts = os.path.splitext(filename[match.end():])[0]
                if len(parts) > 2: show_name = clean_show_name(parts)
                
            is_tv = True
            episode_detected = True
    
    # Apply category override if set
    cat_override = category_override.value
    if cat_override == 'Movie':
        is_tv = False
        episode_detected = False  # Treat as movie, ignore detected episode
    elif cat_override == 'Series':
        is_tv = True
        if not episode_detected:
            episode_num = 1  # Default to E01 if no episode detected
    
    # Apply Force Name override - affects both TV shows and movies
    if manual_show_name:
        if is_tv or episode_detected:
            # TV show: use forced name as show name
            show_name = manual_show_name
            if not episode_detected and playlist_index is not None:
                episode_num = playlist_index
        else:
            # Movie: use forced name as folder/file name
            folder_name = f"{manual_show_name} ({manual_year})" if manual_year else manual_show_name
            _, ext = os.path.splitext(filename)
            new_filename = f"{manual_show_name}{ext}"
            if is_anime_mode_enabled():
                full_dir = os.path.join(f"{DRIVE_BASE}{get_anime_movies_path()}", folder_name)
                if not dry_run and not os.path.exists(full_dir): os.makedirs(full_dir, exist_ok=True)
                return os.path.join(full_dir, new_filename), "Anime Movies"
            else:
                full_dir = os.path.join(f"{DRIVE_BASE}{get_movie_path()}", folder_name)
                if not dry_run and not os.path.exists(full_dir): os.makedirs(full_dir, exist_ok=True)
                return os.path.join(full_dir, new_filename), "Movies"
    elif is_tv:
        pass  # Continue to TV show path generation below
    else:
        # No Force Name, not TV - detect movie
        year_match = re.search(r'\b(19|20)\d{2}\b', filename)
        if year_match:
            movie_name = clean_show_name(filename[:year_match.start()])
            year = year_match.group(0)
            folder_name = f"{movie_name} ({year})"
        elif source == "youtube":
            return os.path.join(f"{DRIVE_BASE}{get_youtube_path()}", filename), "YouTube"
        else:
            movie_name = clean_show_name(os.path.splitext(filename)[0])
            folder_name = movie_name
        _, ext = os.path.splitext(filename)
        new_filename = f"{folder_name}{ext}"
        # Use anime folder if anime mode is enabled
        if is_anime_mode_enabled():
            full_dir = os.path.join(f"{DRIVE_BASE}{get_anime_movies_path()}", folder_name)
            if not dry_run and not os.path.exists(full_dir): os.makedirs(full_dir, exist_ok=True)
            return os.path.join(full_dir, new_filename), "Anime Movies"
        else:
            full_dir = os.path.join(f"{DRIVE_BASE}{get_movie_path()}", folder_name)
            if not dry_run and not os.path.exists(full_dir): os.makedirs(full_dir, exist_ok=True)
            return os.path.join(full_dir, new_filename), "Movies"

    _, ext = os.path.splitext(filename)
    # Apply English part suffix only when no SxxExx/NxN pattern was detected
    # (SxxExx already uniquely identifies the episode — Part 2 is typically the next episode)
    if english_part_suffix and not sxe_strict and not sxe_nxn:
        part_suffix = part_suffix or english_part_suffix
    new_filename = f"{show_name} - S{season_num:02d}E{episode_num:02d}{part_suffix}{ext}"
    season_folder = "Specials" if season_num == 0 else f"Season {season_num:02d}"
    # Append year to show folder name only (file name stays without year)
    show_folder = f"{show_name} ({manual_year})" if manual_year else show_name
    
    # Use anime folder if anime mode is enabled
    if is_anime_mode_enabled():
        base_path = f"{DRIVE_BASE}{get_anime_series_path()}"
        full_dir = os.path.join(base_path, show_folder, season_folder)
        if not dry_run and not os.path.exists(full_dir): os.makedirs(full_dir, exist_ok=True)
        return os.path.join(full_dir, new_filename), "Anime Series"
    else:
        base_path = f"{DRIVE_BASE}{get_tv_path()}"
        full_dir = os.path.join(base_path, show_folder, season_folder)
        if not dry_run and not os.path.exists(full_dir): os.makedirs(full_dir, exist_ok=True)
        return os.path.join(full_dir, new_filename), "TV"

# --- CORE LOGIC ---
def setup_environment(needs_mega, needs_ytdlp, needs_aria):
    drive_path = f"{COLAB_ROOT}drive"
    if not os.path.exists(drive_path): drive.mount(drive_path)
    
    # Try to load secrets again (may not have been accessible on initial load)
    check_and_load_secrets()
    
    # Load saved directory settings from Drive (skip UI state to preserve user's current selection)
    load_dir_settings(skip_ui_state=True)
    
    # Create media folders and config folder
    for p in [get_tv_path(), get_movie_path(), get_youtube_path()]:
        full_p = f"{DRIVE_BASE}{p}"
        if not os.path.exists(full_p): os.makedirs(full_p)
    if not os.path.exists(UD_CONFIG_PATH): os.makedirs(UD_CONFIG_PATH)
    
    if needs_ytdlp:
        # Always upgrade yt-dlp to latest version (YouTube changes frequently)
        print("🛠️ Installing/updating yt-dlp...")
        subprocess.run(["pip", "install", "-U", "yt-dlp"], check=True, stdout=subprocess.DEVNULL)
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

def get_youtube_title(url: str) -> str:
    """Quickly fetch video/playlist title for queue display."""
    try:
        import yt_dlp
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': True,  # Don't download, just get metadata
            'skip_download': True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if info:
                title = info.get('title', '')
                # For playlists, show playlist name + count
                if info.get('_type') == 'playlist':
                    count = len(info.get('entries', []))
                    return f"📋 {title} ({count} videos)"
                return title[:60] + "..." if len(title) > 60 else title
    except Exception:
        pass
    return ""  # Fall back to showing URL

def resolve_youtube_playlist(url: str) -> List[DownloadTask]:
    """Expand a YouTube playlist URL into individual video DownloadTasks for queue display.
    For single videos, returns a single-element list.
    Uses extract_flat for fast metadata-only extraction.
    """
    try:
        import yt_dlp
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': True,  # Fast: get metadata without full extraction
            'skip_download': True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if info and info.get('_type') == 'playlist':
                entries = [e for e in info.get('entries', []) if e is not None]
                playlist_title = info.get('title', 'Playlist')
                tasks = []
                for i, entry in enumerate(entries, 1):
                    video_title = entry.get('title', f'Video {i}')
                    video_url = entry.get('url') or entry.get('webpage_url') or entry.get('id', '')
                    if video_url and not video_url.startswith('http'):
                        video_url = f"https://www.youtube.com/watch?v={video_url}"
                    display = f"[{i}/{len(entries)}] {video_title}"
                    if len(display) > 60:
                        display = display[:57] + "..."
                    tasks.append(DownloadTask(
                        url=video_url, filename=display,
                        source="youtube", link_type="youtube",
                        original_url=url  # Store original playlist URL
                    ))
                if tasks:
                    print(f"   📋 Expanded playlist '{playlist_title}': {len(tasks)} videos")
                return tasks
            elif info:
                # Single video — return as-is
                title = info.get('title', '')
                display = title[:60] + "..." if len(title) > 60 else title
                return [DownloadTask(
                    url=url, filename=display or url[:50] + "...",
                    source="youtube", link_type="youtube"
                )]
    except Exception as e:
        print(f"   ⚠️ Could not expand YouTube link: {e}")
    # Fallback: return single task with URL
    return [DownloadTask(url=url, filename=url[:50] + "...", source="youtube", link_type="youtube")]


def get_youtube_subtitles(url: str) -> dict:
    """Fetch available manual subtitles (not auto-generated) from YouTube video.
    Returns dict of {lang_code: lang_name} or empty dict if none available.
    """
    try:
        import yt_dlp
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'skip_download': True,
            'writesubtitles': True,
            'listsubtitles': False,  # We'll get them from info dict
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if info:
                # For playlists, get subs from first available entry
                if info.get('_type') == 'playlist':
                    entries = info.get('entries', [])
                    for entry in entries:
                        if entry and entry.get('subtitles'):
                            info = entry
                            break
                    else:
                        return {}
                
                # Get manual subtitles only (not automatic_captions)
                subtitles = info.get('subtitles', {})
                if not subtitles:
                    return {}
                
                # Build {code: name} dict
                result = {}
                for lang_code, formats in subtitles.items():
                    # Skip if it's a weird format or live_chat
                    if lang_code.startswith('live_chat'):
                        continue
                    # Get language name from first format or use code
                    lang_name = formats[0].get('name', lang_code) if formats else lang_code
                    # Clean up: "English" not "English - en"
                    if ' - ' in lang_name:
                        lang_name = lang_name.split(' - ')[0]
                    result[lang_code] = lang_name
                return result
    except Exception:
        pass
    return {}

def process_youtube_link(url, mode="video", apply_playlist_range=True) -> Tuple[int, int, int]:
    """Process YouTube link. Returns (success_count, fail_count, total_count).
    
    Args:
        url: YouTube URL to process
        mode: 'video' or 'subtitles'
        apply_playlist_range: If False, download all items (ignore playlist_selection)
    """
    import yt_dlp
    print(f"   ▶️ Processing Video: {url}")
    with progress_lock:
        progress_bar.value = 0
        progress_bar.description = "Starting..."
    
    success_count = 0
    fail_count = 0
    skip_count = 0
    
    archive_path = f"{UD_CONFIG_PATH}yt_history.txt"
    # Only apply playlist range if flag is True and user provided a range
    playlist_items = normalize_playlist_range(playlist_selection.value) if apply_playlist_range else None
    
    ydl_opts = {
        'outtmpl': {
            'default': f'{COLAB_ROOT}%(title)s.%(ext)s',
            'subtitle': f'{COLAB_ROOT}%(title)s.%(ext)s',  # Match video naming for subtitles
        },
        'quiet': True, 'no_warnings': True, 
        'restrictfilenames': False, 
        'ignoreerrors': True, 
        'writesubtitles': True, 
        'subtitleslangs': [f'{lang}.*' if lang == 'en' else lang for lang in subtitle_langs.value] or ['en'],  # Use selected languages 
        'subtitlesformat': 'srt', 
        'progress_hooks': [ytdl_hook], 
        'noprogress': True,
        'download_archive': archive_path,
    }
    
    if playlist_items:
        ydl_opts['playlist_items'] = playlist_items
        print(f"   🎯 Playlist filter: {playlist_items}")
    
    # Experimental: Use cookies if available (may cause issues - use Clear Cookies if errors occur)
    if os.path.exists(COOKIE_PATH): 
        print(f"      🍪 Cookies detected (experimental)")
        ydl_opts['cookiefile'] = COOKIE_PATH
    
    if mode == "video":
        # Best quality available (including 4K), with fallback to combined formats
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
                return (0, 1, 1)
            if not info: 
                return (0, 1, 1)
            
            # Get entries, filtering out None values (unavailable videos)
            if 'entries' in info:
                raw_entries = list(info['entries'])
                entries = [e for e in raw_entries if e is not None]
                none_count = len(raw_entries) - len(entries)
                if none_count > 0:
                    print(f"   ⚠️ {none_count} videos unavailable in playlist")
                    fail_count += none_count
            else:
                entries = [info]
            
            total_items = len(entries)
            print(f"   📜 Processing {total_items} item(s)...")
            
            for i, entry in enumerate(entries, 1):
                if not entry:
                    fail_count += 1
                    continue
                
                # For playlists, entries may have shallow metadata - extract full info per video
                video_url = entry.get('webpage_url') or entry.get('url') or entry.get('id')
                if not video_url:
                    print(f"      [{i}/{total_items}] ⚠️ Skipped: No valid URL found")
                    fail_count += 1
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
                    skip_count += 1
                    continue
                
                print(f"      [{i}/{total_items}] Downloading: {title}")
                
                try:
                    before = set(os.listdir(COLAB_ROOT))
                    ydl.download([entry.get('webpage_url', entry.get('url'))])
                    after = set(os.listdir(COLAB_ROOT))
                    new_files = list(after - before)
                    
                    if not new_files:
                        fail_count += 1
                        continue
                    for f in new_files:
                        if f.endswith(('.part', '.ytdl')): continue
                        handle_file_processing(os.path.join(COLAB_ROOT, f), source="youtube")
                    success_count += 1
                except Exception as e:
                    print(f"      ❌ Failed to download {title}: {str(e)[:80]}")
                    fail_count += 1
    except Exception as e:
        print(f"   ❌ YouTube processing failed: {str(e)[:100]}")
        return (success_count, fail_count + 1, success_count + fail_count + skip_count + 1)
    
    with progress_lock:
        progress_bar.description = "Idle"
    
    total = success_count + fail_count + skip_count
    return (success_count, fail_count, total)

def process_mega_link(url) -> bool:
    """Process Mega.nz download. Returns True on success, False on failure."""
    print(f"   ☁️ Processing Mega: {url}")
    with progress_lock:
        progress_bar.description = "Mega DL..."
        progress_bar.value = 0
        progress_bar.bar_style = 'info'
    # megadl can't handle /folder/.../file/... URLs — strip /file/ID to download entire folder
    dl_url = url
    folder_file_match = re.match(r'(https?://mega\.nz/folder/[^/]+#[^/]+)/file/.+', url)
    if folder_file_match:
        dl_url = folder_file_match.group(1)
        print(f"   📦 Folder/file link detected — downloading entire folder")
    
    cmd = ['megadl', '--path', COLAB_ROOT, dl_url]
    success = False
    # Snapshot files before download to detect silent failures
    skip_names = {'sample_data', '.config', 'drive', 'temp_extract', 'cookies.txt'}
    try:
        files_before = set(os.listdir(COLAB_ROOT))
    except Exception:
        files_before = set()
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
            # Verify files were actually downloaded (megadl can exit 0 for unsupported folder/file URLs)
            try:
                files_after = set(os.listdir(COLAB_ROOT))
            except Exception:
                files_after = set()
            new_files = [f for f in (files_after - files_before) if f not in skip_names]
            if new_files:
                print("   ✅ Mega Download Complete")
                with progress_lock:
                    progress_bar.value = 100
                for f in new_files:
                    handle_file_processing(os.path.join(COLAB_ROOT, f), source="mega")
                success = True
            else:
                print("   ❌ Mega: megadl exited OK but no files were downloaded")
                print("   💡 Tip: Folder/file links may not be supported by megadl. Try using Real-Debrid.")
        else: 
            print(f"   ❌ Mega Error (Code {process.returncode}) - Possible causes: Invalid link, auth required, or file not found")
    except Exception as e: 
        print(f"   ❌ Mega Execution Error: {e}")
    with progress_lock:
        progress_bar.bar_style = 'info'
    return success

def download_with_aria2(url: str, filename: str, dest_folder: str, cookie: Optional[str] = None, task_id: Optional[str] = None) -> Optional[str]:
    """Thread-safe aria2 download with progress tracking."""
    filename = sanitize_filename(filename)
    
    # Safeguard: if filename is empty, extract from URL
    if not filename or not filename.strip():
        filename = os.path.basename(unquote(urlparse(url).path)) or "download"
        filename = sanitize_filename(filename)
        if not filename or not filename.strip():
            filename = f"download_{int(time.time())}"
    
    if check_duplicate_in_drive(filename):
        return None
    
    final_path = os.path.join(dest_folder, filename)
    if os.path.exists(final_path) and os.path.getsize(final_path) > 1024*1024: return final_path
    with print_lock:
        print(f"   ⬇️ Downloading: {filename}")
    
    with progress_lock:
        if task_id:
            active_downloads[task_id] = "starting"
        progress_bar.value = 0
        progress_bar.bar_style = 'info'
        progress_bar.description = "Starting..."
    
    cmd = ['aria2c', url, '-d', dest_folder, '-o', filename, '-x', '16', '-s', '16', '-k', '1M', 
           '-c', '--file-allocation=none', '--user-agent', 'Mozilla/5.0', 
           '--connect-timeout=30', '--timeout=60', '--max-tries=3', '--retry-wait=2',
           '--summary-interval=1', '--show-console-readout=true']
    if cookie: cmd.extend(['--header', f'Cookie: accountToken={cookie}'])
    
    for attempt in range(1, 4):
        try:
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, universal_newlines=True)
            last_speed = ""
            for line in process.stdout:
                match = re.search(r'\((\d+)%\)', line)
                # aria2 outputs speed as "DL:5.2MiB" or "DL: 5.2MiB/s" - capture various formats
                speed_match = re.search(r'DL:\s*(\d+\.?\d*)\s*([KMG]i?B)', line)
                if match:
                    try: 
                        val = float(match.group(1))
                        if speed_match:
                            speed_str = f"{speed_match.group(1)}{speed_match.group(2)}/s"
                            last_speed = speed_str
                        else:
                            speed_str = last_speed
                        with progress_lock:
                            if task_id:
                                active_downloads[task_id] = f"{int(val)}% ({speed_str})"
                            # Also update progress bar directly for sequential downloads
                            progress_bar.value = val
                            progress_bar.description = f"DL: {int(val)}% ({speed_str})" if speed_str else f"DL: {int(val)}%"
                    except Exception: pass
            process.wait()
            file_exists = os.path.exists(final_path)
            if process.returncode == 0 and file_exists: 
                with progress_lock:
                    if task_id:
                        active_downloads[task_id] = "done"
                return final_path
            else:
                # Check if aria2 succeeded but saved with a different filename (common with URL-encoded names)
                if process.returncode == 0:
                    # Look for recently created files in dest folder matching the extension
                    _, ext = os.path.splitext(filename)
                    for f in os.listdir(dest_folder):
                        candidate = os.path.join(dest_folder, f)
                        if f.endswith(ext) and os.path.isfile(candidate):
                            # Check if this file was created in the last 60 seconds
                            if time.time() - os.path.getmtime(candidate) < 60:
                                with print_lock:
                                    print(f"      ✓ Found downloaded file: {f}")
                                with progress_lock:
                                    if task_id:
                                        active_downloads[task_id] = "done"
                                return candidate
                with print_lock:
                    if not os.path.exists(final_path):
                        print(f"      ⚠️ Retry {attempt}/3 - File not found at expected path")
                        print(f"         Expected: {final_path}")
                    else:
                        print(f"      ⚠️ Retry {attempt}/3 - aria2 returned code {process.returncode}")
                time.sleep(2**attempt)
        except Exception as e:
            with print_lock:
                print(f"      ❌ Download error (attempt {attempt}/3): {str(e)[:80]}")
            break
    
    with print_lock:
        print(f"   ❌ Download failed after 3 attempts - Check URL validity or network connection")
    with progress_lock:
        if task_id:
            active_downloads[task_id] = "failed"
    return None

def move_with_progress(src: str, dest: str):
    """Move a file, with progress output for large cross-filesystem transfers.
    
    When moving across filesystems (e.g. local disk → Google Drive FUSE),
    shutil.move does a full copy+delete. This wrapper uses buffered copy
    with periodic progress prints so the user can see the transfer happening.
    For same-filesystem moves, falls back to os.rename (instant).
    """
    try:
        # Try rename first (instant for same filesystem)
        os.rename(src, dest)
        return
    except OSError:
        pass  # Different filesystems — need to copy+delete
    
    file_size = os.path.getsize(src)
    size_mb = file_size / (1024 * 1024)
    
    # For small files (<100MB), just use shutil.move silently
    if size_mb < 100:
        shutil.move(src, dest)
        return
    
    # Large file: buffered copy with progress
    chunk_size = 8 * 1024 * 1024  # 8MB chunks
    copied = 0
    last_report = 0
    report_interval = 500 * 1024 * 1024  # Print every 500MB
    start_time = time.time()
    
    with print_lock:
        print(f"      📤 Transferring to Drive: {size_mb:.0f} MB...")
    
    try:
        with open(src, 'rb') as fsrc, open(dest, 'wb') as fdst:
            while True:
                buf = fsrc.read(chunk_size)
                if not buf:
                    break
                fdst.write(buf)
                copied += len(buf)
                
                if copied - last_report >= report_interval:
                    elapsed = time.time() - start_time
                    speed = (copied / (1024 * 1024)) / elapsed if elapsed > 0 else 0
                    pct = (copied / file_size) * 100
                    with print_lock:
                        print(f"         {pct:.0f}% ({copied / (1024*1024):.0f}/{size_mb:.0f} MB) @ {speed:.1f} MB/s")
                    last_report = copied
        
        elapsed = time.time() - start_time
        speed = size_mb / elapsed if elapsed > 0 else 0
        with print_lock:
            print(f"      ✅ Transfer complete: {size_mb:.0f} MB in {elapsed:.0f}s ({speed:.1f} MB/s)")
        
        os.remove(src)
    except Exception as e:
        # If copy failed, clean up partial destination and re-raise
        if os.path.exists(dest):
            try:
                os.remove(dest)
            except Exception:
                pass
        raise

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
        move_with_progress(file_path, final_dest)
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
            move_with_progress(extracted_full, final_dest)
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
    """Create authenticated Gofile session."""
    s = requests.Session()
    s.headers.update({'User-Agent': 'Mozilla/5.0'})
    t = {'token': token, 'wt': GOFILE_WEBSITE_TOKEN}
    if not token:
        try: 
            r = s.post("https://api.gofile.io/accounts", json={}, timeout=REQUEST_TIMEOUT)
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
    """Resolve Pixeldrain URL to direct download link."""
    try:
        match = re.search(r'pixeldrain\.com/u/([a-zA-Z0-9]+)', url)
        if not match:
            print(f"   ⚠️ Pixeldrain: Could not extract file ID from URL")
            return []
        fid = match.group(1)
        name = s.get(f"https://pixeldrain.com/api/file/{fid}/info", timeout=REQUEST_TIMEOUT).json().get('name', f"pixeldrain_{fid}")
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
            
            # Poll for torrent status with progress updates
            with progress_lock:
                progress_bar.value = 0
                progress_bar.bar_style = 'info'
                progress_bar.description = "RD: Caching..."
            
            for poll_count in range(60):  # 2 minutes max (60 * 2s)
                i = requests.get(f"https://api.real-debrid.com/rest/1.0/torrents/info/{r['id']}", headers=h, timeout=30).json()
                
                # Update progress bar with RD caching progress
                progress_pct = i.get('progress', 0)
                status = i.get('status', 'unknown')
                with progress_lock:
                    progress_bar.value = progress_pct
                    if status == 'downloading':
                        progress_bar.description = f"RD: {int(progress_pct)}% cached"
                    elif status == 'waiting_files_selection':
                        progress_bar.description = "RD: Selecting files..."
                    elif status == 'queued':
                        progress_bar.description = "RD: Queued..."
                    else:
                        progress_bar.description = f"RD: {status}"
                
                if status == 'downloaded':
                    with progress_lock:
                        progress_bar.value = 100
                        progress_bar.description = "RD: Cached ✓"
                    print(f"   ✅ Torrent cached - {len(i.get('links', []))} file(s)")
                    for idx, l in enumerate(i['links'], 1):
                        print(f"   📥 Downloading file {idx}/{len(i['links'])}...")
                        process_rd_link(l, key)
                    return
                time.sleep(2)
            
            print("   ❌ RD Timeout - Torrent took too long to cache (2 min limit)")
        except Exception as e:
            print(f"   ❌ RD Magnet Error: {str(e)[:80]}")
        finally:
            with progress_lock:
                progress_bar.description = "Idle"
                progress_bar.bar_style = 'info'
        return
    
    # Regular RD link (unrestrict and download)
    try:
        d = requests.post("https://api.real-debrid.com/rest/1.0/unrestrict/link", data={"link": link}, headers=h, timeout=30).json()
        if 'error' in d:
            print(f"   ❌ RD Unrestrict Error: {d.get('error', 'Unknown')} - Check if link is supported")
            return
        
        # Generate a task_id for progress tracking
        task_id = f"rd_{str(uuid4())[:8]}"
        with progress_lock:
            progress_bar.value = 0
            progress_bar.bar_style = 'info'
        
        f = download_with_aria2(d['download'], d['filename'], COLAB_ROOT, task_id=task_id)
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

def resolve_magnet_files(magnet_url: str, rd_key: str) -> List[DownloadTask]:
    """
    Add magnet to RD and wait for file list to be available.
    Returns list of DownloadTask objects for each file in the torrent.
    """
    if not rd_key:
        print(f"   ❌ RD Token required for magnets")
        return []
    
    h = {"Authorization": f"Bearer {rd_key}"}
    
    try:
        print("   🧲 Adding magnet to Real-Debrid...")
        
        # Add magnet to RD (with retry on rate-limit)
        global _rd_magnet_delay
        r = None
        for attempt in range(4):  # Up to 4 attempts (0, 1, 2, 3)
            r = requests.post("https://api.real-debrid.com/rest/1.0/torrents/addMagnet", 
                             data={"magnet": magnet_url}, headers=h, timeout=30).json()
            if 'error' not in r:
                break  # Success
            # Check if rate-limited — retry with exponential backoff
            err_msg = r.get('error', '').lower().replace('_', ' ')
            if attempt < 3 and any(kw in err_msg for kw in ['too many', 'limit', 'flood', 'action already done']):
                _rd_magnet_delay = 2  # Enable pacing for all subsequent magnets
                wait = 5 * (2 ** attempt)  # 5s, 10s, 20s
                print(f"   ⏳ RD rate limit hit, retrying in {wait}s (attempt {attempt + 2}/4)...")
                time.sleep(wait)
            else:
                print(f"   ❌ RD Magnet Error: {r.get('error', 'Unknown')}")
                return []
        
        torrent_id = r['id']
        
        # Wait for file list to become available (magnet_conversion -> waiting_files_selection)
        print("   ⏳ Waiting for torrent metadata...")
        for _ in range(30):  # 60 seconds max
            info = requests.get(f"https://api.real-debrid.com/rest/1.0/torrents/info/{torrent_id}", 
                               headers=h, timeout=30).json()
            status = info.get('status', '')
            
            if status == 'waiting_files_selection':
                # Files are available for selection
                files = info.get('files', [])
                if not files:
                    print("   ⚠️ No files found in torrent")
                    return []
                
                torrent_name = info.get('filename', 'Unknown Torrent')
                print(f"   ✅ Found {len(files)} file(s) in: {torrent_name[:50]}")
                
                # Create DownloadTask for each file
                tasks = []
                for f in files:
                    file_id = f.get('id')
                    file_path = f.get('path', '').lstrip('/')
                    file_name = os.path.basename(file_path) if file_path else f"file_{file_id}"
                    file_size = f.get('bytes', 0)
                    size_mb = file_size / (1024 * 1024)
                    
                    # Skip very small files (likely samples or NFOs)
                    if size_mb < 1 and not file_name.endswith(('.srt', '.ass', '.sub', '.vtt')):
                        continue
                    
                    tasks.append(DownloadTask(
                        url=magnet_url,  # Store original magnet
                        filename=f"{file_name} ({size_mb:.1f} MB)" if size_mb > 0 else file_name,
                        source="magnet",
                        link_type="magnet_file",
                        original_url=f"{torrent_id}:{file_id}",  # Store torrent_id:file_id for later selection
                    ))
                
                return tasks
            
            elif status == 'downloaded':
                # Already cached - get links directly
                links = info.get('links', [])
                torrent_name = info.get('filename', 'Unknown Torrent')
                print(f"   ✅ Already cached: {torrent_name[:50]} ({len(links)} files)")
                
                tasks = []
                for link_idx, link in enumerate(links):
                    # Rate limit: delay between unrestrict calls to avoid RD fair-use blocks
                    if link_idx > 0:
                        time.sleep(2)
                    # Unrestrict to get filename
                    try:
                        d = requests.post("https://api.real-debrid.com/rest/1.0/unrestrict/link",
                                         data={"link": link}, headers=h, timeout=30).json()
                        if 'download' in d:
                            tasks.append(DownloadTask(
                                url=d['download'],
                                filename=d.get('filename', 'unknown'),
                                source="magnet",
                                link_type="rd",  # Already unrestricted, can download directly
                                original_url=link,
                            ))
                    except Exception:
                        pass
                
                return tasks
            
            elif status == 'magnet_error':
                print(f"   ❌ Magnet error - invalid or dead torrent")
                # Clean up the failed torrent
                try:
                    requests.delete(f"https://api.real-debrid.com/rest/1.0/torrents/delete/{torrent_id}", 
                                   headers=h, timeout=30)
                except Exception:
                    pass
                return []
            
            time.sleep(2)
        
        print("   ❌ Timeout waiting for torrent metadata (60s)")
        return []
        
    except Exception as e:
        print(f"   ❌ RD Magnet Resolve Error: {str(e)[:80]}")
        return []

def process_magnet_file_tasks(tasks: List[DownloadTask], rd_key: str) -> int:
    """
    Process magnet_file tasks - select files in RD, wait for cache, download.
    Returns number of successfully downloaded files.
    """
    if not tasks or not rd_key:
        return 0
    
    h = {"Authorization": f"Bearer {rd_key}"}
    success_count = 0
    
    # Group tasks by torrent_id (stored as torrent_id:file_id in original_url)
    torrent_files: Dict[str, List[Tuple[str, DownloadTask]]] = {}  # torrent_id -> [(file_id, task), ...]
    
    for task in tasks:
        if task.original_url and ':' in task.original_url:
            torrent_id, file_id = task.original_url.split(':', 1)
            if torrent_id not in torrent_files:
                torrent_files[torrent_id] = []
            torrent_files[torrent_id].append((file_id, task))
    
    # Process each torrent
    for torrent_id, file_list in torrent_files.items():
        try:
            file_ids = [fid for fid, _ in file_list]
            print(f"\n   🧲 Processing torrent with {len(file_ids)} selected file(s)...")
            
            # Select only the chosen files
            file_selection = ','.join(file_ids)
            select_resp = requests.post(
                f"https://api.real-debrid.com/rest/1.0/torrents/selectFiles/{torrent_id}",
                data={"files": file_selection}, headers=h, timeout=30
            )
            
            if select_resp.status_code != 204:
                print(f"   ⚠️ File selection may have failed (status {select_resp.status_code})")
            
            # Wait for caching with progress
            with progress_lock:
                progress_bar.value = 0
                progress_bar.bar_style = 'info'
                progress_bar.description = "RD: Caching..."
            
            for poll_count in range(120):  # 4 minutes max
                info = requests.get(
                    f"https://api.real-debrid.com/rest/1.0/torrents/info/{torrent_id}",
                    headers=h, timeout=30
                ).json()
                
                status = info.get('status', '')
                progress_pct = info.get('progress', 0)
                
                with progress_lock:
                    progress_bar.value = progress_pct
                    if status == 'downloading':
                        progress_bar.description = f"RD: {int(progress_pct)}% cached"
                    elif status == 'queued':
                        progress_bar.description = "RD: Queued..."
                    else:
                        progress_bar.description = f"RD: {status}"
                
                if status == 'downloaded':
                    links = info.get('links', [])
                    print(f"   ✅ Cached! Downloading {len(links)} file(s)...")
                    
                    with progress_lock:
                        progress_bar.value = 100
                        progress_bar.description = "RD: Cached ✓"
                    
                    # Download each link
                    for idx, link in enumerate(links, 1):
                        # Rate limit: delay between unrestrict calls to avoid RD fair-use blocks
                        if idx > 1:
                            time.sleep(2)
                        try:
                            d = requests.post(
                                "https://api.real-debrid.com/rest/1.0/unrestrict/link",
                                data={"link": link}, headers=h, timeout=30
                            ).json()
                            
                            if 'download' in d:
                                print(f"   📥 [{idx}/{len(links)}] {d.get('filename', 'file')[:50]}")
                                task_id = f"rd_{str(uuid4())[:8]}"
                                f = download_with_aria2(d['download'], d['filename'], COLAB_ROOT, task_id=task_id)
                                if f:
                                    handle_file_processing(f, source="magnet")
                                    success_count += 1
                        except Exception as e:
                            print(f"   ❌ Failed to download: {str(e)[:60]}")
                    
                    break
                
                elif status in ['magnet_error', 'error', 'dead']:
                    print(f"   ❌ Torrent error: {status}")
                    break
                
                time.sleep(2)
            else:
                print("   ❌ Timeout waiting for torrent to cache (4 min)")
                
        except Exception as e:
            print(f"   ❌ Error processing torrent: {str(e)[:80]}")
        finally:
            with progress_lock:
                progress_bar.description = "Idle"
                progress_bar.bar_style = 'info'
    
    return success_count

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

# --- FSHARE RESOLVER ---
# FShare API credentials (used by legacy API if it still works)
FSHARE_APP_KEY = "L2S7R6ZMagggC5wWkQhX2+aDi467PPuftWUMRFSn"
FSHARE_API_URL = "https://api.fshare.vn/api"

def _fshare_api_login(email: str, password: str, session: requests.Session) -> Optional[Dict[str, str]]:
    """Login to FShare via legacy API. Returns {'token': ..., 'session_id': ...} or None."""
    try:
        data = {
            "user_email": email,
            "password": password,
            "app_key": FSHARE_APP_KEY,
        }
        resp = session.post(f"{FSHARE_API_URL}/user/login",
                           json=data,
                           headers={"User-Agent": "okhttp/3.6.0", "Content-Type": "application/json"},
                           timeout=REQUEST_TIMEOUT)
        result = resp.json()
        if result.get("code") == 200 and result.get("token"):
            return {"token": result["token"], "session_id": result.get("session_id", "")}
        else:
            return None
    except Exception:
        return None

def _fshare_api_get_download_link(url: str, token: str, session: requests.Session) -> Optional[str]:
    """Get direct download link from FShare API. Returns URL string or None."""
    try:
        data = {"token": token, "url": url}
        resp = session.post(f"{FSHARE_API_URL}/session/download",
                           json=data,
                           headers={"User-Agent": "okhttp/3.6.0", "Content-Type": "application/json"},
                           timeout=REQUEST_TIMEOUT)
        if resp.status_code == 200:
            result = resp.json()
            download_url = result.get("location") or result.get("url") or result.get("download")
            if download_url and download_url.startswith("http"):
                return download_url
        return None
    except Exception:
        return None

def _fshare_api_list_folder(url: str, token: str, session: requests.Session) -> List[Dict[str, Any]]:
    """List files in an FShare folder via API. Returns list of file info dicts."""
    try:
        # Extract folder code from URL
        match = re.search(r'fshare\.vn/folder/([a-zA-Z0-9]+)', url)
        if not match:
            return []
        folder_code = match.group(1)
        
        # FShare API folder listing
        page = 1
        all_files = []
        while True:
            data = {"token": token, "url": url, "dirOnly": 0, "pageIndex": page}
            resp = session.post(f"{FSHARE_API_URL}/fileops/listDir",
                               json=data,
                               headers={"User-Agent": "okhttp/3.6.0", "Content-Type": "application/json"},
                               timeout=REQUEST_TIMEOUT)
            if resp.status_code != 200:
                break
            result = resp.json()
            items = result if isinstance(result, list) else result.get("items", result.get("data", []))
            if not items:
                break
            for item in items:
                if item.get("type") == 1:  # type 1 = file (not subfolder)
                    all_files.append({
                        "name": item.get("name", "unknown"),
                        "url": f"https://www.fshare.vn/file/{item.get('linkcode', '')}",
                        "size": item.get("size", 0),
                    })
            # Check if there are more pages
            if len(items) < 50:  # Assume page size is ~50
                break
            page += 1
        return all_files
    except Exception:
        return []

def _fshare_extract_filename(url: str, session: requests.Session) -> str:
    """Extract filename from an FShare file page by scraping the HTML."""
    try:
        resp = session.get(url, timeout=REQUEST_TIMEOUT,
                          headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
        if resp.status_code == 200:
            # Try to extract filename from the page title or file info section
            title_match = re.search(r'<title>([^<]+)</title>', resp.text)
            if title_match:
                title = title_match.group(1).strip()
                # FShare titles are typically "filename - Fshare" or just "filename"
                title = re.sub(r'\s*[-|]\s*[Ff]share.*$', '', title).strip()
                if title and title.lower() not in ['fshare', 'fshare.vn', '']:
                    return sanitize_filename(title)
            # Try data attribute or download button text
            name_match = re.search(r'class="file-name[^"]*"[^>]*>([^<]+)<', resp.text)
            if name_match:
                return sanitize_filename(name_match.group(1).strip())
    except Exception:
        pass
    # Fallback: extract from URL
    match = re.search(r'fshare\.vn/file/([a-zA-Z0-9]+)', url)
    return f"fshare_{match.group(1)}" if match else "fshare_download"

def save_fshare_cookies(session: requests.Session):
    """Save FShare session cookies to json file."""
    try:
        if not os.path.exists(UD_CONFIG_PATH):
            os.makedirs(UD_CONFIG_PATH, exist_ok=True)
        cookies = requests.utils.dict_from_cookiejar(session.cookies)
        with open(FSHARE_COOKIE_FILE, 'w') as f:
            json.dump(cookies, f)
    except Exception as e:
        print(f"   ⚠️ Could not save FShare cookies: {e}")

def load_fshare_cookies(session: requests.Session) -> bool:
    """Load FShare session cookies from json file if it exists."""
    try:
        if os.path.exists(FSHARE_COOKIE_FILE):
            with open(FSHARE_COOKIE_FILE, 'r') as f:
                cookies = json.load(f)
            session.cookies.update(requests.utils.cookiejar_from_dict(cookies))
            return True
    except Exception as e:
        print(f"   ⚠️ Could not load FShare cookies: {e}")
    return False

def _is_fshare_logged_in(session: requests.Session) -> bool:
    """Verify if FShare session is active by requesting the login page."""
    try:
        resp = session.get("https://www.fshare.vn/site/login",
                           headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                           timeout=10,
                           allow_redirects=True)
        return "/site/login" not in resp.url
    except Exception:
        return False

def _fshare_web_login(email: str, password: str, session: requests.Session) -> bool:
    """Login to FShare via web interface. Returns True on success."""
    try:
        # Get the login page to obtain CSRF token and session cookie
        login_url = "https://www.fshare.vn/site/login"
        login_page = session.get(login_url,
                                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                                timeout=REQUEST_TIMEOUT)
        
        # Check if CAPTCHA is present on page load
        if 'robot' in login_page.text or 'phép tính' in login_page.text:
            print(f"   ⚠️ FShare: Robot verification (CAPTCHA) detected on login page. Please log in to fshare.vn in your web browser first to clear the challenge.")
            return False
            
        # Extract CSRF token - FShare uses "_csrf-app" as the parameter name
        # Method 1: From hidden input field (most reliable)
        csrf_input = re.search(r'name="(_csrf-app)"\s+value="([^"]+)"', login_page.text)
        if csrf_input:
            csrf_param = csrf_input.group(1)
            csrf_token = csrf_input.group(2)
        else:
            # Method 2: From meta tags
            csrf_param_meta = re.search(r'name="csrf-param"\s+content="([^"]+)"', login_page.text)
            csrf_token_meta = re.search(r'name="csrf-token"\s+content="([^"]+)"', login_page.text)
            csrf_param = csrf_param_meta.group(1) if csrf_param_meta else "_csrf-app"
            csrf_token = csrf_token_meta.group(1) if csrf_token_meta else ""
        
        if not csrf_token:
            print(f"   ⚠️ FShare: Could not extract CSRF token from login page")
            return False
            
        print(f"   ℹ️ FShare: Extracted CSRF token successfully ({csrf_param}={csrf_token[:15]}...)")
        
        # Submit login form
        login_data = {
            csrf_param: csrf_token,
            "LoginForm[email]": email,
            "LoginForm[password]": password,
            "LoginForm[rememberMe]": "0",
        }
        
        resp = session.post(login_url,
                           data=login_data,
                           headers={
                               "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                               "Referer": login_url,
                               "Content-Type": "application/x-www-form-urlencoded",
                           },
                           timeout=REQUEST_TIMEOUT,
                           allow_redirects=True)
        
        print(f"   ℹ️ FShare: Login POST status code: {resp.status_code}")
        print(f"   ℹ️ FShare: Response URL after redirect: {resp.url}")
        
        # Check if login succeeded:
        # - Failed login: stays on /site/login and contains LoginForm fields
        # - Successful login: redirects away from /site/login (e.g. to homepage or dashboard)
        if '/site/login' not in resp.url:
            print(f"   ✅ FShare: Redirected away from login page to {resp.url}")
            save_fshare_cookies(session)
            return True
        
        # If still on login page, check if the login form is gone (another success indicator)
        if 'LoginForm[email]' not in resp.text:
            print(f"   ✅ FShare: LoginForm[email] not found in response text. Assuming logged in.")
            save_fshare_cookies(session)
            return True
            
        # Check if CAPTCHA is present in failed POST response
        if 'robot' in resp.text or 'phép tính' in resp.text:
            print(f"   ⚠️ FShare: Robot verification (CAPTCHA) detected. Please log in to fshare.vn in your web browser first to clear the challenge.")
        
        # Let's print out potential errors shown on the page (mdc-text-field-helper-line or error classes)
        val_msg_match = re.search(r'class="[^"]*validation-msg[^"]*"[^>]*>([^<]+)', resp.text)
        if val_msg_match:
            print(f"   ⚠️ FShare login page error: {val_msg_match.group(1).strip()}")
        else:
            error_match = re.search(r'class="[^"]*error[^"]*"[^>]*>([^<]+)', resp.text, re.IGNORECASE)
            if error_match and error_match.group(1).strip():
                print(f"   ⚠️ FShare login page error: {error_match.group(1).strip()}")
            else:
                # Let's check for yii validation errors
                yii_errors = re.findall(r'class="help-block"[^>]*>([^<]+)', resp.text)
                if yii_errors:
                    print(f"   ⚠️ FShare validation errors: {yii_errors}")
                else:
                    invalid_fields = re.findall(r'class="[^"]*mdc-text-field--invalid[^"]*"', resp.text)
                    if invalid_fields:
                        print(f"   ⚠️ FShare login page has invalid input fields indicator")
        
        # Delete cookies on failure
        if os.path.exists(FSHARE_COOKIE_FILE):
            try:
                os.remove(FSHARE_COOKIE_FILE)
            except Exception:
                pass
        return False
    except Exception as e:
        print(f"   ❌ FShare login exception: {str(e)}")
        # Delete cookies on failure
        if os.path.exists(FSHARE_COOKIE_FILE):
            try:
                os.remove(FSHARE_COOKIE_FILE)
            except Exception:
                pass
        return False

def _fshare_web_get_download_link(url: str, session: requests.Session) -> Optional[str]:
    """Get direct download link from FShare file page (requires logged-in session).
    
    FShare's download mechanism works via an AJAX POST to /download/get with the
    file's linkcode and CSRF token. The response is JSON: {url: "...", wait_time: N}.
    For VIP users, wait_time is 0 and url is the direct download link.
    """
    try:
        # Step 1: Visit the file page to get CSRF token and linkcode
        resp = session.get(url,
                          headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                          timeout=REQUEST_TIMEOUT)
        
        if resp.status_code != 200:
            print(f"      🔍 File page returned status {resp.status_code}")
            return None
        
        # Check for password protection
        if 'password' in resp.text.lower() and 'FilePasswordForm' in resp.text:
            print(f"   ⚠️ FShare: File is password-protected (not supported)")
            return None
        
        # Extract CSRF token from the #form-download hidden input
        csrf_input = re.search(r'name="(_csrf-app)"\s+value="([^"]+)"', resp.text)
        if csrf_input:
            csrf_param = csrf_input.group(1)
            csrf_token = csrf_input.group(2)
        else:
            # Fallback to meta tags
            csrf_param_meta = re.search(r'name="csrf-param"\s+content="([^"]+)"', resp.text)
            csrf_token_meta = re.search(r'name="csrf-token"\s+content="([^"]+)"', resp.text)
            csrf_param = csrf_param_meta.group(1) if csrf_param_meta else "_csrf-app"
            csrf_token = csrf_token_meta.group(1) if csrf_token_meta else ""
        
        if not csrf_token:
            print(f"      🔍 No CSRF token found on file page (may not be logged in)")
            return None
        
        # Extract linkcode from the form or URL
        linkcode_input = re.search(r'name="linkcode"\s+value="([^"]+)"', resp.text)
        if linkcode_input:
            linkcode = linkcode_input.group(1)
        else:
            # Fallback: extract from URL
            linkcode_match = re.search(r'fshare\.vn/file/([a-zA-Z0-9]+)', url)
            if not linkcode_match:
                return None
            linkcode = linkcode_match.group(1)
        
        # Step 2: POST to /download/get (the AJAX endpoint used by download.js)
        download_data = {
            csrf_param: csrf_token,
            "linkcode": linkcode,
            "withFcode5": "0",
        }
        
        dl_resp = session.post("https://www.fshare.vn/download/get",
                              data=download_data,
                              headers={
                                  "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                                  "Referer": url,
                                  "X-Requested-With": "XMLHttpRequest",  # Mark as AJAX request
                              },
                              timeout=60)
        
        if dl_resp.status_code == 200:
            try:
                result = dl_resp.json()
                if "url" in result and result["url"]:
                    download_url = result["url"]
                    wait_time = result.get("wait_time", 0)
                    if wait_time > 0:
                        print(f"      ⏳ Wait time {wait_time}s (free account speed limit)")
                    return download_url
                elif result.get("policydownload") or result.get("policydowload"):
                    # FShare policy restriction (note: FShare API has a typo 'policydowload')
                    print(f"      ❌ FShare download policy restriction")
                    return "FSHARE_LIMIT_REACHED"
                elif "errors" in result:
                    errors = result["errors"]
                    if "linkcode" in errors:
                        print(f"      ❌ {errors['linkcode'][0]}")
                    elif "fcode" in errors:
                        print(f"      ❌ {errors['fcode'][0]}")
                    else:
                        print(f"      ❌ Errors: {errors}")
                else:
                    print(f"      🔍 Unexpected response: {str(result)[:200]}")
            except (ValueError, KeyError):
                print(f"      🔍 Non-JSON response from /download/get: {dl_resp.text[:200]}")
        else:
            print(f"      🔍 /download/get returned status {dl_resp.status_code}")
        
        return None
    except Exception as e:
        print(f"   ❌ FShare download error: {str(e)[:80]}")
        return None

# Cached FShare web session — reused across all links in a batch to avoid repeated logins
_fshare_cached_session: Optional[requests.Session] = None
_fshare_cached_session_email: str = ""
_fshare_cached_session_time: float = 0  # time.time() when session was last verified

def _get_fshare_web_session(email: str, password: str) -> Optional[requests.Session]:
    """Get or create a cached FShare web session. Logs in only once per batch."""
    global _fshare_cached_session, _fshare_cached_session_email, _fshare_cached_session_time
    
    # If we have a cached session for the same email, trust it if recently verified (<5 min)
    if _fshare_cached_session and _fshare_cached_session_email == email:
        elapsed = time.time() - _fshare_cached_session_time
        if elapsed < 300:  # Trust cached session for 5 minutes without re-verifying
            print(f"   ✅ FShare: Reusing active session (skipped login)")
            return _fshare_cached_session
        # Session is old, verify it's still active
        if _is_fshare_logged_in(_fshare_cached_session):
            print(f"   ✅ FShare: Reusing active session (skipped login)")
            _fshare_cached_session_time = time.time()
            return _fshare_cached_session
        else:
            print(f"   ℹ️ FShare: Cached session expired, logging in again...")
            _fshare_cached_session = None
    
    # Create a new session
    session = requests.Session()
    
    # Try to load saved cookies from disk
    if load_fshare_cookies(session):
        if _is_fshare_logged_in(session):
            print(f"   ✅ FShare: Restored saved session (skipped login)")
            _fshare_cached_session = session
            _fshare_cached_session_email = email
            _fshare_cached_session_time = time.time()
            return session
    
    # Need a fresh login
    if _fshare_web_login(email, password, session):
        _fshare_cached_session = session
        _fshare_cached_session_email = email
        _fshare_cached_session_time = time.time()
        return session
    
    # Login failed
    _fshare_cached_session = None
    return None

def resolve_fshare(url: str, email: str, password: str) -> List[Tuple[str, str]]:
    """Resolve FShare URL to direct download link(s) via web scraping.
    
    Supports both file URLs (fshare.vn/file/...) and folder URLs (fshare.vn/folder/...).
    Uses a cached session to avoid repeated logins when resolving multiple links.
    """
    if not email or not password:
        print(f"   ❌ FShare credentials required (set in Settings → FShare Account)")
        return []
    
    # Determine if this is a folder or file URL
    is_folder = '/folder/' in url
    
    results: List[Tuple[str, str]] = []
    
    print(f"   🇻🇳 Resolving FShare: {url[:60]}...")
    session = _get_fshare_web_session(email, password)
    if not session:
        print(f"   ❌ FShare: Login failed — click Resolve Links again to retry")
        return []
    
    if is_folder:
        # Use FShare's internal web API to list folder contents
        try:
            # Extract linkcode from URL (e.g., /folder/ABC123XYZ -> ABC123XYZ)
            linkcode_match = re.search(r'/folder/([a-zA-Z0-9]+)', url)
            if not linkcode_match:
                print(f"   ❌ FShare: Could not extract folder linkcode from URL")
                return []
            linkcode = linkcode_match.group(1)
            
            all_files = []
            page = 1
            per_page = 50
            
            while True:
                api_url = f"https://www.fshare.vn/api/v3/files/folder?linkcode={linkcode}&sort=type,name&page={page}&per-page={per_page}"
                resp = session.get(api_url,
                                   headers={
                                       "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                                       "Accept": "application/json",
                                       "X-Requested-With": "XMLHttpRequest",
                                       "Referer": url,
                                   },
                                   timeout=REQUEST_TIMEOUT)
                
                if resp.status_code != 200:
                    print(f"   ❌ FShare folder API returned status {resp.status_code}")
                    break
                
                try:
                    data = resp.json()
                except ValueError:
                    print(f"   ❌ FShare folder API returned non-JSON response")
                    break
                
                items = data if isinstance(data, list) else data.get("items", data.get("data", []))
                if not items:
                    break
                
                for item in items:
                    if isinstance(item, dict):
                        # FShare: type=1 is file, type=0 is folder
                        item_type = item.get("type")
                        if item_type == 0:
                            continue  # Skip sub-folders
                        
                        name = item.get("name", "")
                        item_linkcode = item.get("linkcode", "")
                        size = item.get("size", 0)
                        
                        if name and item_linkcode:
                            file_url = f"https://www.fshare.vn/file/{item_linkcode}"
                            all_files.append({"url": file_url, "name": name, "size": int(size) if size else 0})
                
                # Check pagination via 'links' dict
                # links = {"self": "...?page=1", "first": "...?page=1", "last": "...?page=N"}
                links = data.get("links", {}) if isinstance(data, dict) else {}
                last_link = links.get("last", "")
                has_more = False
                
                if last_link:
                    last_page_match = re.search(r'page=(\d+)', last_link)
                    if last_page_match:
                        total_pages = int(last_page_match.group(1))
                        has_more = page < total_pages
                
                # Fallback: if we got exactly per_page items, there are likely more pages
                if not has_more and len(items) >= per_page:
                    has_more = True
                
                if not has_more:
                    break
                page += 1
                time.sleep(0.5)  # Rate limit between pages
            
            if all_files:
                total = len(all_files)
                print(f"   📁 FShare folder: {total} file(s) listed")
                print(f"   💡 Download links will be resolved when you click 'Start Download'")
                print(f"   💡 Remove unwanted files from the queue first to save your daily download limit")
                for f_info in all_files:
                    file_url = f_info["url"]
                    filename = f_info["name"]
                    results.append((file_url, sanitize_filename(filename)))
                return results
            else:
                print(f"   ⚠️ FShare: No files found in folder (folder may be empty or require login)")
        except Exception as e:
            print(f"   ❌ FShare folder error: {str(e)[:80]}")
        return []
    else:
        # Single file via web
        filename = _fshare_extract_filename(url, session)
        dl_link = _fshare_web_get_download_link(url, session)
        if dl_link and dl_link != "FSHARE_LIMIT_REACHED":
            print(f"   ✅ FShare (web): {filename}")
            return [(dl_link, sanitize_filename(filename))]
        elif dl_link == "FSHARE_LIMIT_REACHED":
            print(f"   🛑 FShare daily download limit reached — try again tomorrow")
            return []
        else:
            print(f"   ❌ FShare: Could not extract download link — VIP account may be required")
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
    global _rd_magnet_delay
    _rd_magnet_delay = 0  # Reset adaptive pacing for each new batch
    parallel_tasks: List[DownloadTask] = []
    youtube_urls: List[str] = []
    mega_urls: List[str] = []
    rd_urls: List[str] = []
    
    for url in urls:
        if "transfer.it" in url:
            mega_urls.append(url)
        elif "mega.nz" in url:
            if rd_key:
                # Try RD for MEGA (handles all URL formats)
                resolved = resolve_rd_link(url, rd_key)
                if resolved:
                    for u, n in resolved:
                        parallel_tasks.append(DownloadTask(
                            url=u, filename=n, source="mega", link_type="rd",
                            original_url=url
                        ))
                else:
                    # RD failed (e.g. ip_not_allowed from Colab) — fall back to megadl
                    print(f"   ⤴️ Falling back to megadl for: {url[:60]}...")
                    mega_urls.append(url)
            else:
                mega_urls.append(url)
        elif any(h in url for h in ['youtube.com', 'youtu.be', 'vimeo.com', 'twitch.tv', 'ok.ru']) or ('archive.org/details/' in url):
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
            # Resolve magnet to individual files if RD key available
            if rd_key:
                # Adaptive pacing: delay only after a rate-limit has been hit
                if _rd_magnet_delay > 0:
                    time.sleep(_rd_magnet_delay)
                magnet_tasks = resolve_magnet_files(url, rd_key)
                parallel_tasks.extend(magnet_tasks)
            else:
                # No RD key - can't process magnet
                print(f"   ❌ RD Token required for magnet links")
        elif "real-debrid.com/d/" in url:
            # RD direct links can be parallelized
            resolved = resolve_rd_link(url, rd_key)
            for u, n in resolved:
                parallel_tasks.append(DownloadTask(
                    url=u, filename=n, source="rd", link_type="rd",
                    original_url=url  # Store original for re-resolve
                ))
        elif "fshare.vn" in url:
            # FShare file or folder links - resolve with VIP account
            fshare_email = token_fshare_email.value.strip()
            fshare_password = token_fshare_password.value.strip()
            resolved = resolve_fshare(url, fshare_email, fshare_password)
            for u, n in resolved:
                parallel_tasks.append(DownloadTask(
                    url=u, filename=n, source="fshare", link_type="fshare",
                    original_url=url
                ))
            if resolved:
                time.sleep(1)  # Rate limit between FShare link resolutions
        elif rd_key and any(host in url for host in RD_SUPPORTED_HOSTS):
            # Route through RD for any supported premium host
            resolved = resolve_rd_link(url, rd_key)
            for u, n in resolved:
                parallel_tasks.append(DownloadTask(
                    url=u, filename=n, source="rd_host", link_type="rd",
                    original_url=url
                ))
        elif rd_key and "http" in url and "archive.org" not in url:
            # Other links through RD - try unrestricting
            rd_urls.append(url)
        else:
            # Direct URL (including archive.org/download/ links)
            filename = os.path.basename(unquote(urlparse(url).path)) or "download"
            source = "archive" if "archive.org" in url else "direct"
            parallel_tasks.append(DownloadTask(
                url=url, filename=filename, source=source, link_type="direct"
            ))
    
    return parallel_tasks, youtube_urls, mega_urls, rd_urls

def update_progress_display(tasks: List[DownloadTask]):
    """Update progress bar with parallel download status, speed, and ETA."""
    global last_display_speed
    
    active = [t for t in tasks if t.status == "downloading"]
    done = sum(1 for t in tasks if t.status in ["done", "skipped"])
    failed = sum(1 for t in tasks if t.status == "failed")
    total = len(tasks)
    
    # Collect speeds and individual progress from active downloads
    total_speed_mbs = 0.0
    individual_progress = 0.0
    for t in active:
        status = active_downloads.get(t.id, "0%")
        # Extract percentage from status like "45% (5.2MiB/s)"
        pct_match = re.search(r'(\d+)%', status)
        if pct_match:
            individual_progress = float(pct_match.group(1))  # Use last active's progress for single downloads
        # Extract speed from status like "45% (5.2MiB/s)" or "45% (5.2MB/s)"
        speed_match = re.search(r'\(([\d.]+)([KMG])i?B/s\)', status)
        if speed_match:
            speed_val = float(speed_match.group(1))
            unit = speed_match.group(2)
            if unit == 'K': total_speed_mbs += speed_val / 1024
            elif unit == 'M': total_speed_mbs += speed_val
            elif unit == 'G': total_speed_mbs += speed_val * 1024
    
    # Use current speed if available, otherwise keep last known speed
    if total_speed_mbs > 0:
        last_display_speed = total_speed_mbs
    display_speed = last_display_speed if last_display_speed > 0 else total_speed_mbs
    
    # Special case: single download shows real-time individual progress
    if total == 1 and active:
        display_progress = individual_progress
    else:
        # Batch progress: completed tasks / total tasks
        display_progress = (done / total) * 100 if total else 0
    
    progress_bar.value = display_progress
    progress_bar.bar_style = 'warning' if active else 'success' if done == total else 'info'
    
    # Calculate ETA based on remaining tasks and current speed
    remaining = total - done - failed
    eta_str = ""
    if active and batch_start_time:
        elapsed = time.time() - batch_start_time
        if done > 0:
            avg_time_per_task = elapsed / done
            eta_seconds = avg_time_per_task * remaining
            if eta_seconds < 60:
                eta_str = f"{int(eta_seconds)}s"
            elif eta_seconds < 3600:
                eta_str = f"{int(eta_seconds // 60)}m {int(eta_seconds % 60)}s"
            else:
                eta_str = f"{int(eta_seconds // 3600)}h {int((eta_seconds % 3600) // 60)}m"
    
    if active:
        speed_str = f"{display_speed:.1f} MB/s" if display_speed > 0 else "starting..."
        eta_part = f" | ⏱️ {eta_str}" if eta_str else ""
        progress_bar.description = f"⚡ {done}/{total}"
        status_label.value = f"<small>📊 <b>{len(active)} downloading</b> | ⬇️ {speed_str}{eta_part}</small>"
    elif done == total:
        progress_bar.description = f"✅ {done}/{total}"
        status_label.value = ""
        last_display_speed = 0.0  # Reset for next batch
    elif failed > 0:
        progress_bar.description = f"⚠️ {done}/{total}"
        status_label.value = f"<small style='color:orange'>❌ {failed} failed</small>"
    else:
        progress_bar.description = f"DL {done}/{total}"

def progress_monitor(tasks: List[DownloadTask], interval: float = 0.5):
    """Background thread to update progress display periodically."""
    global stop_monitor
    while not stop_monitor:
        try:
            update_progress_display(tasks)
            time.sleep(interval)
        except Exception:
            pass


def _run_download_pipeline(
    all_tasks: List[DownloadTask],
    parallel_tasks: List[DownloadTask],
    youtube_urls: List[str],
    mega_urls: List[str],
    rd_urls: List[str],
    mode: str,
    gofile_token: str,
    rd_key: str,
    max_workers: int,
    magnet_file_tasks: Optional[List[DownloadTask]] = None,
    enable_retry: bool = True
) -> Tuple[int, int]:
    """
    Shared download orchestration for parallel and sequential downloads.
    
    Returns: (total_success, total_failed) counts
    """
    global yt_success_cumulative, yt_fail_cumulative, stop_monitor, batch_start_time
    import threading
    
    start_keep_alive()
    
    # --- PARALLEL DOWNLOADS ---
    if parallel_tasks:
        total_parallel = len(parallel_tasks)
        print(f"⚡ Starting {total_parallel} parallel downloads (max {max_workers} concurrent)...")
        
        stop_monitor = False
        batch_start_time = time.time()
        
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
                        save_session(all_tasks, gofile_token=gofile_token, rd_token=rd_key, 
                                   show_name=show_name_override.value.strip(), year=year_input.value.strip(), playlist_range=playlist_selection.value.strip(), 
                                   yt_success=yt_success_cumulative, yt_fail=yt_fail_cumulative, 
                                   subtitle_langs_value=subtitle_langs.value)
                    except Exception as e:
                        print(f"   ❌ Task failed: {str(e)[:80]}")
                        task.status = "failed"
                        task.error = str(e)[:100]
        finally:
            stop_monitor = True
        
        update_progress_display(parallel_tasks)
        print(f"✅ Parallel downloads complete!")
    
    # --- SEQUENTIAL DOWNLOADS ---
    yt_success = 0
    yt_fail = 0
    
    if youtube_urls:
        print(f"\n▶️ Processing {len(youtube_urls)} YouTube links...")
        playlist_url_count = sum(1 for u in youtube_urls if 'list=' in u or '/playlist' in u)
        use_playlist_range = playlist_url_count <= 1
        if not use_playlist_range and playlist_selection.value.strip():
            print("   ℹ️ Multiple playlist URLs detected - playlist range ignored (downloading all videos)")
        
        for i, url in enumerate(youtube_urls, 1):
            print(f"   [{i}/{len(youtube_urls)}] {url[:60]}...")
            s, f, total = process_youtube_link(url, mode, apply_playlist_range=use_playlist_range)
            yt_success += s
            yt_fail += f
            yt_success_cumulative += s
            yt_fail_cumulative += f
            
            for task in all_tasks:
                if task.url == url and task.link_type == 'youtube':
                    task.status = "done" if f == 0 else "failed"
                    break
            save_session(all_tasks, gofile_token=gofile_token, rd_token=rd_key, 
                       show_name=show_name_override.value.strip(), playlist_range=playlist_selection.value.strip(), 
                       yt_success=yt_success_cumulative, yt_fail=yt_fail_cumulative, 
                       subtitle_langs_value=subtitle_langs.value)
        
        if yt_fail > 0:
            print(f"   📊 YouTube: {yt_success_cumulative} succeeded, {yt_fail_cumulative} failed")
        else:
            print(f"   📊 YouTube: {yt_success_cumulative} succeeded")
    
    if mega_urls:
        print(f"\n☁️ Processing {len(mega_urls)} Mega links...")
        for i, url in enumerate(mega_urls, 1):
            print(f"   [{i}/{len(mega_urls)}] {url[:60]}...")
            mega_success = process_mega_link(url)
            for t in all_tasks:
                if t.url == url and t.link_type == 'mega':
                    t.status = "done" if mega_success else "failed"
                    break
            save_session(all_tasks, gofile_token=gofile_token, rd_token=rd_key, 
                       show_name=show_name_override.value.strip(), playlist_range=playlist_selection.value.strip(), 
                       yt_success=yt_success_cumulative, yt_fail=yt_fail_cumulative, 
                       subtitle_langs_value=subtitle_langs.value)
    
    if rd_urls and rd_key:
        print(f"\n🔓 Processing {len(rd_urls)} RD links...")
        for i, url in enumerate(rd_urls, 1):
            print(f"   [{i}/{len(rd_urls)}] {url[:60]}...")
            process_rd_link(url, rd_key)
            for t in all_tasks:
                if t.url == url and t.link_type == 'magnet':
                    t.status = "done"
                    break
            save_session(all_tasks, gofile_token=gofile_token, rd_token=rd_key, 
                       show_name=show_name_override.value.strip(), playlist_range=playlist_selection.value.strip(), 
                       yt_success=yt_success_cumulative, yt_fail=yt_fail_cumulative, 
                       subtitle_langs_value=subtitle_langs.value)
    
    # --- MAGNET FILE TASKS ---
    if magnet_file_tasks and rd_key:
        print(f"\n🧲 Processing {len(magnet_file_tasks)} selected magnet files...")
        process_magnet_file_tasks(magnet_file_tasks, rd_key)
        for t in all_tasks:
            if t.link_type == 'magnet_file':
                t.status = "done"
    
    # --- SUMMARY ---
    done_count = sum(1 for t in all_tasks if t.status == 'done')
    failed_count = sum(1 for t in all_tasks if t.status == 'failed')
    
    # Adjust for YouTube individual counts
    total_success = done_count - len([t for t in all_tasks if t.link_type == 'youtube' and t.status == 'done']) + yt_success_cumulative
    total_failed = failed_count - len([t for t in all_tasks if t.link_type == 'youtube' and t.status == 'failed']) + yt_fail_cumulative
    
    return total_success, total_failed


def execute_selected_tasks(selected_tasks: List[DownloadTask], mode: str):
    """Execute download for selected tasks from queue."""
    global yt_success_cumulative, yt_fail_cumulative
    yt_success_cumulative = 0
    yt_fail_cumulative = 0
    
    clear_output(wait=True)
    display(input_ui)
    settings_ui.layout.display = 'none'
    btn.disabled = True
    btn_subs.disabled = True
    btn_resume.disabled = True
    
    start_keep_alive()
    try:
        gofile_token = token_gf.value.strip()
        rd_key = token_rd.value.strip()
        max_workers = concurrent_slider.value
        
        # Separate by type
        parallel_tasks = [t for t in selected_tasks if t.link_type in ['gofile', 'pixeldrain', 'direct', 'rd', 'fshare']]
        youtube_urls = [t.url for t in selected_tasks if t.link_type == 'youtube']
        mega_urls = [t.url for t in selected_tasks if t.link_type == 'mega']
        rd_urls = [t.url for t in selected_tasks if t.link_type == 'magnet']
        magnet_file_tasks = [t for t in selected_tasks if t.link_type == 'magnet_file']
        
        # Resolve FShare download links sequentially before parallel download
        # (deferred from Resolve Links phase so user can review queue first)
        fshare_unresolved = [t for t in parallel_tasks if t.link_type == 'fshare' and 'fshare.vn/file/' in t.url]
        if fshare_unresolved:
            fshare_email = token_fshare_email.value.strip()
            fshare_password = token_fshare_password.value.strip()
            if fshare_email and fshare_password:
                session = _get_fshare_web_session(fshare_email, fshare_password)
                if session:
                    total_fshare = len(fshare_unresolved)
                    print(f"🔄 Resolving {total_fshare} FShare download link(s)...")
                    print(f"   ⚠️ Each resolved link counts toward your daily FShare download limit")
                    resolved_count = 0
                    limit_reached = False
                    consecutive_failures = 0
                    for i, task in enumerate(fshare_unresolved, 1):
                        print(f"   [{i}/{total_fshare}] {task.filename[:60]}{'...' if len(task.filename) > 60 else ''}")
                        dl_link = _fshare_web_get_download_link(task.url, session)
                        if dl_link == "FSHARE_LIMIT_REACHED":
                            print(f"   🛑 FShare download policy restriction — stopping resolution")
                            print(f"   💡 Try again later or check your FShare account status")
                            task.status = "failed"
                            task.error = "FShare policy restriction"
                            # Mark remaining tasks as failed too
                            for remaining in fshare_unresolved[i:]:
                                remaining.status = "failed"
                                remaining.error = "FShare policy restriction"
                            limit_reached = True
                            break
                        elif dl_link:
                            task.url = dl_link
                            resolved_count += 1
                            consecutive_failures = 0
                            print(f"   ✅ Resolved")
                        else:
                            print(f"   ⚠️ Could not resolve — will skip")
                            task.status = "failed"
                            task.error = "Could not resolve FShare download link"
                            consecutive_failures += 1
                            if consecutive_failures >= 3:
                                print(f"   🛑 3 consecutive failures — stopping resolution")
                                print(f"   💡 FShare may be temporarily blocking requests")
                                for remaining in fshare_unresolved[i:]:
                                    remaining.status = "failed"
                                    remaining.error = "Skipped (consecutive failures)"
                                limit_reached = True
                                break
                        time.sleep(1)  # Rate limiting
                    # Remove failed FShare tasks from parallel
                    parallel_tasks = [t for t in parallel_tasks if t.status != "failed"]
                    if limit_reached:
                        print(f"   📊 {resolved_count}/{total_fshare} resolved before limit was reached\n")
                    else:
                        print(f"   📊 {resolved_count}/{total_fshare} FShare links resolved\n")
                else:
                    print("   ❌ FShare login failed — skipping FShare downloads")
                    parallel_tasks = [t for t in parallel_tasks if t.link_type != 'fshare']
        
        all_tasks = selected_tasks.copy()
        
        total_parallel = len(parallel_tasks)
        total_sequential = len(youtube_urls) + len(mega_urls) + len(rd_urls) + len(magnet_file_tasks)
        print(f"📊 Starting: {total_parallel} parallel + {total_sequential} sequential\n")
        
        # Run shared download pipeline
        total_success, total_failed = _run_download_pipeline(
            all_tasks=all_tasks,
            parallel_tasks=parallel_tasks,
            youtube_urls=youtube_urls,
            mega_urls=mega_urls,
            rd_urls=rd_urls,
            mode=mode,
            gofile_token=gofile_token,
            rd_key=rd_key,
            max_workers=max_workers,
            magnet_file_tasks=magnet_file_tasks,
            enable_retry=True
        )
        
        # Handle results
        if total_failed > 0:
            failed_files = [t.filename for t in all_tasks if t.status == 'failed']
            print(f"\n⚠️ Completed with {total_success} success, {total_failed} failed after 3 attempts:")
            for f in failed_files[:5]:
                print(f"   ❌ {f[:60]}")
            if len(failed_files) > 5:
                print(f"   ... and {len(failed_files) - 5} more")
            
            save_session(all_tasks, gofile_token=gofile_token, rd_token=rd_key, 
                        show_name=show_name_override.value.strip(), year=year_input.value.strip(), playlist_range=playlist_selection.value.strip(), 
                        yt_success=yt_success_cumulative, yt_fail=yt_fail_cumulative)
            print(f"\n💾 Session saved. Use 'Resume Previous' to retry later, or 'Clear Session' in Settings to mark complete.")
            btn_restart.layout.display = 'inline-block'
        else:
            print(f"\n✅ All {total_success} downloads completed successfully!")
            clear_session()
            btn_restart.layout.display = 'none'
            yt_success_cumulative = 0
            yt_fail_cumulative = 0
    
    except Exception as e:
        print(f"\n❌ Critical Error: {e}")
    finally:
        stop_keep_alive()
        btn.disabled = False
        btn_quick.disabled = False
        btn_subs.disabled = False
        btn_resume.disabled = False
        reset_progress()
        check_resume_available()


def execute_batch(mode: str, resume: bool = False, quick_mode: bool = False):
    global yt_success_cumulative, yt_fail_cumulative  # Must be at function start
    clear_output(wait=True)
    display(input_ui)
    settings_ui.layout.display = 'none'  # Close settings panel if open
    btn.disabled = True
    btn_quick.disabled = True
    btn_subs.disabled = True
    btn_resume.disabled = True
    print(f"\n🚀 Initializing... (Mode: {mode}, Resume: {resume})")
    
    start_keep_alive()
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
            # Restore year from session
            saved_year = session_data.get('year', '')
            if saved_year:
                year_input.value = saved_year
                print(f"   📅 Restored year: {saved_year}")
            # Restore playlist range from session
            saved_playlist_range = session_data.get('playlist_range', '')
            if saved_playlist_range:
                playlist_selection.value = saved_playlist_range
                print(f"   🎯 Restored playlist range: {saved_playlist_range}")
            # Restore subtitle language selection from session
            saved_subtitle_langs = session_data.get('subtitle_langs', None)
            if saved_subtitle_langs:
                subtitle_langs.value = tuple(saved_subtitle_langs)
                print(f"   🔤 Restored subtitle languages: {', '.join(saved_subtitle_langs)}")
            # Restore media type and category from session
            saved_media_type = session_data.get('media_type', '')
            if saved_media_type:
                media_type_toggle.value = saved_media_type
                print(f"   🎭 Restored media type: {saved_media_type}")
            saved_category = session_data.get('category', '')
            if saved_category:
                category_override.value = saved_category
                print(f"   📂 Restored category: {saved_category}")
            # Restore cumulative YouTube counters
            # Only restore success count - reset fail count so previous 403s don't persist
            yt_success_cumulative = session_data.get('yt_success', 0)
            yt_fail_cumulative = 0  # Reset failures - only count failures in current run
            all_tasks = [DownloadTask(**t) for t in session_data.get('tasks', [])]
            
            # Filter to pending/failed/downloading tasks (downloading = was active when runtime crashed)
            pending_tasks = [t for t in all_tasks if t.status in ['pending', 'failed', 'downloading']]
            print(f"📂 Resuming {len(pending_tasks)} of {len(all_tasks)} tasks...")
            
            # Install required tools first
            needs_pixeldrain_gofile_rd = any(t.link_type in ['gofile', 'pixeldrain', 'rd'] for t in pending_tasks)
            needs_fshare = any(t.link_type == 'fshare' for t in pending_tasks)
            needs_ytdlp = any(t.link_type in ['youtube', 'archive'] for t in pending_tasks)
            needs_mega = any(t.link_type == 'mega' for t in pending_tasks)
            needs_aria = any(t.link_type in ['gofile', 'pixeldrain', 'direct', 'rd', 'fshare'] for t in pending_tasks)
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
            
            # Re-resolve FShare URLs with fresh login session
            if needs_fshare:
                fshare_email = token_fshare_email.value.strip()
                fshare_password = token_fshare_password.value.strip()
                if fshare_email and fshare_password:
                    fshare_tasks = [t for t in pending_tasks if t.original_url and t.link_type == 'fshare']
                    if fshare_tasks:
                        print(f"🔄 Re-resolving {len(fshare_tasks)} FShare link(s)...")
                        print(f"   ⚠️ Note: Each resolved link counts toward your daily FShare download limit")
                        for task in fshare_tasks:
                            try:
                                resolved = resolve_fshare(task.original_url, fshare_email, fshare_password)
                                if resolved:
                                    task.url = resolved[0][0]
                            except Exception as e:
                                print(f"   ⚠️ Could not re-resolve FShare {task.filename}: {e}")
            
            # Separate by type for processing
            parallel_tasks = [t for t in pending_tasks if t.link_type in ['gofile', 'pixeldrain', 'direct', 'rd', 'fshare']]
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
            
            needs_ytdlp = any(h in u for u in urls for h in ['youtube.com', 'youtu.be', 'twitch.tv', 'tiktok.com', 'vimeo.com', 'dailymotion.com', 'soundcloud.com', 'ok.ru']) or any('archive.org/details/' in u for u in urls)
            needs_mega = any("mega.nz" in u or "transfer.it" in u for u in urls)
            needs_aria = not (needs_ytdlp and not needs_mega) or any(h in u for u in urls for h in ["gofile.io", "pixeldrain.com", "magnet:", "real-debrid", "mega.nz", "fshare.vn"])

            
            setup_environment(needs_mega, needs_ytdlp, needs_aria)
            
            s, t = get_gofile_session(gofile_token)
            
            print(f"🔍 Resolving {len(urls)} links...")
            parallel_tasks, youtube_urls, mega_urls, rd_urls = resolve_all_links(urls, s, t, rd_key)
            
            # Create session-compatible task list for saving
            all_tasks = parallel_tasks.copy()
            
            # Expand YouTube playlists into individual video tasks for queue display
            for url in youtube_urls:
                yt_tasks = resolve_youtube_playlist(url)
                all_tasks.extend(yt_tasks)
            
            for url in mega_urls:
                all_tasks.append(DownloadTask(url=url, filename="", source="mega", link_type="mega"))
            for url in rd_urls:
                # Distinguish between magnet links and other RD-related links
                if "magnet:?" in url:
                    all_tasks.append(DownloadTask(url=url, filename="", source="rd", link_type="magnet"))
                else:
                    all_tasks.append(DownloadTask(url=url, filename="", source="rd", link_type="rd"))
            
            # Save initial session
            save_session(all_tasks, gofile_token=gofile_token, rd_token=rd_key, 
                        show_name=show_name_override.value.strip(), year=year_input.value.strip(), playlist_range=playlist_selection.value.strip())
            
            if quick_mode:
                # Quick Download: Skip queue preview, start immediately
                print(f"⚡ Quick Download: Starting {len(all_tasks)} items...")
                execute_selected_tasks(all_tasks, mode)
            else:
                # Show queue preview instead of immediate download
                show_queue_preview(all_tasks, mode)
            return  # Wait for user to click "Start Selected"
        
        # This code only runs for RESUME mode (preview was skipped)
        total_parallel = len(parallel_tasks)
        total_sequential = len(youtube_urls) + len(mega_urls) + len(rd_urls)
        print(f"📊 Tasks: {total_parallel} parallel + {total_sequential} sequential\n")
        
        # Run shared download pipeline (no retry in resume mode - user can resume again if needed)
        total_success, total_failed = _run_download_pipeline(
            all_tasks=all_tasks,
            parallel_tasks=parallel_tasks,
            youtube_urls=youtube_urls,
            mega_urls=mega_urls,
            rd_urls=rd_urls,
            mode=mode,
            gofile_token=gofile_token,
            rd_key=rd_key,
            max_workers=max_workers,
            magnet_file_tasks=None,
            enable_retry=False  # Resume mode: user can resume again if needed
        )
        
        # Handle results
        if total_failed > 0:
            print(f"\n⚠️ Completed with {total_success} success, {total_failed} failed (session saved for retry)")
            btn_restart.layout.display = 'inline-block'
        else:
            print(f"\n✅ All {total_success} downloads completed successfully!")
            clear_session()
            btn_restart.layout.display = 'none'
            yt_success_cumulative = 0
            yt_fail_cumulative = 0
        
    except Exception as e: 
        print(f"\n❌ Critical Error: {e}")
    finally: 
        stop_keep_alive()
        btn.disabled = False
        btn_quick.disabled = False
        btn_resume.disabled = False
        reset_progress()
        check_resume_available()

# --- QUICK DOWNLOAD ---
def on_quick_download(b=None):
    """Quick Download - bypass queue preview and download immediately."""
    urls = text_area.value.strip()
    if not urls:
        print("⚠️ No links to download!")
        return
    
    # Setup subtitle languages for Quick Download if enabled
    if quick_dl_subs_checkbox.value:
        subtitle_langs.value = quick_dl_subtitle_langs.value
    
    # Call execute_batch with quick_mode to skip queue preview
    execute_batch("video", quick_mode=True)

# --- BINDINGS ---
btn.on_click(lambda b: execute_batch("video"))
btn_quick.on_click(on_quick_download)
btn_resume.on_click(lambda b: execute_batch("video", resume=True))
btn_restart.on_click(restart_runtime)
btn_history.on_click(view_history)

# Queue control bindings
btn_queue_up.on_click(queue_move_up)
btn_queue_down.on_click(queue_move_down)
btn_queue_select_all.on_click(queue_select_all)
btn_queue_select_none.on_click(queue_select_none)
btn_queue_remove.on_click(queue_remove_selected)
btn_queue_cancel.on_click(queue_cancel)
btn_queue_sort.on_click(queue_sort_alpha)
btn_queue_start.on_click(lambda b: start_from_queue(mode="video"))
btn_queue_start_subs.on_click(lambda b: start_from_queue(mode="subs_only"))

# Settings control bindings
btn_settings.on_click(toggle_settings)
btn_settings_close.on_click(close_settings)
btn_upload_cookies.on_click(upload_cookies)
btn_clear_cookies.on_click(clear_cookies)
btn_clear_history.on_click(request_clear_history)
btn_clear_ytarchive.on_click(request_clear_ytarchive)
btn_clear_session.on_click(request_clear_session)
btn_confirm_yes.on_click(confirm_action)
btn_confirm_cancel.on_click(cancel_confirmation)

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

# Display UI first (so it shows even if mount hangs), then mount drive
display(input_ui)
early_mount_drive()