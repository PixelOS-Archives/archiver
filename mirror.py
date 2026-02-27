import os
import json
import urllib.request
import xml.etree.ElementTree as ET
import subprocess
import hashlib
import sys

LOG_FILE = "log.json"
MAX_UPLOAD_PER_RUN = 10
SPLIT_SIZE_BYTES = int(1.9 * 1024**3)
PART_SIZE_MB = "1600M" # for split command
GH_REPO = os.environ.get("GITHUB_REPOSITORY", "user/repo")

def get_sha256(filepath):
    print(f"Calculating SHA256 for {filepath}...")
    sha256_hash = hashlib.sha256()
    with open(filepath, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()

def ensure_gh_release(tag, codename):
    # Check if release exists
    res = subprocess.run(["gh", "release", "view", tag], capture_output=True, text=True)
    if res.returncode != 0:
        print(f"Release {tag} not found, creating it...")
        subprocess.run(["gh", "release", "create", tag, "--title", f"Releases for {codename}", "--notes", f"Automated mirror of SourceForge releases for {codename}"], check=True)

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
    print(f"Fetching RSS: {url}")
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

    # Sort pending_files to be deterministic, oldest to newest might be better?
    # Sorting by filename is okay.
    pending_files.sort(key=lambda x: x["filename"])
    
    print(f"Found {len(pending_files)} pending files.")
    
    uploaded_count = 0
    dry_run = os.environ.get("DRY_RUN", "0") == "1"
    
    if dry_run:
        print("Dry run requested, here are the first 10 items:")
        for pending in pending_files[:10]:
            print(json.dumps(pending, indent=2))
        return

    for f_info in pending_files:
        if uploaded_count >= MAX_UPLOAD_PER_RUN:
            print("Reached max upload limit for this run.")
            break
            
        print(f"\nProcessing {f_info['filename']}...")
        download_url = f_info["url"]
        local_filename = f_info["filename"]
        try:
            # Download file
            print(f"Downloading {local_filename} from {download_url}...")
            subprocess.run(["wget", "-q", "--show-progress", "-O", local_filename, download_url], check=True)
            
            # Calculate SHA256 before splitting
            sha256sum = get_sha256(local_filename)
            
            tag = f"{f_info['codename']}-{f_info['version']}"
            ensure_gh_release(tag, f_info['codename'])
            
            files_to_upload = []
            is_split = False
            
            actual_size = os.path.getsize(local_filename)
            print(f"Downloaded size: {actual_size} bytes")
            if actual_size > SPLIT_SIZE_BYTES:
                print(f"File {local_filename} is larger than 1.9GB. Splitting into {PART_SIZE_MB}...")
                is_split = True
                split_prefix = f"{local_filename}.part"
                # use system split
                subprocess.run(["split", "-b", PART_SIZE_MB, local_filename, split_prefix], check=True)
                
                # find parts
                for f in sorted(os.listdir(".")):
                    if f.startswith(split_prefix):
                        files_to_upload.append(f)
            else:
                files_to_upload.append(local_filename)
                
            for f_to_up in files_to_upload:
                print(f"Uploading {f_to_up} to GitHub Releases tag {tag}...")
                subprocess.run(["gh", "release", "upload", tag, f_to_up, "--clobber"], check=True)
            
            gh_base_url = f"https://github.com/{GH_REPO}/releases/download/{tag}"
            
            # Form final URLs (gh cli puts it under the tag)
            if is_split:
                gh_url = f"{gh_base_url}/{local_filename}.part*"
            else:
                gh_url = f"{gh_base_url}/{local_filename}"

            # Log successful upload
            log_entry = {
                "filename": f_info["filename"],
                "size": actual_size,
                "SHA256": sha256sum,
                "SF URL": download_url,
                "GH URL": gh_url,
                "parts": is_split
            }
            log_data.append(log_entry)
            
            # Remove downloaded parts
            if os.path.exists(local_filename):
                os.remove(local_filename)
            for f in files_to_upload:
                if f != local_filename and os.path.exists(f):
                    os.remove(f)
                    
            uploaded_count += 1
            
            # Save log cumulatively
            with open(LOG_FILE, "w") as f:
                json.dump(log_data, f, indent=4)
                
        except Exception as e:
            print(f"Error processing {local_filename}: {e}")
            # Ensure partial cleanup
            if os.path.exists(local_filename): os.remove(local_filename)
            # breaking out of loop on error
            break

if __name__ == "__main__":
    main()
