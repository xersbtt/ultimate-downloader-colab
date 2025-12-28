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

# --- GLOBAL LOGS ---
report_log = {"TV": [], "Movies": [], "YouTube": [], "Failed": []}
start_time_global = 0

# --- HELPER: TEXT SANITISATION & PATHS ---

def sanitize_filename(name):
    """Removes characters that break Plex or Windows/Linux filesystems."""
    name = unquote(name)
    name = re.sub(r'[<>:"/\\|?*]', '_', name) 
    name = "".join(c for c in name if c.isprintable())
    name = re.sub(r'[\s_]+', ' ', name).strip()
    return name

def clean_show_name(name):
    """
    Specific cleaner for YouTube/Scene titles.
    Removes: [ENG SUB], ENG SUB, brackets [], and other noise.
    """
    # 1. Remove "ENG SUB" variations
    name = re.sub(r'(?i)(?:\[?\s*ENG\s*SUB\s*\]?|\[?\s*ENGSUB\s*\]?)', '', name)
    
    # 2. Remove "Multi Sub" / "Indo Sub" variations if common, or just generic brackets
    # Remove surrounding brackets [Name] -> Name
    name = re.sub(r'[\[\]]', ' ', name)
    
    # 3. Replace dots/underscores/dashes with space
    name = re.sub(r'[._-]', ' ', name)
    
    # 4. Collapse multiple spaces
    return re.sub(r'\s+', ' ', name).strip()

def determine_destination_path(filename, source="generic"):
    """
    Decides destination. Handles standard S01E01 and Asian Drama 'Ep01' formats.
    """
    filename = sanitize_filename(filename)
    
    # 1. STRICT TV CHECK (SxxExx)
    sxe_strict = re.search(r'(?i)\bS(\d{1,2})E(\d{1,2})\b', filename)
    
    # 2. LOOSE TV CHECK (Exx / Ep01)
    sxe_loose = re.search(r'(?i)\b(?:Ep?|Episode)[ ._]?(\d{1,3})\b', filename)

    if sxe_strict:
        season_num = int(sxe_strict.group(1))
        episode_num = int(sxe_strict.group(2))
        
        # Extract Show Name
        raw_show_name = filename[:sxe_strict.start()]
        show_name = clean_show_name(raw_show_name)
        show_name = re.sub(r'(?i)Season\s*\d+$', '', show_name).strip()
        
        # Clean Filename (Optional: Rename to Show - S01E01.ext)
        # For now, we keep original filename but cleaned path
        category = "TV"
        
    elif sxe_loose:
        season_num = 1 
        episode_num = int(sxe_loose.group(1))
        
        raw_show_name = filename[:sxe_loose.start()]
        show_name = clean_show_name(raw_show_name)
        category = "TV"
        
        # --- NEW: Filename Cleaner for YouTube ---
        # YouTube titles often have junk after the Ep number: "Show EP01 | The Plot Summary..."
        # We want to strip that summary.
        _, ext = os.path.splitext(filename)
        new_filename = f"{show_name} - S01E{episode_num:02d}{ext}"
        
        # We only rename if it came from YouTube/Loose logic to keep it clean
        filename = new_filename
        
    else:
        sxe_strict = None
        sxe_loose = None
        category = "Movies" 

    # --- BUILD PATHS ---
    if category == "TV":
        if len(show_name) < 2: show_name = "Unknown Show"
        base_path = f"/content/drive/My Drive/{DRIVE_TV_PATH}"
        season_folder = f"Season {season_num:02d}"
        full_dir = os.path.join(base_path, show_name, season_folder)
        
    else:
        # --- MOVIE vs YOUTUBE LOGIC ---
        year_match = re.search(r'\b(19|20)\d{2}\b', filename)
        
        if year_match:
            movie_name = filename[:year_match.start()]
            movie_name = clean_show_name(movie_name)
            base_path = f"/content/drive/My Drive/{DRIVE_MOVIE_PATH}"
            full_dir = os.path.join(base_path, movie_name)
            category = "Movies"
        elif source == "youtube":
            base_path = f"/content/drive/My Drive/{DRIVE_YOUTUBE_PATH}"
            full_dir = base_path
            category = "YouTube"
        else:
            movie_name = os.path.splitext(filename)[0]
            movie_name = clean_show_name(movie_name)
            base_path = f"/content/drive/My Drive/{DRIVE_MOVIE_PATH}"
            full_dir = os.path.join(base_path, movie_name)
            category = "Movies"

    if not os.path.exists(full_dir):
        os.makedirs(full_dir, exist_ok=True)
        
    return os.path.join(full_dir, filename), category

# --- SYSTEM SETUP ---

def setup_environment():
    if not os.path.exists('/content/drive'):
        drive.mount('/content/drive')
    
    for p in [DRIVE_TV_PATH, DRIVE_MOVIE_PATH, DRIVE_YOUTUBE_PATH]:
        full_p = f"/content/drive/My Drive/{p}"
        if not os.path.exists(full_p):
            os.makedirs(full_p)

    try:
        import yt_dlp
    except ImportError:
        print("🛠️ Installing yt-dlp...")
        subprocess.run("pip install yt-dlp", shell=True, check=True, stdout=subprocess.DEVNULL)

    required_tools = ['aria2c', '7z', 'unrar', 'ffmpeg']
    missing = [t for t in required_tools if not shutil.which(t)]
    
    if missing:
        print(f"🛠️ Installing tools ({', '.join(missing)})...")
        subprocess.run("apt-get update -qq", shell=True)
        subprocess.run("apt-get install -y aria2 unrar p7zip-full ffmpeg", shell=True, check=True, stdout=subprocess.DEVNULL)

# --- MODULE: YOUTUBE DOWNLOADER ---

def process_youtube_link(url):
    import yt_dlp
    print(f"   ▶️ Processing YouTube: {url}")
    
    ydl_opts = {
        'format': 'bestvideo+bestaudio/best',
        'merge_output_format': 'mkv',
        'outtmpl': '/content/%(title)s.%(ext)s',
        'quiet': True,
        'no_warnings': True,
        'restrictfilenames': True, 
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if 'entries' in info: entries = info['entries']
            else: entries = [info]
                
            for entry in entries:
                filename = ydl.prepare_filename(entry)
                base, _ = os.path.splitext(filename)
                potential_files = [f for f in os.listdir('/content/') if f.startswith(os.path.basename(base))]
                
                if potential_files:
                    best_match = max([os.path.join('/content/', f) for f in potential_files], key=os.path.getsize)
                    handle_file_processing(best_match, source="youtube")
                else:
                    print(f"   ⚠️ Could not locate downloaded file for: {entry.get('title')}")

    except Exception as e:
        print(f"   ❌ YouTube Error: {e}")
        report_log["Failed"].append(url)

# --- HELPER: ARIA2 DOWNLOADER ---

def download_with_aria2(url, filename, dest_folder, cookie=None):
    filename = sanitize_filename(filename)
    final_path = os.path.join(dest_folder, filename)

    if os.path.exists(final_path):
        size_mb = os.path.getsize(final_path) / (1024 * 1024)
        if size_mb > 1:
            print(f"   ⚠️ File '{filename}' exists (~{size_mb:.1f} MB). Skipping.")
            return final_path

    con_limit = '16'
    if "pixeldrain.com" in url: con_limit = '4'
        
    print(f"   ⬇️ Downloading: {filename} (Connections: {con_limit})")
    
    cmd = [
        'aria2c', url, '-d', dest_folder, '-o', filename,
        '-x', con_limit, '-s', con_limit, '-k', '1M', 
        '--file-allocation=none', '--summary-interval=5',
        '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
    ]
    if cookie:
        cmd.extend(['--header', f'Cookie: accountToken={cookie}'])

    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            for line in process.stdout:
                if '[' in line and 'ERR' not in line: print(line.strip())     
            process.wait()
            
            if process.returncode == 0 and os.path.exists(final_path):
                print(f"   ✅ Download Completed: {filename}")
                return final_path
            else:
                if attempt < max_retries:
                    print(f"   ❌ Attempt {attempt} failed. Sleeping 60s...")
                    time.sleep(60)
                else:
                    print("   ❌ All attempts failed.")
                    report_log["Failed"].append(filename)
                    return None
        except Exception as e:
            print(f"   ❌ Critical Error: {e}"); return None

# --- PROCESSING ---

def handle_file_processing(file_path, source="generic"):
    if not file_path or not os.path.exists(file_path): return

    filename = os.path.basename(file_path)
    lower_name = filename.lower()
    _, ext = os.path.splitext(lower_name)
    
    # CASE A: NOT AN ARCHIVE (Movies/MKVs/MP4s)
    if ext not in ['.rar', '.zip', '.7z']:
        final_dest, cat = determine_destination_path(filename, source)
        if os.path.exists(final_dest): os.remove(final_dest)
        shutil.move(file_path, final_dest)
        print(f"   ✨ Moved to {cat}: {os.path.basename(os.path.dirname(final_dest))}/{os.path.basename(final_dest)}")
        report_log[cat].append(os.path.basename(final_dest))
        return

    # CASE B: IS ARCHIVE
    print(f"   📦 Archive detected ({ext}). Extracting...")
    extract_temp = "/content/temp_extract"
    if os.path.exists(extract_temp): shutil.rmtree(extract_temp, ignore_errors=True)
    os.makedirs(extract_temp)

    files = []
    try:
        if '.rar' in ext:
            res = subprocess.run(['unrar', 'lb', file_path], capture_output=True, text=True)
            if res.returncode == 0: files = res.stdout.strip().splitlines()
        else:
            res = subprocess.run(['7z', 'l', '-ba', '-slt', file_path], capture_output=True, text=True)
            if res.returncode == 0:
                for line in res.stdout.splitlines():
                    if line.strip().startswith('Path = '): files.append(line.split(' = ')[1])
    except: return

    print(f"   🔄 Extracting {len(files)} files sequentially...")
    
    for f_path in files:
        cmd = []
        if '.rar' in ext: cmd = ['unrar', 'x', '-o+', file_path, f_path, extract_temp]
        else: cmd = ['7z', 'x', '-y', file_path, f'-o{extract_temp}', f_path]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        extracted_full = os.path.join(extract_temp, f_path)
        if os.path.exists(extracted_full) and not os.path.isdir(extracted_full):
            clean_name = sanitize_filename(os.path.basename(f_path))
            file_ext = os.path.splitext(clean_name)[1].lower()
            f_size_mb = os.path.getsize(extracted_full) / (1024 * 1024)
            
            if f_size_mb < MIN_FILE_SIZE_MB and file_ext not in KEEP_EXTENSIONS:
                print(f"      -> 🗑️ Skipped junk ({f_size_mb:.1f} MB)")
                os.remove(extracted_full); continue

            final_dest, cat = determine_destination_path(clean_name, source)
            if os.path.exists(final_dest): os.remove(final_dest)
            shutil.move(extracted_full, final_dest)
            print(f"      -> Extracted to {cat}: {clean_name}")
            report_log[cat].append(clean_name)
        
        if os.path.exists(extract_temp): shutil.rmtree(extract_temp, ignore_errors=True)
        os.makedirs(extract_temp)

    os.remove(file_path)
    print("   🗑️ Original archive deleted.")

# --- LINK RESOLVERS ---

def get_gofile_session(custom_token=None):
    session = requests.Session()
    session.headers.update({'User-Agent': 'Mozilla/5.0'})
    tokens = {'token': custom_token, 'wt': "4fd6sg89d7s6"}
    if not tokens['token']:
        try:
            r = session.post("https://api.gofile.io/accounts", json={}, timeout=15)
            if r.status_code == 200: tokens['token'] = r.json()['data']['token']
        except: pass
    return session, tokens

def resolve_gofile(url, session, tokens):
    match = re.search(r'gofile\.io/d/([a-zA-Z0-9]+)', url)
    if not match: return []
    content_id = match.group(1)
    for _ in range(3):
        if not tokens['token']: return []
        try:
            r = session.get(f"https://api.gofile.io/contents/{content_id}", 
                          params={'wt': tokens['wt']}, 
                          headers={'Authorization': f"Bearer {tokens['token']}"}, timeout=30)
            if r.status_code == 429: time.sleep(30); continue
            data = r.json()
            if data.get('status') == 'ok':
                results = []
                for child in data['data'].get('children', {}).values():
                    if child.get('link') and child.get('name'):
                        results.append((child['link'], child['name']))
                return results
            else: break
        except: time.sleep(2)
    return []

def resolve_pixeldrain(url, session):
    match = re.search(r'pixeldrain\.com/u/([a-zA-Z0-9]+)', url)
    if not match: return []
    fid = match.group(1)
    name = f"pixeldrain_{fid}.file"
    try:
        r = session.get(f"https://pixeldrain.com/api/file/{fid}/info", timeout=15)
        if r.status_code == 200: name = sanitize_filename(r.json().get('name', name))
    except: pass
    return [(f"https://pixeldrain.com/api/file/{fid}?download", name)]

def process_rd_link(link, rd_token):
    headers = {"Authorization": f"Bearer {rd_token}"}
    if "magnet:?" in link:
        print("   🧲 Processing Magnet...")
        try:
            r = requests.post("https://api.real-debrid.com/rest/1.0/torrents/addMagnet", 
                              data={"magnet": link}, headers=headers, timeout=30)
            if r.status_code != 201: print("   ❌ Failed to add magnet."); return
            tid = r.json()['id']
            requests.post(f"https://api.real-debrid.com/rest/1.0/torrents/selectFiles/{tid}", 
                          data={"files": "all"}, headers=headers, timeout=30)
            start_time = time.time()
            while True:
                if time.time() - start_time > 600: print("   ❌ Magnet timed out."); return
                info = requests.get(f"https://api.real-debrid.com/rest/1.0/torrents/info/{tid}", 
                                    headers=headers, timeout=30).json()
                if info['status'] == 'downloaded':
                    print("   ✅ Magnet Cached.")
                    for direct_link in info['links']: process_rd_link(direct_link, rd_token)
                    return
                elif info['status'] in ['error', 'dead']: print("   ❌ Magnet dead."); return
                time.sleep(2)
        except: return
    try:
        r = requests.post("https://api.real-debrid.com/rest/1.0/unrestrict/link", 
                          data={"link": link}, headers=headers, timeout=30)
    except: return
    if r.status_code == 200:
        data = r.json()
        temp_file = download_with_aria2(data['download'], data.get('filename', 'rd_dl'), "/content/")
        handle_file_processing(temp_file)
    else:
        path = urlparse(link).path
        name = os.path.basename(unquote(path)) or f"file_{int(time.time())}"
        temp_file = download_with_aria2(link, name, "/content/")
        handle_file_processing(temp_file)

# --- MAIN UI ---

def on_start(b):
    global start_time_global
    start_time_global = time.time()
    clear_output(wait=True)
    display(widgets.VBox([token_gf, token_rd, text_area, btn]))
    for k in report_log: report_log[k] = [] 

    setup_environment()
    session, gf_tokens = get_gofile_session(token_gf.value.strip())
    rd_key = token_rd.value.strip()
    urls = [line.strip() for line in text_area.value.split('\n') if line.strip()]
    
    print(f"\n🚀 Processing {len(urls)} Links...\n")
    
    for i, url in enumerate(urls, 1):
        print(f"--- Link [{i}/{len(urls)}] ---")
        
        if "youtube.com" in url or "youtu.be" in url:
            process_youtube_link(url)
            
        elif "gofile.io" in url:
            files = resolve_gofile(url, session, gf_tokens)
            for d_url, name in files:
                temp_file = download_with_aria2(d_url, name, "/content/", gf_tokens['token'])
                handle_file_processing(temp_file)
                time.sleep(2)
                
        elif "pixeldrain.com" in url:
            files = resolve_pixeldrain(url, session)
            for d_url, name in files:
                temp_file = download_with_aria2(d_url, name, "/content/")
                handle_file_processing(temp_file)
                time.sleep(5)
                
        elif "magnet:?" in url or (rd_key and "http" in url):
            if rd_key: process_rd_link(url, rd_key)
            else: print("   ❌ RD Token missing.")
                
        else:
            path = urlparse(url).path
            name = os.path.basename(unquote(path)) or f"file_{int(time.time())}"
            temp_file = download_with_aria2(url, name, "/content/")
            handle_file_processing(temp_file)

    elapsed = time.time() - start_time_global
    print("\n" + "="*40)
    print(f"📝 MISSION REPORT (Time: {int(elapsed//60)}m {int(elapsed%60)}s)")
    print("="*40)
    for k in ["TV", "Movies", "YouTube", "Failed"]:
        if report_log[k]:
            print(f"{k} ({len(report_log[k])}):")
            for item in report_log[k]: print(f"  - {item}")
    print("="*40 + "\n✅ All Tasks Finished.")

# --- WIDGETS ---
print("👇 Configuration & Links:")
token_gf = widgets.Text(description='Gofile Token:', placeholder='Optional')
token_rd = widgets.Text(description='RD Token:', placeholder='Real-Debrid API Key')
text_area = widgets.Textarea(description='Links:', placeholder='Paste Magnets/YouTube/Links Here...', layout=widgets.Layout(width='100%', height='200px'))
btn = widgets.Button(description="Start Download", button_style='success')
btn.on_click(on_start)
display(widgets.VBox([token_gf, token_rd, text_area, btn]))