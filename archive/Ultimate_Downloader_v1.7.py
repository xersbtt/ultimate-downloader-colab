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

# --- HELPER: TEXT SANITISATION & PATHS ---

def sanitize_filename(name):
    """Removes characters that break Plex or Windows/Linux filesystems."""
    name = unquote(name)
    name = re.sub(r'[<>:"/\\|?*]', '_', name) # Invalid chars
    name = "".join(c for c in name if c.isprintable()) # Control chars
    name = re.sub(r'[\s_]+', ' ', name).strip() # Cleanup spaces
    return name

def determine_destination_path(filename):
    """
    Decides if file is TV or Movie and builds the correct path.
    Structure:
      TV:    My Drive/TV Shows/[Show Name]/[Season]/[File]
      Movie: My Drive/Movies/[Movie Name]/[File]
    """
    filename = sanitize_filename(filename)
    
    # 1. Check for TV Show Pattern (SxxExx)
    sxe = re.search(r'(?i)[S](\d{1,2})[E](\d{1,2})', filename)
    
    if sxe:
        # --- TV SHOW LOGIC ---
        season_num = int(sxe.group(1))
        
        # Get Show Name (Everything before SxxExx)
        show_name = filename[:sxe.start()]
        show_name = re.sub(r'[._-]', ' ', show_name).strip()
        if len(show_name) < 2: show_name = "Unknown Show"
        
        # Build Path: My Drive/TV Shows/Show Name/Season XX/File
        base_path = f"/content/drive/My Drive/{DRIVE_TV_PATH}"
        season_folder = f"Season {season_num:02d}"
        full_dir = os.path.join(base_path, show_name, season_folder)
        
    else:
        # --- MOVIE LOGIC ---
        # Heuristic: Split at the Year (19xx or 20xx) to find Movie Name
        # e.g. "The.Matrix.1999.1080p.mkv" -> "The Matrix"
        year_match = re.search(r'\b(19|20)\d{2}\b', filename)
        
        if year_match:
            movie_name = filename[:year_match.start()]
        else:
            # Fallback: Use filename without extension
            movie_name = os.path.splitext(filename)[0]
            
        movie_name = re.sub(r'[._-]', ' ', movie_name).strip()
        if len(movie_name) < 2: movie_name = "Unknown Movie"

        # Build Path: My Drive/Movies/Movie Name/File
        base_path = f"/content/drive/My Drive/{DRIVE_MOVIE_PATH}"
        full_dir = os.path.join(base_path, movie_name)

    # Ensure the directory exists
    if not os.path.exists(full_dir):
        os.makedirs(full_dir, exist_ok=True)
        
    return os.path.join(full_dir, filename)

# --- SYSTEM SETUP ---

def setup_environment():
    if not os.path.exists('/content/drive'):
        drive.mount('/content/drive')
    
    # Ensure Roots Exist
    for p in [DRIVE_TV_PATH, DRIVE_MOVIE_PATH]:
        full_p = f"/content/drive/My Drive/{p}"
        if not os.path.exists(full_p):
            os.makedirs(full_p)

    # Check Tools
    required_tools = ['aria2c', '7z', 'unrar']
    missing = [t for t in required_tools if not shutil.which(t)]
    
    if missing:
        print(f"🛠️ Installing tools ({', '.join(missing)})...")
        subprocess.run("apt-get update -qq", shell=True)
        subprocess.run("apt-get install -y aria2 unrar p7zip-full", shell=True, check=True, stdout=subprocess.DEVNULL)

# --- HELPER: ARIA2 DOWNLOADER ---

def download_with_aria2(url, filename, dest_folder, cookie=None):
    """
    Downloads file to the specified folder. 
    Does NOT handle sorting or Drive logic. Just downloads.
    """
    filename = sanitize_filename(filename)
    final_path = os.path.join(dest_folder, filename)

    # Check Existence (Skip if > 1MB)
    if os.path.exists(final_path):
        size_mb = os.path.getsize(final_path) / (1024 * 1024)
        if size_mb > 1:
            print(f"   ⚠️ File '{filename}' exists in temp (~{size_mb:.1f} MB). Skipping download.")
            return final_path

    print(f"   ⬇️ Downloading: {filename}")
    
    cmd = [
        'aria2c', url, '-d', dest_folder, '-o', filename,
        '-x', '16', '-s', '16', '-k', '1M', 
        '--file-allocation=none', '--summary-interval=5',
        '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
    ]
    if cookie:
        cmd.extend(['--header', f'Cookie: accountToken={cookie}'])

    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    
    # Stream output lightly
    for line in process.stdout:
        if '[' in line and 'ERR' not in line:
            print(line.strip())
            
    process.wait()
    
    if process.returncode == 0 and os.path.exists(final_path):
        print(f"   ✅ Download Completed: {filename}")
        return final_path
    else:
        print("   ❌ Download Failed.")
        return None

# --- PROCESSING: EXTRACTION & SORTING ---

def handle_file_processing(file_path):
    """
    The Universal Handler.
    1. Checks if archive -> Extracts.
    2. If not archive -> Moves.
    3. Always sorts into Smart Plex Paths.
    """
    if not file_path or not os.path.exists(file_path):
        return

    filename = os.path.basename(file_path)
    lower_name = filename.lower()
    
    # Check for Archive Extensions
    ext = None
    if lower_name.endswith(('.rar', '.zip', '.7z')): 
        ext = lower_name[-4:]
    
    # CASE A: NOT AN ARCHIVE -> Move directly to Drive
    if not ext:
        final_dest = determine_destination_path(filename)
        
        # Overwrite protection/handling
        if os.path.exists(final_dest): 
            os.remove(final_dest)
            
        shutil.move(file_path, final_dest)
        print(f"   ✨ Moved to Drive: {os.path.basename(os.path.dirname(final_dest))}/{os.path.basename(final_dest)}")
        return

    # CASE B: IS ARCHIVE -> Extract then Move
    print(f"   📦 Archive detected ({ext}). Extracting...")
    extract_temp = "/content/temp_extract"
    if os.path.exists(extract_temp): shutil.rmtree(extract_temp, ignore_errors=True)
    os.makedirs(extract_temp)

    # 1. List Files
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
    
    # 2. Extract & Move Loop
    for f_path in files:
        # Extract ONE file
        cmd = []
        if '.rar' in ext: cmd = ['unrar', 'x', '-o+', file_path, f_path, extract_temp]
        else: cmd = ['7z', 'x', '-y', file_path, f'-o{extract_temp}', f_path]
        
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        extracted_full = os.path.join(extract_temp, f_path)
        
        # Check if it extracted successfully
        if os.path.exists(extracted_full) and not os.path.isdir(extracted_full):
            clean_name = sanitize_filename(os.path.basename(f_path))
            
            # Skip small junk files (sample, txt, nfo) if desired, or keep all
            if os.path.getsize(extracted_full) < 1024 * 1024 * 10: # < 10MB
                pass # You can enable junk filtering here if you want
            
            final_dest = determine_destination_path(clean_name)
            
            if os.path.exists(final_dest): os.remove(final_dest)
            shutil.move(extracted_full, final_dest)
            print(f"      -> Extracted & Sorted: {clean_name}")
        
        # Clean temp immediately to save space
        if os.path.exists(extract_temp): shutil.rmtree(extract_temp, ignore_errors=True)
        os.makedirs(extract_temp)

    # 3. Cleanup Original Archive
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
            print(f"   ⚠️ Gofile Error: {e}"); time.sleep(2)
    return []

def resolve_pixeldrain(url, session):
    match = re.search(r'pixeldrain\.com/u/([a-zA-Z0-9]+)', url)
    if not match: return []
    fid = match.group(1)
    
    name = f"pixeldrain_{fid}.file"
    try:
        r = session.get(f"https://pixeldrain.com/api/file/{fid}/info", timeout=15)
        if r.status_code == 200: 
            # Sanitize immediately from API
            name = sanitize_filename(r.json().get('name', name))
    except Exception as e:
        print(f"   ⚠️ Pixeldrain Info Error: {e}")
    
    return [(f"https://pixeldrain.com/api/file/{fid}?download", name)]

def process_rd_link(link, rd_token):
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
            
            # Wait loop
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
        
        # Download to Temp, then Handler takes over
        temp_file = download_with_aria2(dl_url, filename, "/content/")
        handle_file_processing(temp_file)
        
    elif r.status_code == 403:
        print("   ❌ RD Permission Denied.")
    else:
        print(f"   ⚠️ Link not supported by RD. Trying direct...")
        # Fallback to direct download
        path = urlparse(link).path
        name = os.path.basename(unquote(path)) or f"file_{int(time.time())}"
        temp_file = download_with_aria2(link, name, "/content/")
        handle_file_processing(temp_file)

# --- MAIN UI ---

def on_start(b):
    clear_output(wait=True)
    display(widgets.VBox([token_gf, token_rd, text_area, btn]))
    
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
                # 1. Download to Temp
                temp_file = download_with_aria2(d_url, name, "/content/", gf_tokens['token'])
                # 2. Extract/Sort/Move
                handle_file_processing(temp_file)
                time.sleep(2)
                
        elif "pixeldrain.com" in url:
            files = resolve_pixeldrain(url, session)
            for d_url, name in files:
                temp_file = download_with_aria2(d_url, name, "/content/")
                handle_file_processing(temp_file)
                
        elif "magnet:?" in url or (rd_key and "http" in url):
            if rd_key:
                process_rd_link(url, rd_key)
            else:
                print("   ❌ RD Token missing.")
                
        else:
            # Direct Link Fallback
            path = urlparse(url).path
            name = os.path.basename(unquote(path)) or f"file_{int(time.time())}"
            temp_file = download_with_aria2(url, name, "/content/")
            handle_file_processing(temp_file)

    print("\n✅ All Tasks Finished.")

# --- WIDGETS ---
print("👇 Configuration & Links:")
token_gf = widgets.Text(description='Gofile Token:', placeholder='Optional')
token_rd = widgets.Text(description='RD Token:', placeholder='Real-Debrid API Key')
text_area = widgets.Textarea(description='Links:', placeholder='Paste Magnets/Links Here...', layout=widgets.Layout(width='100%', height='200px'))
btn = widgets.Button(description="Start Download", button_style='success')
btn.on_click(on_start)

display(widgets.VBox([token_gf, token_rd, text_area, btn]))