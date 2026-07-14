import os
import re
import zipfile
import hashlib
import socket
import xml.etree.ElementTree as ET

WORKSPACE_DIR = r"C:\Users\iamsh\Documents\antigravity\zealous-mendeleev"

def get_local_ip():
    """Detects the PC's current local network IP address."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('10.255.255.255', 1))
        ip = s.getsockname()[0]
    except Exception:
        ip = '127.0.0.1'
    finally:
        s.close()
    return ip

def get_md5(data):
    """Generates MD5 hash for a string."""
    m = hashlib.md5()
    m.update(data.encode('utf-8'))
    return m.hexdigest()

def create_addon_zip(addon_id, addon_version, files):
    """Creates a standard addon zip package."""
    zip_dir = os.path.join(WORKSPACE_DIR, addon_id)
    if not os.path.exists(zip_dir):
        os.makedirs(zip_dir)
        
    zip_filename = os.path.join(zip_dir, f"{addon_id}-{addon_version}.zip")
    print(f"[*] Packaging {addon_id} (v{addon_version}) into {os.path.basename(zip_filename)}...")
    
    with zipfile.ZipFile(zip_filename, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        for arcname, filepath in files.items():
            if os.path.exists(filepath):
                zip_path = f"{addon_id}/{arcname}"
                zip_file.write(filepath, zip_path)
            else:
                print(f"  [Error] File not found: {filepath}")
    print(f"[+] Zip created at: {zip_filename}")

def create_directory_indexes():
    """Generates static, clean index.html files to prevent Kodi's HTML directory parser from crashing."""
    print("[*] Creating Kodi-friendly index.html files...")
    
    # 1. Root index.html
    root_html = """<!DOCTYPE html>
<html>
<head><title>YoMovies Local Repo</title></head>
<body>
  <ul>
    <li><a href="addons.xml">addons.xml</a></li>
    <li><a href="addons.xml.md5">addons.xml.md5</a></li>
    <li><a href="plugin.video.yomovies/">plugin.video.yomovies/</a></li>
    <li><a href="repository.yomovies/">repository.yomovies/</a></li>
  </ul>
</body>
</html>"""
    with open(os.path.join(WORKSPACE_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(root_html)

    # 2. plugin.video.yomovies index.html
    plugin_html = """<!DOCTYPE html>
<html>
<head><title>YoMovies Addon</title></head>
<body>
  <ul>
    <li><a href="plugin.video.yomovies-1.0.0.zip">plugin.video.yomovies-1.0.0.zip</a></li>
  </ul>
</body>
</html>"""
    with open(os.path.join(WORKSPACE_DIR, "plugin.video.yomovies", "index.html"), "w", encoding="utf-8") as f:
        f.write(plugin_html)

    # 3. repository.yomovies index.html
    repo_html = """<!DOCTYPE html>
<html>
<head><title>YoMovies Repository</title></head>
<body>
  <ul>
    <li><a href="repository.yomovies-1.0.0.zip">repository.yomovies-1.0.0.zip</a></li>
    <li><a href="addon.xml">addon.xml</a></li>
  </ul>
</body>
</html>"""
    with open(os.path.join(WORKSPACE_DIR, "repository.yomovies", "index.html"), "w", encoding="utf-8") as f:
        f.write(repo_html)
    print("[+] Generated simple HTML indexes successfully.")

def generate_repo():
    print("="*60)
    print("        KODI LOCAL REPOSITORY BUILDER")
    print("="*60)
    
    server_url = "https://thisisshashwat.github.io/kodi/"
    print(f"[*] Repository Server URL: {server_url}")
    
    # Extract version from main addon.xml
    plugin_addon_xml = os.path.join(WORKSPACE_DIR, "addon.xml")
    plugin_version = "1.0.0"
    if os.path.exists(plugin_addon_xml):
        try:
            tree = ET.parse(plugin_addon_xml)
            plugin_version = tree.getroot().attrib.get('version', '1.0.0')
        except:
            pass
            
    # 1. Package the main YoMovies Addon
    yomovies_files = {
        "addon.xml": plugin_addon_xml,
        "default.py": os.path.join(WORKSPACE_DIR, "default.py"),
        "icon.png": os.path.join(WORKSPACE_DIR, "icon.png"),
        "fanart.jpg": os.path.join(WORKSPACE_DIR, "fanart.jpg")
    }
    create_addon_zip("plugin.video.yomovies", plugin_version, yomovies_files)
    
    # 2. Package/Update the Repository Addon itself
    repo_dir = os.path.join(WORKSPACE_DIR, "repository.yomovies")
    os.makedirs(repo_dir, exist_ok=True)
    repo_addon_xml = os.path.join(repo_dir, "addon.xml")
    
    xml_content = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<addon id="repository.yomovies" name="YoMovies Repository" version="1.0.0" provider-name="Antigravity">
  <requires>
    <import addon="xbmc.python" version="3.0.0"/>
  </requires>
  <extension point="xbmc.addon.repository" name="YoMovies Repository">
    <dir>
      <info compressed="false">{server_url}addons.xml</info>
      <checksum>{server_url}addons.xml.md5</checksum>
      <datadir zip="true">{server_url}</datadir>
    </dir>
  </extension>
  <extension point="xbmc.addon.metadata">
    <platform>all</platform>
    <summary lang="en">YoMovies Local Repository</summary>
    <description lang="en">Install and auto-update the YoMovies video addon from your local PC server.</description>
    <license>GPL-2.0-only</license>
    <assets>
      <icon>icon.png</icon>
      <fanart>fanart.jpg</fanart>
    </assets>
  </extension>
</addon>
"""
    with open(repo_addon_xml, "w", encoding="utf-8") as f:
        f.write(xml_content)
    print(f"[+] Updated local repository definition at: {repo_addon_xml}")
    
    # Extract version from repository addon.xml
    repo_version = "1.0.0"
    if os.path.exists(repo_addon_xml):
        try:
            tree = ET.parse(repo_addon_xml)
            repo_version = tree.getroot().attrib.get('version', '1.0.0')
        except:
            pass
        
    repo_files = {
        "addon.xml": repo_addon_xml,
        "icon.png": os.path.join(WORKSPACE_DIR, "icon.png"),
        "fanart.jpg": os.path.join(WORKSPACE_DIR, "fanart.jpg")
    }
    create_addon_zip("repository.yomovies", repo_version, repo_files)
    
    # 3. Generate/Update the master addons.xml and addons.xml.md5
    print("\n[*] Compiling master addons.xml...")
    root = ET.Element("addons")
    
    addons_to_compile = [
        os.path.join(WORKSPACE_DIR, "addon.xml"),
        repo_addon_xml
    ]
    
    for addon_xml_path in addons_to_compile:
        if os.path.exists(addon_xml_path):
            try:
                tree = ET.parse(addon_xml_path)
                addon_root = tree.getroot()
                root.append(addon_root)
                print(f"  Merged xml from {os.path.basename(os.path.dirname(addon_xml_path)) or 'root'}")
            except Exception as e:
                print(f"  [Error] Failed to merge XML from {addon_xml_path}: {e}")
                
    xml_str = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' + ET.tostring(root, encoding="utf-8").decode('utf-8')
    addons_xml_out = os.path.join(WORKSPACE_DIR, "addons.xml")
    with open(addons_xml_out, "w", encoding="utf-8") as f:
        f.write(xml_str)
    print(f"[+] Compiled addons.xml saved to: {addons_xml_out}")
    
    md5_hash = get_md5(xml_str)
    md5_out = os.path.join(WORKSPACE_DIR, "addons.xml.md5")
    with open(md5_out, "w", encoding="utf-8") as f:
        f.write(md5_hash)
    print(f"[+] Compiled addons.xml.md5 checksum: {md5_hash}")
    
    # Generate HTML index files
    create_directory_indexes()
    
    print("\n" + "="*60)
    print(" LOCAL KODI REPOSITORY SUCCESSFULLY COMPILED!")
    print(f" To host: run 'python start_server.py' in this folder.")
    print("="*60)

if __name__ == "__main__":
    generate_repo()
