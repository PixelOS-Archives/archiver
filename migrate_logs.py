import json
import urllib.request
import os
import subprocess

LOG_FILE = "log.json"
GH_REPO = os.environ.get("GITHUB_REPOSITORY", "PixelOS-Archives/archiver")

def migrate_log():
    if not os.path.exists(LOG_FILE):
        print(f"{LOG_FILE} not found.")
        return
        
    with open(LOG_FILE, "r") as f:
        log_data = json.load(f)
        
    updated = False
    
    for entry in log_data:
        # Check if parts is simply "true"
        if entry.get("parts") is True:
            filename = entry["filename"]
            print(f"Fixing entry for {filename}...")
            
            tag_name = ""
            for v in ["twelve", "thirteen", "fourteen", "fifteen", "sixteen"]:
                if f"/{v}/" in entry["SF URL"]:
                    # url: https://.../files/fourteen/alioth/PixelOS_alioth-14.0-20240820-2333.zip/download
                    parts = entry["SF URL"].split(f"/{v}/")[1].split("/")
                    codename = parts[0]
                    tag_name = f"{codename}-{v}"
                    break
            
            if not tag_name:
                print(f"Could not determine tag for {filename}")
                continue
                
            gh_base_url = f"https://github.com/{GH_REPO}/releases/download/{tag_name}"
            
            # Form parts list manually? We'd have to know size/sha256 of the existing uploaded parts. 
            # Easiest way is to just query GitHub release API.
            try:
                # Call gh api
                res = subprocess.run(["gh", "release", "view", tag_name, "--json", "assets"], capture_output=True, text=True, check=True)
                release_info = json.loads(res.stdout)
                
                part_details = []
                for asset in release_info.get("assets", []):
                    asset_name = asset["name"]
                    # Match parts of THIS filename specifically
                    if asset_name.startswith(filename + ".part"):
                        part_details.append({
                            "filename": asset_name,
                            "size": asset["size"],
                            # Re-computing SHA256 retroactively is impossible without downloading the 1.6GB file again
                            "SHA256": "unknown", 
                            "GH URL": f"{gh_base_url}/{asset_name}"
                        })
                
                # Sort to ensure part0, part1, etc is ordered
                part_details.sort(key=lambda x: x["filename"])
                
                if part_details:
                    entry["parts"] = part_details
                    updated = True
                    print(f"   -> Fixed {filename} ({len(part_details)} parts)")
                else:
                    print(f"   -> No parts found on GitHub for {filename} under tag {tag_name}")
                    
            except subprocess.CalledProcessError as e:
                print(f"Error querying GitHub for {tag_name}: {e}")

    if updated:
        with open(LOG_FILE, "w") as f:
            json.dump(log_data, f, indent=4)
        print("Done updating log.json!")
    else:
        print("No changes required.")

if __name__ == "__main__":
    migrate_log()
