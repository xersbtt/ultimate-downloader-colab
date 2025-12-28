import os
import re
import requests
import subprocess
import shutil
import time
import sys
import ipywidgets as widgets
from IPython.display import display, clear_output
from urllib.parse import urlparse, unquote
from google.colab import drive

# --- CONFIGURATION ---
DRIVE_TV_PATH = "TV Shows"
DRIVE_MOVIE_PATH = "Movies"
DRIVE_YOUTUBE_PATH = "YouTube"
MIN_FILE_SIZE_MB = 10
KEEP_EXTENSIONS = {'.srt', '.ass', '.sub', '.vtt'}

# --- UI ELEMENTS ---
token_gf = widgets.Text(description='Gofile:', placeholder='Optional (Required for private)')
token_rd = widgets.Text(description='RD Token:', placeholder='Real-Debrid API Key')
show_name_override = widgets.Text(description='Show Name Override:', placeholder='Optional (Forces Show Name)', style={'description_width': 'initial'})

text_area = widgets.Textarea(description='Links:', placeholder='Paste Links Here (Transfer.it Zip, Mega, YouTube, etc.)...', layout=widgets.Layout(width='98%', height='150px'))
btn = widgets.Button(description="Start Download", button_style='success', icon='download')
btn_subs = widgets.Button(description="Download Subtitles Only", button_style='info', icon='closed-captioning')
progress_bar = widgets.FloatProgress(value=0.0, min=0.0, max=100.0, description='Idle', bar_style='info', layout=widgets.Layout(width='98%'))

input_ui = widgets.VBox([
    widgets.HTML("<h3>🚀 Ultimate Downloader v4.14 (Sequential Extraction)</h3>"),
    widgets.HBox([token_gf, token_rd]),
    show_name_override,
    text_area,
    widgets.HBox([btn, btn_subs]),
    progress_bar,
    widgets.HTML("<hr>")
])

# --- HELPER FUNCTIONS ---
def sanitize_filename(name):
    name = unquote(name)
    name = re.sub(r'[<>:"/\\|?*]', '_', name) 
    name = re.sub(r'[\s_]+', ' ', name).strip()
    return name

def clean_show_name(name):
    name = re.sub(r'(?i)(?:\[?\s*(?:ENG\s*SUB|ENGSUB|FULL)\s*\]?)', '', name)
    name = re.sub(r'[\[\]\(\)《》【】]', ' ', name)
    name = re.sub(r'[|._-]', ' ', name)
    name = re.sub(r'(?i)\s+\b(END|FINALE|FINAL)\b$', '', name)
    clean = name.strip()
    return clean if clean else "Unknown Show"

def determine_destination_path(filename, source="generic"):
    filename = sanitize_filename(filename)
    
    # DETECT MULTI-PART SUFFIX
    part_suffix = ""
    if "上篇" in filename or re.search(r'(?i)(?:Part|Pt)\.?\s*1\b', filename):
        part_suffix = "-pt1"
    elif "下篇" in filename or re.search(r'(?i)(?:Part|Pt)\.?\s*2\b', filename):
        part_suffix = "-pt2"
    elif "中篇" in filename:
        part_suffix = "-pt2"

    manual_show_name = show_name_override.value.strip()
    sxe_strict = re.search(r'(?i)\bS(\d{1,2})E(\d{1,2})\b', filename)
    sxe_loose = re.search(r'(?i)\b(?:Ep?|Episode)[ ._]?(\d{1,3})\b', filename)
    sxe_asian = re.search(r'第(\d+)集', filename)

    season_num = 1
    episode_num = 1
    is_tv = False

    if sxe_strict:
        season_num, episode_num = int(sxe_strict.group(1)), int(sxe_strict.group(2))
        show_name = clean_show_name(filename[:sxe_strict.start()])
        is_tv = True
    elif sxe_loose:
        episode_num = int(sxe_loose.group(1))
        show_name = clean_show_name(filename[:sxe_loose.start()])
        if len(show_name) < 2: show_name = clean_show_name(os.path.splitext(filename[sxe_loose.end():])[0])
        is_tv = True
    elif sxe_asian:
        episode_num = int(sxe_asian.group(1))
        show_name = clean_show_name(filename[:sxe_asian.start()])
        is_tv = True
    
    if manual_show_name:
        show_name = manual_show_name
        is_tv = True
    elif is_tv: pass
    else:
        year_match = re.search(r'\b(19|20)\d{2}\b', filename)
        if year_match: movie_name = clean_show_name(filename[:year_match.start()])
        elif source == "youtube":
            return os.path.join(f"/content/drive/My Drive/{DRIVE_YOUTUBE_PATH}", filename), "YouTube"
        else: movie_name = clean_show_name(os.path.splitext(filename)[0])
        
        full_dir = os.path.join(f"/content/drive/My Drive/{DRIVE_MOVIE_PATH}", movie_name)
        if not os.path.exists(full_dir): os.makedirs(full_dir, exist_ok=True)
        return os.path.join(full_dir, filename), "Movies"

    base_path = f"/content/drive/My Drive/{DRIVE_TV_PATH}"
    season_folder = f"Season {season_num:02d}"
    full_dir = os.path.join(base_path, show_name, season_folder)
    _, ext = os.path.splitext(filename)
    new_filename = f"{show_name} - S{season_num:02d}E{episode_num:02d}{part_suffix}{ext}"
    
    if not os.path.exists(full_dir): os.makedirs(full_dir, exist_ok=True)
    return os.path.join(full_dir, new_filename), "TV"

# --- CORE LOGIC ---
def setup_environment():
    if not os.path.exists('/content/drive'): drive.mount('/content/drive')
    for p in [DRIVE_TV_PATH, DRIVE_MOVIE_PATH, DRIVE_YOUTUBE_PATH]:
        full_p = f"/content/drive/My Drive/{p}"
        if not os.path.exists(full_p): os.makedirs(full_p)
    
    try: import yt_dlp
    except ImportError:
        print("🛠️ Installing yt-dlp...")
        subprocess.run("pip install yt-dlp", shell=True, check=True, stdout=subprocess.DEVNULL)
    
    if not shutil.which('aria2c') or not shutil.which('megadl'):
        print("🛠️ Installing tools...")
        subprocess.run("apt-get update -qq", shell=True)
        subprocess.run("apt-get install -y aria2 megatools unrar p7zip-full ffmpeg", shell=True, check=True, stdout=subprocess.DEVNULL)

def ytdl_hook(d):
    if d['status'] == 'downloading':
        try:
            p = d.get('_percent_str', '0%').replace('%','')
            progress_bar.value = float(p)
            progress_bar.description = f"YT: {p}%"
        except: pass
    elif d['status'] == 'finished':
        progress_bar.value = 100
        progress_bar.description = "Done!"

def process_youtube_link(url, mode="video"):
    import yt_dlp
    print(f"   ▶️ Processing Video: {url}")
    progress_bar.value = 0
    progress_bar.description = "Starting..."
    
    # Cookie Check
    cookie_path = '/content/cookies.txt'
    
    ydl_opts = {
        'outtmpl': '/content/%(title)s.%(ext)s',
        'quiet': True, 'no_warnings': True, 
        'restrictfilenames': False,
        'ignoreerrors': True,
        'writesubtitles': True, 'writeautomaticsub': False,
        'subtitleslangs': ['en.*', 'vi'], 'subtitlesformat': 'srt',
        'progress_hooks': [ytdl_hook],
        'noprogress': True
    }
    
    if os.path.exists(cookie_path):
        print(f"      🍪 Cookies detected! Using {cookie_path}")
        ydl_opts['cookiefile'] = cookie_path
    
    if mode == "video":
        ydl_opts['format'] = 'bestvideo+bestaudio/best'
        ydl_opts['merge_output_format'] = 'mkv'
    else: ydl_opts['skip_download'] = True 

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try: info = ydl.extract_info(url, download=False)
            except Exception as e: 
                print(f"      ❌ Skipped (Error): {e}")
                return

            if not info: return 
            entries = list(info['entries']) if 'entries' in info else [info]
            if 'entries' in info: print(f"   📜 Playlist: {info.get('title', 'Unknown')} ({len(entries)} items)")
                
            for i, entry in enumerate(entries, 1):
                if not entry: continue
                title = entry.get('title', 'Unknown')
                print(f"      [{i}/{len(entries)}] ⬇️ Downloading: {title}")
                try:
                    before = set(os.listdir('/content/'))
                    ydl.download([entry.get('webpage_url', entry.get('url'))])
                    after = set(os.listdir('/content/'))
                    new_files = list(after - before)
                    
                    if not new_files: continue
                    for f in new_files:
                        if f.endswith(('.part', '.ytdl')): continue
                        handle_file_processing(os.path.join('/content/', f), source="youtube")
                except Exception as e: print(f"      ❌ Error: {e}")
    except Exception as e:
        print(f"   ❌ YT-DLP Failed: {e}")
    progress_bar.description = "Idle"

def process_mega_link(url):
    print(f"   ☁️ Processing Mega.nz: {url}")
    progress_bar.description = "Mega DL..."
    progress_bar.value = 0
    progress_bar.bar_style = 'info'
    cmd = ['megadl', '--path', '/content/', url]
    try:
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, universal_newlines=True)
        for line in process.stdout:
            match = re.search(r'(\d+\.\d+)%', line)
            if match:
                try:
                    val = float(match.group(1))
                    progress_bar.value = val
                    progress_bar.description = f"Mega: {int(val)}%"
                except: pass
        process.wait()
        if process.returncode == 0:
            print("   ✅ Mega Download Complete")
            progress_bar.value = 100
            for f in os.listdir('/content/'):
                if f not in ['sample_data', '.config', 'drive', 'temp_extract', 'cookies.txt']: 
                    handle_file_processing(os.path.join('/content/', f), source="mega")
        else: print(f"   ❌ Mega Error (Code {process.returncode})")
    except Exception as e: print(f"   ❌ Mega Execution Error: {e}")
    progress_bar.bar_style = 'info'

def download_with_aria2(url, filename, dest_folder, cookie=None):
    filename = sanitize_filename(filename)
    final_path = os.path.join(dest_folder, filename)
    if os.path.exists(final_path) and os.path.getsize(final_path) > 1024*1024: return final_path

    print(f"   ⬇️ Downloading: {filename}")
    progress_bar.description = "Aria2 DL..."
    progress_bar.value = 0
    progress_bar.bar_style = 'warning'
    
    cmd = ['aria2c', url, '-d', dest_folder, '-o', filename, '-x', '16', '-s', '16', '-k', '1M', 
           '-c', '--file-allocation=none', '--user-agent', 'Mozilla/5.0', 
           '--connect-timeout=30', '--timeout=60', '--max-tries=3', '--retry-wait=2', '--console-log-level=warn']
    if cookie: cmd.extend(['--header', f'Cookie: accountToken={cookie}'])

    for attempt in range(1, 4):
        try:
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, universal_newlines=True)
            for line in process.stdout:
                match = re.search(r'\((\d+)%\)', line)
                if match:
                    try:
                        val = float(match.group(1))
                        progress_bar.value = val
                        progress_bar.description = f"DL: {int(val)}%"
                    except: pass
            process.wait()
            
            if process.returncode == 0 and os.path.exists(final_path):
                progress_bar.bar_style = 'info'
                return final_path
            else:
                if attempt < 3: time.sleep(2**attempt)
                else: print(f"      ❌ Aria2 Failed.")
        except Exception as e:
            print(f"      ❌ Execution Error: {e}")
            break
            
    progress_bar.bar_style = 'info'
    return None

# --- NEW: SEQUENTIAL EXTRACTION LOGIC (From v1.5) ---
def handle_file_processing(file_path, source="generic"):
    if not file_path or not os.path.exists(file_path): return
    filename = os.path.basename(file_path)
    _, ext = os.path.splitext(filename)

    # 1. NOT AN ARCHIVE? Move directly.
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
        shutil.move(file_path, final_dest)
        print(f"   ✨ Moved to {cat}: {os.path.basename(final_dest)}")
        return

    # 2. IS ARCHIVE? Sequential Extract -> Move -> Delete
    print(f"   📦 Archive Detected: {filename}")
    progress_bar.description = "Analyzing Archive..."
    extract_temp = "/content/temp_extract"
    if os.path.exists(extract_temp): shutil.rmtree(extract_temp)
    os.makedirs(extract_temp)

    # Step A: List files in archive
    archive_files = []
    try:
        if '.rar' in ext:
            # List RAR contents
            res = subprocess.run(['unrar', 'lb', file_path], capture_output=True, text=True)
            if res.returncode == 0: archive_files = res.stdout.strip().splitlines()
        else:
            # List 7Z/ZIP contents
            res = subprocess.run(['7z', 'l', '-ba', '-slt', file_path], capture_output=True, text=True)
            if res.returncode == 0:
                for line in res.stdout.splitlines():
                    if line.strip().startswith('Path = '): archive_files.append(line.split(' = ')[1])
    except: pass
    
    print(f"   🔄 Sequentially extracting {len(archive_files)} files...")

    # Step B: Loop through files
    for f_path in archive_files:
        # Ignore junk files
        if f_path.endswith(('/', '\\')) or '__MACOSX' in f_path: continue
        
        # Extract ONE file
        cmd = []
        if '.rar' in ext: cmd = ['unrar', 'x', '-o+', file_path, f_path, extract_temp]
        else: cmd = ['7z', 'x', '-y', file_path, f'-o{extract_temp}', f_path]
        
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        # Process and Move ONE file
        extracted_full = os.path.join(extract_temp, f_path)
        if os.path.exists(extracted_full) and not os.path.isdir(extracted_full):
            # Check size limit
            if os.path.getsize(extracted_full) < MIN_FILE_SIZE_MB * 1024 * 1024 and not f_path.endswith(tuple(KEEP_EXTENSIONS)):
                os.remove(extracted_full)
                continue

            final_dest, cat = determine_destination_path(f_path, source)
            
            # Ensure target dir exists (fixes "File Not Found" on move)
            if not os.path.exists(os.path.dirname(final_dest)):
                os.makedirs(os.path.dirname(final_dest))

            if os.path.exists(final_dest): os.remove(final_dest)
            shutil.move(extracted_full, final_dest)
            print(f"      -> Extracted & Moved: {os.path.basename(final_dest)}")
        
        # Clean temp immediately to save space
        if os.path.exists(extract_temp): shutil.rmtree(extract_temp, ignore_errors=True)
        os.makedirs(extract_temp)

    # Cleanup Archive
    os.remove(file_path)
    if os.path.exists(extract_temp): shutil.rmtree(extract_temp)
    progress_bar.description = "Idle"

def get_gofile_session(token):
    s = requests.Session()
    s.headers.update({'User-Agent': 'Mozilla/5.0'})
    t = {'token': token, 'wt': "4fd6sg89d7s6"}
    if not token:
        try: 
            r = s.post("https://api.gofile.io/accounts", json={})
            if r.status_code == 200: t['token'] = r.json()['data']['token']
        except: pass
    return s, t

def resolve_gofile(url, s, t):
    try:
        match = re.search(r'gofile\.io/d/([a-zA-Z0-9]+)', url)
        if not match: return []
        cid = match.group(1)
        r = s.get(f"https://api.gofile.io/contents/{cid}", params={'wt': t['wt']}, headers={'Authorization': f"Bearer {t['token']}"})
        data = r.json()
        if data['status'] == 'ok':
            return [(c['link'], c['name']) for c in data['data']['children'].values() if c.get('link')]
        else: print(f"   ❌ Gofile Error: {data.get('status')} (Premium required on Colab)")
    except: pass
    return []

def resolve_pixeldrain(url, s):
    try:
        fid = re.search(r'pixeldrain\.com/u/([a-zA-Z0-9]+)', url).group(1)
        name = s.get(f"https://pixeldrain.com/api/file/{fid}/info").json().get('name', f"pixeldrain_{fid}")
        return [(f"https://pixeldrain.com/api/file/{fid}?download", sanitize_filename(name))]
    except: return []

def process_rd_link(link, key):
    h = {"Authorization": f"Bearer {key}"}
    if "magnet:?" in link:
        print("   🧲 Resolving Magnet...")
        try:
            r = requests.post("https://api.real-debrid.com/rest/1.0/torrents/addMagnet", data={"magnet": link}, headers=h).json()
            requests.post(f"https://api.real-debrid.com/rest/1.0/torrents/selectFiles/{r['id']}", data={"files": "all"}, headers=h)
            for _ in range(30):
                i = requests.get(f"https://api.real-debrid.com/rest/1.0/torrents/info/{r['id']}", headers=h).json()
                if i['status'] == 'downloaded':
                    for l in i['links']: process_rd_link(l, key)
                    return
                time.sleep(2)
        except: pass
        return
    try:
        d = requests.post("https://api.real-debrid.com/rest/1.0/unrestrict/link", data={"link": link}, headers=h).json()
        f = download_with_aria2(d['download'], d['filename'], "/content/")
        handle_file_processing(f)
    except: pass

def execute_batch(mode):
    clear_output(wait=True)
    display(input_ui)
    btn.disabled = True
    btn_subs.disabled = True
    print(f"\n🚀 Initializing... (Mode: {mode})")
    try:
        setup_environment()
        s, t = get_gofile_session(token_gf.value.strip())
        rd = token_rd.value.strip()
        urls = [x.strip() for x in text_area.value.split('\n') if x.strip()]
        
        print(f"🚀 Processing {len(urls)} links...\n")
        video_hosts = ['youtube.com', 'youtu.be', 'twitch.tv', 'tiktok.com', 'vimeo.com', 'dailymotion.com', 'soundcloud.com']
        
        for i, url in enumerate(urls, 1):
            print(f"--- Link [{i}/{len(urls)}] ---")
            if "mega.nz" in url: process_mega_link(url)
            elif any(h in url for h in video_hosts): process_youtube_link(url, mode)
            elif "gofile.io" in url:
                for u, n in resolve_gofile(url, s, t): 
                    f = download_with_aria2(u, n, "/content/", t.get('token'))
                    handle_file_processing(f)
            elif "pixeldrain.com" in url:
                for u, n in resolve_pixeldrain(url, s):
                    f = download_with_aria2(u, n, "/content/")
                    handle_file_processing(f)
            elif "magnet:?" in url or (rd and "http" in url):
                if rd: process_rd_link(url, rd)
                else: print("   ❌ RD Token Required")
            else:
                f = download_with_aria2(url, os.path.basename(unquote(urlparse(url).path)), "/content/")
                handle_file_processing(f)
        print(f"\n✅ All Tasks Finished")
    except Exception as e: print(f"\n❌ Critical Error: {e}")
    finally:
        btn.disabled = False
        btn_subs.disabled = False

# --- BINDINGS ---
btn.on_click(lambda b: execute_batch("video"))
btn_subs.on_click(lambda b: execute_batch("subs_only"))
display(input_ui)