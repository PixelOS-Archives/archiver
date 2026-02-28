import os
import json
import urllib.request
import xml.etree.ElementTree as ET
import subprocess
import hashlib
import sys
import time
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(threadName)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

LOG_FILE = "log.json"
MAX_UPLOAD_PER_RUN = 120
SPLIT_SIZE_BYTES = int(1.9 * 1024**3)
PART_SIZE_MB = "1600M" # for split command
GH_REPO = os.environ.get("GITHUB_REPOSITORY", "user/repo")

def get_sha256(filepath):
    sha256_hash = hashlib.sha256()
    with open(filepath, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()

def ensure_gh_release(tag, codename):
    # Check if release exists
    res = subprocess.run(["gh", "release", "view", tag], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if res.returncode != 0:
        subprocess.run(["gh", "release", "create", tag, "--title", f"Releases for {codename}", "--notes", f"Automated mirror of SourceForge releases for {codename}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def main():
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, "r") as f:
            try:
                log_data = json.load(f)
            except json.JSONDecodeError:
                log_data = [] # empty valid json fallback
    else:
        log_data = []

    processed_urls = {item["SF URL"] for item in log_data if "SF URL" in item}
    
    url = 'https://sourceforge.net/projects/pixelos-releases/rss?limit=10000'
    logging.info(f"Fetching RSS: {url}")
    # Using a User-Agent so SF won't block the request occasionally
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    xml_data = urllib.request.urlopen(req).read().decode('utf-8')
    tree = ET.fromstring(xml_data)

    valid_versions = ["twelve", "thirteen", "fourteen", "fifteen", "sixteen"]
    
    pending_files = []

    # Namespace handling
    ns = {'media': 'http://video.search.yahoo.com/mrss/'}

    for item in tree.iter('item'):
        link_elem = item.find('link')
        if link_elem is None: continue
        link = link_elem.text
        
        # example link: https://sourceforge.net/projects/pixelos-releases/files/fourteen/alioth/PixelOS_alioth-14.0-20240101-0000.zip/download
        prefix = "https://sourceforge.net/projects/pixelos-releases/files/"
        if prefix not in link:
            continue
            
        rel_path = link.split(prefix)[1].rsplit("/download", 1)[0]
        parts = rel_path.split("/")
        
        # We need {version}/{codename}/{filename}.zip
        if len(parts) == 3:
            version, codename, filename = parts
            if version in valid_versions and filename.endswith(".zip") and "recovery" not in rel_path and ".img" not in filename:
                filesize = 0
                content_elem = item.find('media:content', ns)
                if content_elem is not None:
                    filesize = int(content_elem.get('filesize', 0))
                else:
                    # fallback
                    for child in item:
                        if child.tag == '{http://video.search.yahoo.com/mrss/}content':
                            filesize = int(child.attrib.get('filesize', 0))
                            
                if link not in processed_urls:
                    pending_files.append({
                        "version": version,
                        "codename": codename,
                        "filename": filename,
                        "url": link,
                        "size": filesize
                    })

    # Sort pending_files to be deterministic
    pending_files.sort(key=lambda x: x["filename"])
    
    logging.info(f"Found {len(pending_files)} pending files.")
    
    dry_run = os.environ.get("DRY_RUN", "0") == "1"
    
    if dry_run:
        logging.info("Dry run requested, here are the first 10 items:")
        for pending in pending_files[:10]:
            logging.info(json.dumps(pending, indent=2))
        return

    to_process = pending_files[:MAX_UPLOAD_PER_RUN]
    
    if not to_process:
        logging.info("No files to process.")
        return

    from concurrent.futures import ThreadPoolExecutor, as_completed
    import threading

    log_lock = threading.Lock()

    def process_file(f_info):
        download_url = f_info["url"]
        local_filename = f_info["filename"]
        try:
            logging.info(f"[\u2193] Downloading {local_filename}...")
            start_dl = time.time()
            subprocess.run(["wget", "-q", "-O", local_filename, download_url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            dl_time = max(time.time() - start_dl, 0.001)
            
            sha256sum = get_sha256(local_filename)
            tag = f"{f_info['codename']}-{f_info['version']}"
            
            # Since threads might be updating the same release, lock the tag creation explicitly
            with log_lock:
                ensure_gh_release(tag, f_info['codename'])
            
            files_to_upload = []
            is_split = False
            
            actual_size = os.path.getsize(local_filename)
            dl_speed = (actual_size / 1024 / 1024) / dl_time
            logging.info(f"[\u2713] Downloaded {local_filename} at {dl_speed:.2f} MB/s")
            
            if actual_size > SPLIT_SIZE_BYTES:
                logging.info(f"[*] File {local_filename} > 1.9GB. Splitting into {PART_SIZE_MB}...")
                is_split = True
                split_prefix = f"{local_filename}.part"
                subprocess.run(["split", "-b", PART_SIZE_MB, "-d", "-a", "1", local_filename, split_prefix], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
                
                for f in sorted(os.listdir(".")):
                    if f.startswith(split_prefix):
                        files_to_upload.append(f)
            else:
                files_to_upload.append(local_filename)
                
            total_up_size = 0
            up_start = time.time()
            for f_to_up in files_to_upload:
                total_up_size += os.path.getsize(f_to_up)
                subprocess.run(["gh", "release", "upload", tag, f_to_up, "--clobber"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            up_time = max(time.time() - up_start, 0.001)
            up_speed = (total_up_size / 1024 / 1024) / up_time
            logging.info(f"[\u2191] Uploaded {local_filename} at {up_speed:.2f} MB/s")
            
            gh_base_url = f"https://github.com/{GH_REPO}/releases/download/{tag}"
            
            if is_split:
                gh_url = f"{gh_base_url}/{local_filename}.part*"
            else:
                gh_url = f"{gh_base_url}/{local_filename}"

            log_entry = {
                "filename": f_info["filename"],
                "size": actual_size,
                "SHA256": sha256sum,
                "SF URL": download_url,
                "GH URL": gh_url,
                "parts": is_split
            }
            
            with log_lock:
                log_data.append(log_entry)
                with open(LOG_FILE, "w") as f:
                    json.dump(log_data, f, indent=4)
                    
            logging.info(f"[*] Successfully recorded {local_filename} to log.json")

        except Exception as e:
            logging.error(f"[!] Error processing {local_filename}: {e}")
            
        finally:
            if os.path.exists(local_filename):
                os.remove(local_filename)
            try:
                for f in files_to_upload:
                    if f != local_filename and os.path.exists(f):
                        os.remove(f)
            except Exception:
                pass


    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(process_file, item) for item in to_process]
        for future in as_completed(futures):
            future.result()

if __name__ == "__main__":
    main()
