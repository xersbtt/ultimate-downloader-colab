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
MIN_FILE_SIZE_MB = 15  # Delete small files (samples, nfo, txt)
KEEP_EXTENSIONS = {'.srt', '.ass', '.sub', '.vtt'} # ...Unless they are subtitles

# --- GLOBAL LOGS ---
report_log = {"TV": [], "Movies": [], "Failed": []}
start_time_global = 0

# --- HELPER: TEXT SANITISATION & PATHS ---

def sanitize_filename(name):
    """Removes characters that break Plex or Windows/Linux filesystems."""
    name = unquote(name)
    name = re.sub(r'[<>:"/\\|?*]', '_', name) 
    name = "".join(c for c in name if c.isprintable())
    name = re.sub(r'[\s_]+', ' ', name).strip()
    return name

def determine_destination_path(filename):
    """Decides if file is TV or Movie and builds the correct path."""
    filename = sanitize_filename(filename)
    
    # 1. STRICT TV CHECK (SxxExx)
    sxe_strict = re.search(r'(?i)\bS(\d{1,2})E(\d{1,2})\b', filename)
    
    # 2. LOOSE TV CHECK (Exx / Ep01)
    sxe_loose = re.search(r'(?i)\b(?:Ep?|Episode)[ ._]?(\d{1,3})\b', filename)

    if sxe_strict:
        season_num = int(sxe_strict.group(1))
        show_name = filename[:sxe_strict.start()]
        show_name = re.sub(r'[._-]', ' ', show_name).strip()
        show_name = re.sub(r'(?i)Season\s*\d+$', '', show_name).strip()
        
    elif sxe_loose:
        season_num = 1 
        show_name = filename[:sxe_loose.start()]
        show_name = re.sub(r'[._-]', ' ', show_name).strip()
    else:
        sxe_strict = None
        sxe_loose = None

    if sxe_strict or sxe_loose:
        if len(show_name) < 2: show_name = "Unknown Show"
        base_path = f"/content/drive/My Drive/{DRIVE_TV_PATH}"
        season_folder = f"Season {season_num:02d}"
        full_dir = os.path.join(base_path, show_name, season_folder)
        category = "TV"
    else:
        year_match = re.search(r'\b(19|20)\d{2}\b', filename)
        if year_match:
            movie_name = filename[:year_match.start()]
        else:
            movie_name = os.path.splitext(filename)[0]
        movie_name = re.sub(r'[._-]', ' ', movie_name).strip()
        if len(movie_name) < 2: movie_name = "Unknown Movie"

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
    
    for p in [DRIVE_TV_PATH, DRIVE_MOVIE_PATH]:
        full_p = f"/content/drive/My Drive/{p}"
        if not os.path.exists(full_p):
            os.makedirs(full_p)

    required_tools = ['aria2c', '7z', 'unrar']
    missing = [t for t in required_tools if not shutil.which(t)]
    
    if missing:
        print(f"🛠️ Installing tools ({', '.join(missing)})...")
        subprocess.run("apt-get update -qq", shell=True)
        subprocess.run("apt-get install -y aria2 unrar p7zip-full", shell=True, check=True, stdout=subprocess.DEVNULL)

# --- HELPER: ARIA2 DOWNLOADER (WITH RETRY & SMART THROTTLE) ---

def download_with_aria2(url, filename, dest_folder, cookie=None):
    filename = sanitize_filename(filename)
    final_path = os.path.join(dest_folder, filename)

    if os.path.exists(final_path):
        size_mb = os.path.getsize(final_path) / (1024 * 1024)
        if size_mb > 1:
            print(f"   ⚠️ File '{filename}' exists (~{size_mb:.1f} MB). Skipping.")
            return final_path

    # ADAPTIVE CONNECTIONS
    # Default is fast (16), but Pixeldrain gets slow (4)
    con_limit = '16'
    if "pixeldrain.com" in url:
        con_limit = '4'
        
    print(f"   ⬇️ Downloading: {filename} (Connections: {con_limit})")
    
    cmd = [
        'aria2c', url, '-d', dest_folder, '-o', filename,
        '-x', con_limit, '-s', con_limit, '-k', '1M', 
        '--file-allocation=none', '--summary-interval=5',
        '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
    ]
    if cookie:
        cmd.extend(['--header', f'Cookie: accountToken={cookie}'])

    # RETRY LOOP (3 attempts)
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            
            for line in process.stdout:
                if '[' in line and 'ERR' not in line:
                    print(line.strip())
                    
            process.wait()
            
            if process.returncode == 0 and os.path.exists(final_path):
                print(f"   ✅ Download Completed: {filename}")
                return final_path
            else:
                # If failed, check if we should retry
                if attempt < max_retries:
                    wait_time = 60
                    print(f"   ❌ Attempt {attempt} failed. Sleeping {wait_time}s before retry...")
                    time.sleep(wait_time)
                else:
                    print("   ❌ All download attempts failed.")
                    report_log["Failed"].append(filename)
                    return None
        except Exception as e:
            print(f"   ❌ Critical Error: {e}")
            return None

# --- PROCESSING: EXTRACTION & SORTING ---

def handle_file_processing(file_path):
    if not file_path or not os.path.exists(file_path):
        return

    filename = os.path.basename(file_path)
    lower_name = filename.lower()
    _, ext = os.path.splitext(lower_name)
    
    # CASE A: NOT AN ARCHIVE
    if ext not in ['.rar', '.zip', '.7z']:
        final_dest, cat = determine_destination_path(filename)
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
    except Exception as e:
        print(f"   ❌ Error reading archive: {e}"); return

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
                print(f"      -> 🗑️ Skipped junk ({f_size_mb:.1f} MB): {os.path.basename(f_path)}")
                os.remove(extracted_full)
                continue

            final_dest, cat = determine_destination_path(clean_name)
            
            if os.path.exists(final_dest): os.remove(final_dest)
            shutil.move(extracted_full, final_dest)
            print(f"      -> Extracted to {cat}: {clean_name}")
            report_log[cat].append(clean_name)
        
        if os.path.exists(extract_temp): shutil.rmtree(extract_temp, ignore_errors=True)
        os.makedirs(extract_temp)

    os.remove(file_path)
    print("   🗑️ Original archive deleted.")

# --- MODULES: LINK RESOLVERS ---

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
        if r.status_code == 200: 
            name = sanitize_filename(r.json().get('name', name))
    except: pass
    
    return [(f"https://pixeldrain.com/api/file/{fid}?download", name)]

def process_rd_link(link, rd_token):
    headers = {"Authorization": f"Bearer {rd_token}"}
    
    if "magnet:?" in link:
        print("   🧲 Processing Magnet...")
        try:
            r = requests.post("https://api.real-debrid.com/rest/1.0/torrents/addMagnet", 
                              data={"magnet": link}, headers=headers, timeout=30)
            if r.status_code != 201: 
                print("   ❌ Failed to add magnet."); return
            
            tid = r.json()['id']
            requests.post(f"https://api.real-debrid.com/rest/1.0/torrents/selectFiles/{tid}", 
                          data={"files": "all"}, headers=headers, timeout=30)
            
            start_time = time.time()
            while True:
                if time.time() - start_time > 600:
                    print("   ❌ Magnet timed out."); return
                info = requests.get(f"https://api.real-debrid.com/rest/1.0/torrents/info/{tid}", 
                                    headers=headers, timeout=30).json()
                if info['status'] == 'downloaded':
                    print("   ✅ Magnet Cached. Retrieving links...")
                    for direct_link in info['links']:
                        process_rd_link(direct_link, rd_token)
                    return
                elif info['status'] in ['error', 'dead']:
                    print("   ❌ Magnet dead."); return
                time.sleep(2)
        except Exception as e:
            print(f"   ❌ RD Magnet Error: {e}"); return

    try:
        r = requests.post("https://api.real-debrid.com/rest/1.0/unrestrict/link", 
                          data={"link": link}, headers=headers, timeout=30)
    except Exception as e:
        print(f"   ❌ RD Network Error: {e}"); return

    if r.status_code == 200:
        data = r.json()
        filename = data.get('filename', 'rd_download')
        dl_url = data['download']
        
        temp_file = download_with_aria2(dl_url, filename, "/content/")
        handle_file_processing(temp_file)
        
    elif r.status_code == 403:
        print("   ❌ RD Permission Denied.")
    else:
        print(f"   ⚠️ Link not supported by RD. Trying direct...")
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
    
    # Reset Log
    for k in report_log: report_log[k] = []

    setup_environment()
    session, gf_tokens = get_gofile_session(token_gf.value.strip())
    rd_key = token_rd.value.strip()
    
    urls = [line.strip() for line in text_area.value.split('\n') if line.strip()]
    print(f"\n🚀 Processing {len(urls)} Links...\n")
    
    for i, url in enumerate(urls, 1):
        print(f"--- Link [{i}/{len(urls)}] ---")
        
        if "gofile.io" in url:
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
                time.sleep(5) # Gentle pause between Pixeldrain files
                
        elif "magnet:?" in url or (rd_key and "http" in url):
            if rd_key:
                process_rd_link(url, rd_key)
            else:
                print("   ❌ RD Token missing.")
                
        else:
            path = urlparse(url).path
            name = os.path.basename(unquote(path)) or f"file_{int(time.time())}"
            temp_file = download_with_aria2(url, name, "/content/")
            handle_file_processing(temp_file)

    elapsed = time.time() - start_time_global
    print("\n" + "="*40)
    print(f"📝 MISSION REPORT (Time: {int(elapsed//60)}m {int(elapsed%60)}s)")
    print("="*40)
    print(f"📺 TV Shows ({len(report_log['TV'])}):")
    for item in report_log['TV']: print(f"  - {item}")
    
    print(f"\n🎬 Movies ({len(report_log['Movies'])}):")
    for item in report_log['Movies']: print(f"  - {item}")
    
    if report_log['Failed']:
        print(f"\n❌ Failed ({len(report_log['Failed'])}):")
        for item in report_log['Failed']: print(f"  - {item}")
    print("="*40)
    print("\n✅ All Tasks Finished.")

# --- WIDGETS ---
print("👇 Configuration & Links:")
token_gf = widgets.Text(description='Gofile Token:', placeholder='Optional')
token_rd = widgets.Text(description='RD Token:', placeholder='Real-Debrid API Key')
text_area = widgets.Textarea(description='Links:', placeholder='Paste Magnets/Links Here...', layout=widgets.Layout(width='100%', height='200px'))
btn = widgets.Button(description="Start Download", button_style='success')
btn.on_click(on_start)

display(widgets.VBox([token_gf, token_rd, text_area, btn]))