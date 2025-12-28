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
DRIVE_DESTINATION_FOLDER = "Downloads"

# --- SYSTEM SETUP ---

def setup_environment():
    if not os.path.exists('/content/drive'):
        drive.mount('/content/drive')
    
    # Create Destination
    base_path = f"/content/drive/My Drive/{DRIVE_DESTINATION_FOLDER}"
    if not os.path.exists(base_path):
        os.makedirs(base_path)

    # Check/Install Tools (Aria2, 7zip, Unrar)
    required_tools = ['aria2c', '7z', 'unrar']
    missing = [t for t in required_tools if not shutil.which(t)]
    
    if missing:
        print(f"🛠️ Installing tools ({', '.join(missing)})...")
        subprocess.run("apt-get update -qq", shell=True)
        subprocess.run("apt-get install -y aria2 unrar p7zip-full", shell=True, check=True, stdout=subprocess.DEVNULL)
    
    return base_path

# --- HELPER: ARIA2 DOWNLOADER ---

def download_with_aria2(url, filename, dest_folder, cookie=None):
    """
    Universal Aria2 wrapper. 
    If dest_folder is Drive, it saves there. 
    If dest_folder is /content/, it saves to temp.
    """
    final_path = os.path.join(dest_folder, filename)
    if os.path.exists(final_path) and os.path.getsize(final_path) > 1024*1024:
        print(f"   ⚠️ File '{filename}' exists. Skipping.")
        return final_path

    print(f"   ⬇️ Downloading: {filename}")
    
    cmd = [
        'aria2c', url, '-d', dest_folder, '-o', filename,
        '-x', '16', '-s', '16', '-k', '1M', 
        '--file-allocation=none',
        '--summary-interval=5',
        '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
    ]
    if cookie:
        cmd.extend(['--header', f'Cookie: accountToken={cookie}'])

    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    
    # Stream output
    for line in process.stdout:
        if '[' in line and 'ERR' not in line:
            print(line.strip())
            
    process.wait()
    
    if process.returncode == 0 and os.path.exists(final_path):
        print(f"   ✅ Download Completed.")
        return final_path
    else:
        print("   ❌ Download Failed.")
        return None

# --- MODULE 1: GOFILE & PIXELDRAIN (Direct to Drive) ---

def get_gofile_session(custom_token=None):
    session = requests.Session()
    session.headers.update({'User-Agent': 'Mozilla/5.0'})
    tokens = {'token': custom_token, 'wt': "4fd6sg89d7s6"}
    
    if not tokens['token']:
        try:
            r = session.post("https://api.gofile.io/accounts", json={})
            if r.status_code == 200: tokens['token'] = r.json()['data']['token']
        except: pass
    return session, tokens

def resolve_gofile(url, session, tokens):
    match = re.search(r'gofile\.io/d/([a-zA-Z0-9]+)', url)
    if not match: return []
    content_id = match.group(1)

    for _ in range(3): # Retry loop
        if not tokens['token']: return []
        try:
            r = session.get(f"https://api.gofile.io/contents/{content_id}", 
                          params={'wt': tokens['wt']}, 
                          headers={'Authorization': f"Bearer {tokens['token']}"})
            
            if r.status_code == 429:
                print("   ⚠️ Rate Limit (429). Sleeping 30s...")
                time.sleep(30)
                continue
                
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
    
    # Try to fetch real name
    name = f"pixeldrain_{fid}.file"
    try:
        r = session.get(f"https://pixeldrain.com/api/file/{fid}/info")
        if r.status_code == 200: name = r.json().get('name', name)
    except: pass
    
    return [(f"https://pixeldrain.com/api/file/{fid}?download", name)]

# --- MODULE 2: REAL-DEBRID (Temp -> Extract -> Drive) ---

def handle_archive_extraction(archive_path, dest_folder):
    """Checks extension and extracts if RAR/ZIP/7Z. Otherwise moves file."""
    filename = os.path.basename(archive_path)
    lower_name = filename.lower()
    
    ext = None
    if lower_name.endswith('.rar'): ext = '.rar'
    elif lower_name.endswith('.zip'): ext = '.zip'
    elif lower_name.endswith('.7z'): ext = '.7z'
    
    # If not an archive, just move it
    if not ext:
        final_dest = os.path.join(dest_folder, filename)
        if os.path.exists(final_dest): os.remove(final_dest)
        shutil.move(archive_path, final_dest)
        print(f"   ✨ Moved to Drive: {filename}")
        return

    # Extraction Logic
    print(f"   📦 Archive detected ({ext}). Analyzing...")
    extract_temp = "/content/temp_extract"
    if os.path.exists(extract_temp): shutil.rmtree(extract_temp)
    os.makedirs(extract_temp)

    # Get file list
    files = []
    try:
        if ext == '.rar':
            res = subprocess.run(['unrar', 'lb', archive_path], capture_output=True, text=True)
            if res.returncode == 0: files = res.stdout.strip().splitlines()
        else:
            res = subprocess.run(['7z', 'l', '-ba', '-slt', archive_path], capture_output=True, text=True)
            if res.returncode == 0:
                for line in res.stdout.splitlines():
                    if line.strip().startswith('Path = '): files.append(line.split(' = ')[1])
    except Exception as e:
        print(f"   ❌ Error reading archive: {e}")
        return

    print(f"   🔄 Extracting {len(files)} files sequentially...")
    
    for f_path in files:
        # Extract ONE file
        cmd = []
        if ext == '.rar': cmd = ['unrar', 'x', '-o+', archive_path, f_path, extract_temp]
        else: cmd = ['7z', 'x', '-y', archive_path, f'-o{extract_temp}', f_path]
        
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        # Move ONE file
        extracted_full = os.path.join(extract_temp, f_path)
        drive_full = os.path.join(dest_folder, f_path)
        
        if os.path.exists(extracted_full) and not os.path.isdir(extracted_full):
            drive_dir = os.path.dirname(drive_full)
            if not os.path.exists(drive_dir): os.makedirs(drive_dir)
            shutil.move(extracted_full, drive_full)
            print(f"      -> Extracted: {os.path.basename(f_path)}")
        
        # Clean temp immediately to save space
        if os.path.exists(extract_temp): shutil.rmtree(extract_temp)
        os.makedirs(extract_temp)

    # Delete original archive
    os.remove(archive_path)
    print("   🗑️ Original archive deleted.")

def process_rd_link(link, rd_token, drive_path):
    headers = {"Authorization": f"Bearer {rd_token}"}
    
    # 1. Magnet Handling
    if "magnet:?" in link:
        print("   🧲 Processing Magnet...")
        r = requests.post("https://api.real-debrid.com/rest/1.0/torrents/addMagnet", data={"magnet": link}, headers=headers)
        if r.status_code != 201: 
            print("   ❌ Failed to add magnet."); return
        
        tid = r.json()['id']
        requests.post(f"https://api.real-debrid.com/rest/1.0/torrents/selectFiles/{tid}", data={"files": "all"}, headers=headers)
        
        while True:
            info = requests.get(f"https://api.real-debrid.com/rest/1.0/torrents/info/{tid}", headers=headers).json()
            if info['status'] == 'downloaded':
                print("   ✅ Magnet Cached. Retrieving links...")
                for direct_link in info['links']:
                    process_rd_link(direct_link, rd_token, drive_path)
                return
            elif info['status'] in ['error', 'dead']:
                print("   ❌ Magnet dead."); return
            time.sleep(2)

    # 2. Standard Link Unrestrict
    try:
        r = requests.post("https://api.real-debrid.com/rest/1.0/unrestrict/link", data={"link": link}, headers=headers)
    except: return

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
    else:
        # If RD fails, maybe it's a direct file?
        pass

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
        
        # Dispatcher Logic
        if "gofile.io" in url:
            files = resolve_gofile(url, session, gf_tokens)
            for d_url, name in files:
                download_with_aria2(d_url, name, drive_path, gf_tokens['token'])
                time.sleep(2)
                
        elif "pixeldrain.com" in url:
            files = resolve_pixeldrain(url, session)
            for d_url, name in files:
                download_with_aria2(d_url, name, drive_path)
                
        elif "magnet:?" in url or rd_key: 
            # If it's a magnet OR we have an RD key (assume standard hoster link)
            if rd_key:
                process_rd_link(url, rd_key, drive_path)
            else:
                print("   ❌ RD Token missing for Premium/Magnet link.")
                
        else:
            # Fallback: Try direct download to Drive
            path = urlparse(url).path
            name = os.path.basename(unquote(path)) or f"file_{int(time.time())}"
            download_with_aria2(url, name, drive_path)

    print("\n✅ All Tasks Finished.")

# --- WIDGETS ---
print("👇 Configuration & Links:")

token_gf = widgets.Text(description='Gofile Token:', placeholder='Optional')
token_rd = widgets.Text(description='RD Token:', placeholder='Real-Debrid API Key (Required for magnets/premium)')
text_area = widgets.Textarea(description='Links:', placeholder='Paste Links Here...', layout=widgets.Layout(width='100%', height='200px'))
btn = widgets.Button(description="Start Download", button_style='success')
btn.on_click(on_start)

display(widgets.VBox([token_gf, token_rd, text_area, btn]))