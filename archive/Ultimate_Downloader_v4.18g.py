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
show_name_override = widgets.Text(description='Show Name:', placeholder='Optional (Forces Name)', style={'description_width': 'initial'})
playlist_start = widgets.IntText(value=1, description='Playlist Start:', style={'description_width': 'initial'}, layout=widgets.Layout(width='150px'))

text_area = widgets.Textarea(description='Links:', placeholder='Paste Links Here (Transfer.it, Mega, YouTube, etc.)...', layout=widgets.Layout(width='98%', height='150px'))
btn = widgets.Button(description="Start Download", button_style='success', icon='download')
btn_subs = widgets.Button(description="Download Subtitles Only", button_style='info', icon='closed-captioning')
progress_bar = widgets.FloatProgress(value=0.0, min=0.0, max=100.0, description='Idle', bar_style='info', layout=widgets.Layout(width='98%'))

input_ui = widgets.VBox([
    widgets.HTML("<h3>🛡️ Ultimate Downloader v4.18 (Secure & Stable)</h3>"),
    widgets.HBox([token_gf, token_rd]),
    widgets.HBox([show_name_override, playlist_start]),
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
    # Remove release groups, resolutions, and tech tags for clean Plex matching
    name = re.sub(r'(?i)(?:\[?\s*(?:ENG\s*SUB|ENGSUB|FULL|WEB-?DL|WEBRip|BluRay|HDR|10bit|Atmos|DV|Vision|DDP\d\.\d|x265|HEVC|x264|H\.\d{3})\s*\]?)', '', name)
    name = re.sub(r'(?i)\b(2160p|1080p|720p|480p|4k|8k)\b', '', name)
    
    # Remove brackets and separators
    name = re.sub(r'[\[\]\(\)《》「」【】]', ' ', name)
    name = re.sub(r'[|._-]', ' ', name)
    
    # Remove "End" markers
    name = re.sub(r'(?i)\s+\b(END|FINALE|FINAL)\b$', '', name)
    
    clean = re.sub(r'\s+', ' ', name).strip()
    return clean if clean else "Unknown Show"

def is_safe_path(base_dir, filename):
    """Prevent directory traversal attacks (e.g. ../../etc/passwd)"""
    try:
        # Resolve the absolute path of the target
        target_path = os.path.realpath(os.path.join(base_dir, filename))
        # Resolve the absolute path of the base directory
        base_path = os.path.realpath(base_dir)
        # Check if the target is within the base
        return target_path.startswith(base_path)
    except Exception:
        return False

def check_duplicate_in_drive(filename, source="generic"):
    """Check if file already exists in Drive to avoid re-downloading"""
    dest_path, category = determine_destination_path(filename, source, dry_run=True)
    if os.path.exists(dest_path):
        file_size = os.path.getsize(dest_path) / (1024 * 1024)  # MB
        print(f"   ⏭️  SKIPPED (Already exists): {os.path.basename(dest_path)} ({file_size:.1f} MB)")
        return True
    return False

def determine_destination_path(filename, source="generic", dry_run=False):
    filename = sanitize_filename(filename)
    part_suffix = ""
    if "上篇" in filename or re.search(r'(?i)(?:Part|Pt)\.?\s*1\b', filename): part_suffix = "-pt1"
    elif "下篇" in filename or re.search(r'(?i)(?:Part|Pt)\.?\s*2\b', filename): part_suffix = "-pt2"
    elif "中篇" in filename: part_suffix = "-pt2"

    manual_show_name = show_name_override.value.strip()
    show_name = "Unknown Show" # Fix: Initialize to prevent UnboundLocalError
    
    sxe_strict = re.search(r'(?i)\bS(\d{1,2})E(\d{1,2})\b', filename)
    sxe_loose = re.search(r'(?i)\b(?:Ep?|Episode)[ ._]?(\d{1,3})\b', filename)
    sxe_asian = re.search(r'第(\d+)集', filename)

    season_num, episode_num = 1, 1
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
    
    if manual_show_name: show_name = manual_show_name; is_tv = True
    elif is_tv: pass
    else:
        year_match = re.search(r'\b(19|20)\d{2}\b', filename)
        if year_match: movie_name = clean_show_name(filename[:year_match.start()])
        elif source == "youtube":
            return os.path.join(f"/content/drive/My Drive/{DRIVE_YOUTUBE_PATH}", filename), "YouTube"
        else: movie_name = clean_show_name(os.path.splitext(filename)[0])
        full_dir = os.path.join(f"/content/drive/My Drive/{DRIVE_MOVIE_PATH}", movie_name)
        if not dry_run and not os.path.exists(full_dir): os.makedirs(full_dir, exist_ok=True)
        return os.path.join(full_dir, filename), "Movies"

    base_path = f"/content/drive/My Drive/{DRIVE_TV_PATH}"
    season_folder = f"Season {season_num:02d}"
    full_dir = os.path.join(base_path, show_name, season_folder)
    _, ext = os.path.splitext(filename)
    new_filename = f"{show_name} - S{season_num:02d}E{episode_num:02d}{part_suffix}{ext}"
    if not dry_run and not os.path.exists(full_dir): os.makedirs(full_dir, exist_ok=True)
    return os.path.join(full_dir, new_filename), "TV"

# --- CORE LOGIC ---
def setup_environment(needs_mega, needs_ytdlp, needs_aria):
    if not os.path.exists('/content/drive'): drive.mount('/content/drive')
    for p in [DRIVE_TV_PATH, DRIVE_MOVIE_PATH, DRIVE_YOUTUBE_PATH]:
        full_p = f"/content/drive/My Drive/{p}"
        if not os.path.exists(full_p): os.makedirs(full_p)
    
    if needs_ytdlp:
        try: import yt_dlp
        except ImportError:
            print("🛠️ Installing yt-dlp...")
            # Fix: Use list format for shell safety
            subprocess.run(["pip", "install", "yt-dlp"], check=True, stdout=subprocess.DEVNULL)
    else:
        print("⭐️ Skipping yt-dlp (Not needed)")

    # Fix: Map packages to actual binaries for correct checking
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
    
    # Check if binaries exist, if not, add package to install list
    to_install = [pkg for pkg in needed_pkgs if not shutil.which(pkg_map[pkg])]

    if len(to_install) > 0:
        print(f"🛠️ Installing tools: {', '.join(to_install)}...")
        subprocess.run(["apt-get", "update", "-qq"], check=True)
        # apt-get still needs string/shell for multiple packages usually, or multiple calls.
        # Safe enough here since packages are hardcoded strings.
        cmd = f"apt-get install -y {' '.join(to_install)}"
        subprocess.run(cmd, shell=True, check=True, stdout=subprocess.DEVNULL)
    else:
        print("✅ Required tools already present.")

def ytdl_hook(d):
    if d['status'] == 'downloading':
        try:
            p = d.get('_percent_str', '0%').replace('%','')
            speed = d.get('_speed_str', 'N/A')
            progress_bar.value = float(p)
            progress_bar.description = f"YT: {p}% ({speed})"
        except: pass
    elif d['status'] == 'finished':
        progress_bar.value = 100
        progress_bar.description = "Done!"

def process_youtube_link(url, mode="video"):
    import yt_dlp
    print(f"   ▶️ Processing Video: {url}")
    progress_bar.value = 0
    progress_bar.description = "Starting..."
    
    cookie_path = '/content/cookies.txt'
    archive_path = '/content/drive/My Drive/yt_history.txt'
    
    ydl_opts = {
        'outtmpl': '/content/%(title)s.%(ext)s', 
        'quiet': True, 'no_warnings': True, 
        'restrictfilenames': False, 
        'ignoreerrors': True, 
        'writesubtitles': True, 
        'subtitleslangs': ['en.*', 'vi'], 
        'subtitlesformat': 'srt', 
        'progress_hooks': [ytdl_hook], 
        'noprogress': True,
        'download_archive': archive_path,
        'playliststart': playlist_start.value
    }
    
    if os.path.exists(cookie_path): 
        print(f"      🍪 Cookies detected! Using {cookie_path}")
        ydl_opts['cookiefile'] = cookie_path
    
    if mode == "video": ydl_opts['format'] = 'bestvideo+bestaudio/best'; ydl_opts['merge_output_format'] = 'mkv'
    else: ydl_opts['skip_download'] = True 
    
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
            print(f"   📜 Playlist found: {total_items} items (Starting from #{playlist_start.value})")
            
            for i, entry in enumerate(entries, 1):
                if not entry: continue
                title = entry.get('title', 'Unknown')
                
                # Check for duplicate before downloading (Assume MKV based on config)
                temp_filename = f"{title}.mkv"
                if check_duplicate_in_drive(temp_filename, source="youtube"):
                    continue
                
                print(f"      Downloading: {title}")
                
                try:
                    before = set(os.listdir('/content/'))
                    ydl.download([entry.get('webpage_url', entry.get('url'))])
                    after = set(os.listdir('/content/'))
                    new_files = list(after - before)
                    
                    if not new_files: continue
                    for f in new_files:
                        if f.endswith(('.part', '.ytdl')): continue
                        handle_file_processing(os.path.join('/content/', f), source="youtube")
                except Exception as e:
                    print(f"      ❌ Failed to download {title}: {str(e)[:80]}")
    except Exception as e:
        print(f"   ❌ YouTube processing failed: {str(e)[:100]}")
    progress_bar.description = "Idle"

def process_mega_link(url):
    print(f"   ☁️ Processing Mega: {url}")
    progress_bar.description = "Mega DL..."
    progress_bar.value = 0
    progress_bar.bar_style = 'info'
    cmd = ['megadl', '--path', '/content/', url]
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
                    progress_bar.value = val
                    progress_bar.description = f"Mega: {int(val)}% ({speed_str})"
                except: pass
        process.wait()
        if process.returncode == 0:
            print("   ✅ Mega Download Complete")
            progress_bar.value = 100
            for f in os.listdir('/content/'):
                if f not in ['sample_data', '.config', 'drive', 'temp_extract', 'cookies.txt']: 
                    handle_file_processing(os.path.join('/content/', f), source="mega")
        else: 
            print(f"   ❌ Mega Error (Code {process.returncode}) - Possible causes: Invalid link, auth required, or file not found")
    except Exception as e: 
        print(f"   ❌ Mega Execution Error: {e}")
    progress_bar.bar_style = 'info'

def download_with_aria2(url, filename, dest_folder, cookie=None):
    filename = sanitize_filename(filename)
    
    if check_duplicate_in_drive(filename):
        return None
    
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
            last_speed = ""
            for line in process.stdout:
                match = re.search(r'\((\d+)%\)', line)
                speed_match = re.search(r'DL:(\d+\.?\d*[KMG]iB/s)', line)
                if match:
                    try: 
                        val = float(match.group(1))
                        speed_str = speed_match.group(1) if speed_match else last_speed
                        if speed_match: last_speed = speed_str
                        progress_bar.value = val
                        progress_bar.description = f"DL: {int(val)}% ({speed_str})"
                    except: pass
            process.wait()
            if process.returncode == 0 and os.path.exists(final_path): 
                progress_bar.bar_style = 'info'
                return final_path
            else: 
                print(f"      ⚠️ Retry {attempt}/3 - Download incomplete")
                time.sleep(2**attempt)
        except Exception as e:
            print(f"      ❌ Download error (attempt {attempt}/3): {str(e)[:80]}")
            break
    
    print(f"   ❌ Download failed after 3 attempts - Check URL validity or network connection")
    progress_bar.bar_style = 'info'
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
            parts = filename.split('.'); lang = parts[-2] if len(parts) >= 3 and len(parts[-2]) in [2, 3] else ""; base = os.path.splitext(final_dest)[0]; final_dest = f"{base}.{lang}.srt" if lang else f"{base}.srt"
        if os.path.exists(final_dest): os.remove(final_dest)
        
        if not os.path.exists(os.path.dirname(final_dest)): os.makedirs(os.path.dirname(final_dest))
        shutil.move(file_path, final_dest)
        print(f"   ✨ Moved to {cat}: {os.path.basename(final_dest)}")
        return

    print(f"   📦 Archive Detected: {filename}")
    extract_temp = "/content/temp_extract"
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
        
        # Fix: Security check for Path Traversal
        if not is_safe_path(extract_temp, f_path):
            print(f"      ⚠️ SKIPPING UNSAFE PATH: {f_path}")
            continue

        extracted_count += 1
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
            
            # Post-extract Duplicate Check
            if os.path.exists(final_dest):
                print(f"      -> ⚠️ Duplicate in Drive (Deleted): {os.path.basename(final_dest)}")
                os.remove(extracted_full)
                continue

            if not os.path.exists(os.path.dirname(final_dest)): os.makedirs(os.path.dirname(final_dest))
            shutil.move(extracted_full, final_dest)
            print(f"      [{extracted_count}/{total_files}] -> {os.path.basename(final_dest)}")
        
        if os.path.exists(extract_temp): shutil.rmtree(extract_temp, ignore_errors=True)
        os.makedirs(extract_temp)

    os.remove(file_path)
    if os.path.exists(extract_temp): shutil.rmtree(extract_temp)
    progress_bar.description = "Idle"
    print(f"   ✅ Extraction complete: {extracted_count} files processed")

def get_gofile_session(token):
    s = requests.Session(); s.headers.update({'User-Agent': 'Mozilla/5.0'}); t = {'token': token, 'wt': "4fd6sg89d7s6"}
    if not token:
        try: r = s.post("https://api.gofile.io/accounts", json={}); t['token'] = r.json()['data']['token'] if r.status_code == 200 else None
        except: pass
    return s, t

def resolve_gofile(url, s, t):
    try:
        match = re.search(r'gofile\.io/d/([a-zA-Z0-9]+)', url)
        if not match: return []
        r = s.get(f"https://api.gofile.io/contents/{match.group(1)}", params={'wt': t['wt']}, headers={'Authorization': f"Bearer {t['token']}"})
        data = r.json()
        if data['status'] == 'ok': return [(c['link'], c['name']) for c in data['data']['children'].values() if c.get('link')]
        else:
            print(f"   ❌ Gofile Error: {data.get('status', 'unknown')} - Check if link is valid or requires authentication")
    except Exception as e:
        print(f"   ❌ Gofile API Error: {str(e)[:80]}")
    return [] # Fix: Ensure list is returned on error

def resolve_pixeldrain(url, s):
    try:
        fid = re.search(r'pixeldrain\.com/u/([a-zA-Z0-9]+)', url).group(1)
        name = s.get(f"https://pixeldrain.com/api/file/{fid}/info").json().get('name', f"pixeldrain_{fid}")
        return [(f"https://pixeldrain.com/api/file/{fid}?download", sanitize_filename(name))]
    except Exception as e:
        print(f"   ❌ Pixeldrain Error: {str(e)[:80]} - File may not exist or be private")
    return []

def process_rd_link(link, key):
    h = {"Authorization": f"Bearer {key}"}
    if "magnet:?" in link:
        print("   🧲 Resolving Magnet...")
        try:
            r = requests.post("https://api.real-debrid.com/rest/1.0/torrents/addMagnet", data={"magnet": link}, headers=h).json()
            if 'error' in r:
                print(f"   ❌ RD Magnet Error: {r.get('error', 'Unknown')} - Check token or magnet validity")
                return
            requests.post(f"https://api.real-debrid.com/rest/1.0/torrents/selectFiles/{r['id']}", data={"files": "all"}, headers=h)
            for _ in range(30):
                i = requests.get(f"https://api.real-debrid.com/rest/1.0/torrents/info/{r['id']}", headers=h).json()
                if i['status'] == 'downloaded':
                    for l in i['links']: process_rd_link(l, key); return
                time.sleep(2)
            print("   ❌ RD Timeout - Torrent took too long to download")
        except Exception as e:
            print(f"   ❌ RD Magnet Error: {str(e)[:80]}")
        return
    try:
        d = requests.post("https://api.real-debrid.com/rest/1.0/unrestrict/link", data={"link": link}, headers=h).json()
        if 'error' in d:
            print(f"   ❌ RD Unrestrict Error: {d.get('error', 'Unknown')} - Check if link is supported")
            return
        f = download_with_aria2(d['download'], d['filename'], "/content/")
        if f: handle_file_processing(f)
    except Exception as e:
        print(f"   ❌ RD Error: {str(e)[:80]}")

def execute_batch(mode):
    clear_output(wait=True); display(input_ui); btn.disabled = True; btn_subs.disabled = True
    print(f"\n🚀 Initializing... (Mode: {mode})")
    try:
        urls = [x.strip() for x in text_area.value.split('\n') if x.strip()]
        
        needs_ytdlp = any(h in u for u in urls for h in ['youtube.com', 'youtu.be', 'twitch.tv', 'tiktok.com', 'vimeo.com', 'dailymotion.com', 'soundcloud.com'])
        needs_mega = any("mega.nz" in u or "transfer.it" in u for u in urls)
        needs_aria = not (needs_ytdlp and not needs_mega) or any(h in u for u in urls for h in ["gofile.io", "pixeldrain.com", "magnet:", "real-debrid"])
        
        setup_environment(needs_mega, needs_ytdlp, needs_aria)
        
        s, t = get_gofile_session(token_gf.value.strip()); rd = token_rd.value.strip()
        print(f"🚀 Processing {len(urls)} links...\n")
        
        for i, url in enumerate(urls, 1):
            print(f"--- Link [{i}/{len(urls)}] ---")
            if "mega.nz" in url or "transfer.it" in url: process_mega_link(url)
            elif needs_ytdlp and any(h in url for h in ['youtube', 'youtu.be', 'vimeo', 'twitch']): process_youtube_link(url, mode)
            elif "gofile.io" in url:
                for u, n in resolve_gofile(url, s, t): 
                    f = download_with_aria2(u, n, "/content/", t.get('token'))
                    if f: handle_file_processing(f)
            elif "pixeldrain.com" in url:
                for u, n in resolve_pixeldrain(url, s): 
                    f = download_with_aria2(u, n, "/content/")
                    if f: handle_file_processing(f)
            elif "magnet:?" in url or (rd and "http" in url):
                if rd: process_rd_link(url, rd)
                else: print("   ❌ RD Token Required for magnets/premium links")
            else: 
                f = download_with_aria2(url, os.path.basename(unquote(urlparse(url).path)), "/content/")
                if f: handle_file_processing(f)
        print(f"\n✅ All Tasks Finished")
    except Exception as e: print(f"\n❌ Critical Error: {e}")
    finally: btn.disabled = False; btn_subs.disabled = False; progress_bar.description = "Idle"

# --- BINDINGS ---
btn.on_click(lambda b: execute_batch("video"))
btn_subs.on_click(lambda b: execute_batch("subs_only"))
display(input_ui)