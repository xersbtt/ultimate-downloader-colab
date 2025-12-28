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
DRIVE_DESTINATION_FOLDER = "TV Shows"
AUTO_PLEX_TV = True  # Set to True to auto-sort into /Show Name/Season XX/

# --- HELPER: TEXT SANITISATION & PATHS ---

def sanitize_filename(name):
    """Removes characters that break Plex or Windows/Linux filesystems."""
    name = unquote(name)
    # Replace invalid chars with underscore
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    # Remove control characters
    name = "".join(c for c in name if c.isprintable())
    # Collapse multiple spaces/underscores
    name = re.sub(r'[\s_]+', ' ', name).strip()
    return name

def build_plex_tv_path(base_folder, filename):
    """
    If AUTO_PLEX_TV is True, attempts to sort into Show/Season structure.
    Returns the full destination path.
    """
    if not AUTO_PLEX_TV:
        return os.path.join(base_folder, filename)

    # Regex to find SxxExx or SxEx
    sxe = re.search(r'(?i)[S](\d{1,2})[E](\d{1,2})', filename)
    
    if not sxe:
        # Not a TV show, dump in root
        return os.path.join(base_folder, filename)

    season_num = int(sxe.group(1))
    
    # Heuristic: The show name is everything before SxxExx
    # e.g. "My.Show.S01E01.mkv" -> "My Show"
    show_part = filename[:sxe.start()]
    show_part = re.sub(r'[._-]', ' ', show_part).strip()
    
    if len(show_part) < 2: 
        show_part = "Unknown Show"

    # Build Structure: Base / Show Name / Season XX
    show_dir = os.path.join(base_folder, show_part)
    season_dir = os.path.join(show_dir, f"Season {season_num:02d}")

    if not os.path.exists(season_dir):
        os.makedirs(season_dir, exist_ok=True)

    return os.path.join(season_dir, filename)

# --- SYSTEM SETUP ---

def setup_environment():
    if not os.path.exists('/content/drive'):
        drive.mount('/content/drive')
    
    base_path = f"/content/drive/My Drive/{DRIVE_DESTINATION_FOLDER}"
    if not os.path.exists(base_path):
        os.makedirs(base_path)

    required_tools = ['aria2c', '7z', 'unrar']
    missing = [t for t in required_tools if not shutil.which(t)]
    
    if missing:
        print(f"🛠️ Installing tools ({', '.join(missing)})...")
        subprocess.run("apt-get update -qq", shell=True)
        subprocess.run("apt-get install -y aria2 unrar p7zip-full", shell=True, check=True, stdout=subprocess.DEVNULL)
    
    return base_path

# --- HELPER: ARIA2 DOWNLOADER ---

def download_with_aria2(url, filename, dest_folder, cookie=None):
    filename = sanitize_filename(filename)
    
    # If downloading directly to Drive (no extraction), use Plex logic immediately
    # If dest_folder is /content/ (temp), we just save flatly
    if "My Drive" in dest_folder:
        final_path = build_plex_tv_path(dest_folder, filename)
        save_dir = os.path.dirname(final_path) # Aria2 needs dir separate from filename
        save_name = os.path.basename(final_path)
    else:
        final_path = os.path.join(dest_folder, filename)
        save_dir = dest_folder
        save_name = filename

    if os.path.exists(final_path):
        size_mb = os.path.getsize(final_path) / (1024 * 1024)
        if size_mb > 1:
            print(f"   ⚠️ File '{save_name}' exists (~{size_mb:.1f} MB). Skipping.")
            return final_path

    print(f"   ⬇️ Downloading: {save_name}")
    
    cmd = [
        'aria2c', url, '-d', save_dir, '-o', save_name,
        '-x', '16', '-s', '16', '-k', '1M', 
        '--file-allocation=none', '--summary-interval=5',
        '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
    ]
    if cookie:
        cmd.extend(['--header', f'Cookie: accountToken={cookie}'])

    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    
    for line in process.stdout:
        if '[' in line and 'ERR' not in line:
            print(line.strip())
            
    process.wait()
    
    if process.returncode == 0 and os.path.exists(final_path):
        print(f"   ✅ Download Completed: {save_name}")
        return final_path
    else:
        print("   ❌ Download Failed.")
        return None

# --- MODULE 1: GOFILE & PIXELDRAIN (Direct) ---

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
                          headers={'Authorization': f"Bearer {tokens['token']}"},
                          timeout=30)
            
            if r.status_code == 429:
                print("   ⚠️ Rate Limit (429). Sleeping 30s...")
                time.sleep(30); continue
                
            data = r.json()
            if data.get('status') == 'ok':
                results = []
                for child in data['data'].get('children', {}).values():
                    if child.get('link') and child.get('name'):
                        results.append((child['link'], child['name']))
                return results
            else: break
        except Exception as e:
            print(f"   ⚠️ Gofile Error: {e}")
            time.sleep(2)
    return []

def resolve_pixeldrain(url, session):
    match = re.search(r'pixeldrain\.com/u/([a-zA-Z0-9]+)', url)
    if not match: return []
    fid = match.group(1)
    
    name = f"pixeldrain_{fid}.file"
    try:
        r = session.get(f"https://pixeldrain.com/api/file/{fid}/info", timeout=15)
        if r.status_code == 200: 
            name = r.json().get('name', name)
    except: pass
    
    return [(f"https://pixeldrain.com/api/file/{fid}?download", name)]

# --- MODULE 2: REAL-DEBRID (Temp -> Extract -> Drive/Plex) ---

def handle_archive_extraction(archive_path, dest_folder):
    """Checks extension and extracts if RAR/ZIP/7Z. Otherwise moves file."""
    filename = os.path.basename(archive_path)
    lower_name = filename.lower()
    
    ext = None
    if lower_name.endswith(('.rar', '.zip', '.7z')): ext = lower_name[-4:] # crude extension check
    
    # 1. NOT AN ARCHIVE: Just Move to Plex Structure
    if not ext:
        final_dest = build_plex_tv_path(dest_folder, filename)
        
        # Ensure dir exists (if build_plex_tv_path created a new Season folder)
        if not os.path.exists(os.path.dirname(final_dest)):
            os.makedirs(os.path.dirname(final_dest))

        if os.path.exists(final_dest): os.remove(final_dest)
        shutil.move(archive_path, final_dest)
        print(f"   ✨ Moved to Drive: {os.path.basename(final_dest)}")
        return

    # 2. IS ARCHIVE: Extract
    print(f"   📦 Archive detected. Analyzing...")
    extract_temp = "/content/temp_extract"
    if os.path.exists(extract_temp): shutil.rmtree(extract_temp, ignore_errors=True)
    os.makedirs(extract_temp)

    files = []
    try:
        if '.rar' in ext:
            res = subprocess.run(['unrar', 'lb', archive_path], capture_output=True, text=True)
            if res.returncode == 0: files = res.stdout.strip().splitlines()
        else:
            res = subprocess.run(['7z', 'l', '-ba', '-slt', archive_path], capture_output=True, text=True)
            if res.returncode == 0:
                for line in res.stdout.splitlines():
                    if line.strip().startswith('Path = '): files.append(line.split(' = ')[1])
    except Exception as e:
        print(f"   ❌ Error reading archive: {e}"); return

    print(f"   🔄 Extracting {len(files)} files sequentially...")
    
    for f_path in files:
        # Extract ONE file
        cmd = []
        if '.rar' in ext: cmd = ['unrar', 'x', '-o+', archive_path, f_path, extract_temp]
        else: cmd = ['7z', 'x', '-y', archive_path, f'-o{extract_temp}', f_path]
        
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        extracted_full = os.path.join(extract_temp, f_path)
        
        # Move ONE file to Plex Structure
        if os.path.exists(extracted_full) and not os.path.isdir(extracted_full):
            clean_name = sanitize_filename(os.path.basename(f_path))
            drive_full = build_plex_tv_path(dest_folder, clean_name)
            
            drive_dir = os.path.dirname(drive_full)
            if not os.path.exists(drive_dir): os.makedirs(drive_dir)
            
            if os.path.exists(drive_full): os.remove(drive_full)
            shutil.move(extracted_full, drive_full)
            print(f"      -> Extracted: {clean_name}")
        
        # Clean temp immediately
        if os.path.exists(extract_temp): shutil.rmtree(extract_temp, ignore_errors=True)
        os.makedirs(extract_temp)

    os.remove(archive_path)
    print("   🗑️ Original archive deleted.")

def process_rd_link(link, rd_token, drive_path):
    headers = {"Authorization": f"Bearer {rd_token}"}
    
    # 1. Magnet Handling
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
            
            # Wait loop with timeout
            start_time = time.time()
            while True:
                if time.time() - start_time > 600: # 10 min timeout
                    print("   ❌ Magnet timed out (not cached?)."); return

                info = requests.get(f"https://api.real-debrid.com/rest/1.0/torrents/info/{tid}", 
                                    headers=headers, timeout=30).json()
                if info['status'] == 'downloaded':
                    print("   ✅ Magnet Cached. Retrieving links...")
                    for direct_link in info['links']:
                        process_rd_link(direct_link, rd_token, drive_path)
                    return
                elif info['status'] in ['error', 'dead']:
                    print("   ❌ Magnet dead."); return
                time.sleep(2)
        except Exception as e:
            print(f"   ❌ RD Magnet Error: {e}"); return

    # 2. Standard Link Unrestrict
    try:
        r = requests.post("https://api.real-debrid.com/rest/1.0/unrestrict/link", 
                          data={"link": link}, headers=headers, timeout=30)
    except Exception as e:
        print(f"   ❌ RD Network Error: {e}"); return

    if r.status_code == 200:
        data = r.json()
        filename = data.get('filename', 'rd_download')
        dl_url = data['download']
        
        # Download to Colab Temp (NOT Drive) to allow extraction logic
        temp_file = download_with_aria2(dl_url, filename, "/content/")
        if temp_file:
            handle_archive_extraction(temp_file, drive_path)
    elif r.status_code == 403:
        print("   ❌ RD Permission Denied (Check Token).")
    elif r.status_code == 503:
        print("   ❌ RD Service Unavailable.")
    else:
        print(f"   ⚠️ Link not supported by RD (Code {r.status_code}). Trying direct...")
        # Fallback to direct download if RD fails
        path = urlparse(link).path
        name = os.path.basename(unquote(path)) or f"file_{int(time.time())}"
        download_with_aria2(link, name, drive_path)

# --- MAIN UI ---

def on_start(b):
    clear_output(wait=True)
    display(widgets.VBox([token_gf, token_rd, text_area, btn]))
    
    drive_path = setup_environment()
    session, gf_tokens = get_gofile_session(token_gf.value.strip())
    rd_key = token_rd.value.strip()
    
    urls = [line.strip() for line in text_area.value.split('\n') if line.strip()]
    print(f"\n🚀 Processing {len(urls)} Links...\n")
    
    for i, url in enumerate(urls, 1):
        print(f"--- Link [{i}/{len(urls)}] ---")
        
        if "gofile.io" in url:
            files = resolve_gofile(url, session, gf_tokens)
            for d_url, name in files:
                download_with_aria2(d_url, name, drive_path, gf_tokens['token'])
                time.sleep(2)
                
        elif "pixeldrain.com" in url:
            files = resolve_pixeldrain(url, session)
            for d_url, name in files:
                download_with_aria2(d_url, name, drive_path)
                
        elif "magnet:?" in url or (rd_key and "http" in url):
            # ROUTER: Magnets OR (HTTP links if RD key is present) go to RD
            if rd_key:
                process_rd_link(url, rd_key, drive_path)
            else:
                print("   ❌ RD Token missing for Magnet.")
                
        else:
            # Fallback for HTTP links if NO RD key provided
            path = urlparse(url).path
            name = os.path.basename(unquote(path)) or f"file_{int(time.time())}"
            download_with_aria2(url, name, drive_path)

    print("\n✅ All Tasks Finished.")

# --- WIDGETS ---
print("👇 Configuration & Links:")

token_gf = widgets.Text(description='Gofile Token:', placeholder='Optional')
token_rd = widgets.Text(description='RD Token:', placeholder='Real-Debrid API Key')
text_area = widgets.Textarea(description='Links:', placeholder='Paste Magnets/Links Here...', layout=widgets.Layout(width='100%', height='200px'))
btn = widgets.Button(description="Start Download", button_style='success')
btn.on_click(on_start)

display(widgets.VBox([token_gf, token_rd, text_area, btn]))