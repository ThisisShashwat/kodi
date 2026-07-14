import urllib.request
import urllib.parse
import urllib.error
import ssl
import re
import sys
import os
import subprocess

BASE_URL = "https://yomovies.business"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# Create a default context that ignores SSL verification to avoid certificate errors on various devices
SSL_CONTEXT = ssl.create_default_context()
SSL_CONTEXT.check_hostname = False
SSL_CONTEXT.verify_mode = ssl.CERT_NONE

def fetch_html(url, referer=None):
    """Fetches HTML content from a URL using standard urllib with proper headers."""
    headers = {
        "User-Agent": USER_AGENT
    }
    if referer:
        headers["Referer"] = referer

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, context=SSL_CONTEXT, timeout=15) as response:
            return response.read().decode('utf-8', errors='ignore')
    except Exception as e:
        print(f"\n[Error] Failed to fetch {url}: {e}")
        return ""

def score_quality(label):
    """Assigns a score to quality labels so we can choose the highest quality server."""
    label = label.lower()
    if 'uhd' in label or '4k' in label or '2160' in label:
        return 100
    if '1080' in label or 'fhd' in label:
        return 80
    if '720' in label:
        return 60
    if 'hd' in label:
        return 50  # Generic HD is better than SD
    if '480' in label or 'sd' in label:
        return 30
    if '360' in label or 'low' in label:
        return 20
    return 10

def parse_cards(html):
    """Parses movie cards from yomovies page HTML."""
    card_starts = [m.start() for m in re.finditer(r'<div[^>]*class="[^"]*ml-item[^"]*"[^>]*>', html)]
    cards = []
    
    for i in range(len(card_starts)):
        start = card_starts[i]
        end = card_starts[i+1] if i + 1 < len(card_starts) else len(html)
        segment = html[start:end]
        
        # Get details URL
        href_m = re.search(r'href="([^"]+)"[^>]*class="[^"]*ml-mask[^"]*"', segment)
        if not href_m:
            href_m = re.search(r'class="ml-mask[^"]*"[^>]*href="([^"]+)"', segment)
            
        if href_m:
            href = href_m.group(1)
            
            # Title
            title_m = re.search(r'oldtitle="([^"]+)"', segment)
            if not title_m:
                title_m = re.search(r'<h2>([^<]+)</h2>', segment)
            if not title_m:
                title_m = re.search(r'alt="([^"]+)"', segment)
            title = title_m.group(1).strip() if title_m else "Unknown Title"
            
            # Poster
            img_m = re.search(r'data-original="([^"]+)"', segment)
            if not img_m:
                img_m = re.search(r'src="([^"]+)"[^>]*class="[^"]*lazy', segment)
            poster = img_m.group(1) if img_m else ""
            
            # Quality
            quality_m = re.search(r'class="mli-quality">([^<]+)</span>', segment)
            quality = quality_m.group(1).strip() if quality_m else "HD"
            
            # Year
            year_m = re.search(r'/release-year/(\d{4})/', segment)
            year = year_m.group(1) if year_m else ""
            
            # IMDb Rating
            imdb_m = re.search(r'IMDb:\s*([\d\.]+)', segment)
            imdb = imdb_m.group(1) if imdb_m else ""
            
            cards.append({
                'title': title,
                'url': href,
                'poster': poster,
                'quality': quality,
                'year': year,
                'imdb': imdb
            })
            
    return cards

def unpack_dean_edwards(packed_str):
    """Decodes Dean Edwards packed JavaScript code (p.a.c.k.e.r)."""
    pattern = r"}\s*\(\s*'(.*)'\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*'(.*)'\s*\.split\s*\(\s*'\|'\s*\)"
    match = re.search(pattern, packed_str)
    if not match:
        pattern = r'}\s*\(\s*"(.*)"\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*"(.*)"\s*\.split\s*\(\s*"\|"\s*\)'
        match = re.search(pattern, packed_str)
        
    if not match:
        return packed_str
        
    payload, radix, count, keywords_str = match.groups()
    radix = int(radix)
    count = int(count)
    keywords = keywords_str.split('|')
    
    def baseN(num, base):
        chars = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        if num == 0:
            return "0"
        result = []
        while num > 0:
            result.append(chars[num % base])
            num //= base
        return "".join(reversed(result))
        
    translation = {}
    for i in range(count):
        token = baseN(i, radix)
        word = keywords[i] if i < len(keywords) and keywords[i] else token
        translation[token] = word
        
    def replace_word(match):
        token = match.group(0)
        return translation.get(token, token)
        
    return re.sub(r'\b\w+\b', replace_word, payload)

def resolve_max_quality_m3u8(master_url, referer_url):
    """Fetches the master .m3u8, parses all sub-playlists, and returns the highest quality stream URL."""
    if '.m3u8' not in master_url:
        return master_url
        
    print(f"[*] Parsing master playlist for highest quality...")
    content = fetch_html(master_url, referer=referer_url)
    if not content or '#EXT-X-STREAM-INF' not in content:
        return master_url
        
    lines = content.split('\n')
    streams = []
    current_resolution = (0, 0)
    current_bandwidth = 0
    
    # Extract base URL directory for relative paths
    base_parts = master_url.split('?')[0].split('/')
    base_dir = '/'.join(base_parts[:-1]) + '/'
    query_string = master_url.split('?')[1] if '?' in master_url else ''
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith('#EXT-X-STREAM-INF:'):
            res_m = re.search(r'RESOLUTION=(\d+)x(\d+)', line, re.IGNORECASE)
            current_resolution = (int(res_m.group(1)), int(res_m.group(2))) if res_m else (0, 0)
            bw_m = re.search(r'BANDWIDTH=(\d+)', line, re.IGNORECASE)
            current_bandwidth = int(bw_m.group(1)) if bw_m else 0
        elif not line.startswith('#'):
            # This is the sub-playlist URL
            full_url = line
            if not line.startswith('http'):
                # Handle relative URL
                if line.startswith('/'):
                    domain_m = re.match(r'(https?://[^/]+)', master_url)
                    full_url = domain_m.group(1) + line if domain_m else line
                else:
                    full_url = base_dir + line
                
                # Re-append query string (containing tokens/parameters) if absent
                if query_string and '?' not in full_url:
                    full_url += '?' + query_string
                    
            streams.append({
                'url': full_url,
                'width': current_resolution[0],
                'height': current_resolution[1],
                'bandwidth': current_bandwidth
            })
            current_resolution = (0, 0)
            current_bandwidth = 0
            
    if streams:
        # Sort by resolution size (width * height) and bandwidth descending
        streams.sort(key=lambda x: (x['width'] * x['height'], x['bandwidth']), reverse=True)
        best_stream = streams[0]
        print(f"[+] Selected max quality sub-playlist: {best_stream['width']}x{best_stream['height']} (Bandwidth: {best_stream['bandwidth']})")
        return best_stream['url']
        
    return master_url

def play_with_vlc(stream_url, referer_url):
    """Searches for VLC and launches it with the stream URL and Referer header."""
    vlc_paths = [
        r"C:\Program Files\VideoLAN\VLC\vlc.exe",
        r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe",
        os.path.expandvars(r"%PROGRAMFILES%\VideoLAN\VLC\vlc.exe"),
        os.path.expandvars(r"%PROGRAMFILES(X86)%\VideoLAN\VLC\vlc.exe"),
        "vlc"  # Fallback to PATH environment variable
    ]
    
    vlc_path = None
    for path in vlc_paths:
        if path == "vlc" or os.path.exists(path):
            vlc_path = path
            break
            
    if not vlc_path:
        print("[!] Could not find VLC player installation path. Please play the URL manually.")
        return False
        
    print(f"[*] Launching VLC: {vlc_path}...")
    try:
        # Launch VLC in background
        cmd = [vlc_path, stream_url, f"--http-referrer={referer_url}"]
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print("[+] VLC launched successfully! (Check your taskbar/desktop)")
        return True
    except Exception as e:
        print(f"[!] Error launching VLC: {e}")
        return False

def resolve_stream_from_player(player_url, referer_url):
    """Fetches player HTML and resolves direct video stream link (.m3u8 or .mp4)."""
    print(f"[*] Fetching player page: {player_url}")
    player_html = fetch_html(player_url, referer=referer_url)
    if not player_html:
        return None
        
    # Unpack if packed javascript is present
    if "eval(function(p,a,c,k,e," in player_html:
        print("[*] Packed script detected, unpacking...")
        packed_blocks = re.findall(r'(eval\(function\(p,a,c,k,e,.*?\)\))', player_html)
        for block in packed_blocks:
            unpacked = unpack_dean_edwards(block)
            player_html += "\n" + unpacked
            
    # Search for stream links in JWPlayer / PlayerJS settings
    stream_m = re.search(r'(?:file|source|url)\s*:\s*["\'](https?://[^\s"\'>]+\.(?:m3u8|mp4)[^\s"\'>]*)["\']', player_html, re.IGNORECASE)
    if not stream_m:
        stream_m = re.search(r'["\'](https?://[^\s"\'>]+\.(?:m3u8|mp4)[^\s"\'>]*)["\']', player_html, re.IGNORECASE)
        
    if stream_m:
        stream_url = stream_m.group(1)
        stream_url = stream_url.replace("\\/", "/")
        
        # Parse the master playlist to extract and return the direct highest-quality stream
        max_quality_url = resolve_max_quality_m3u8(stream_url, player_url)
        return max_quality_url
        
    return None

def resolve_movie(movie_url):
    """Scrapes yomovies detail page, sorts servers by quality, and resolves video stream."""
    print(f"[*] Fetching detail page: {movie_url}")
    html = fetch_html(movie_url)
    if not html:
        return None
        
    # Parse Quality Tabs/Servers
    tabs = re.findall(r'href=["\']#(tab\d+)["\'][^>]*>(.*?)</a>', html, re.IGNORECASE)
    server_qualities = {}
    for tab_id, label in tabs:
        label_clean = re.sub(r'<[^>]*>', '', label).strip()
        server_qualities[tab_id] = label_clean
        
    player_candidates = []
    
    # Associate tabs with iframe embed sources
    for tab_id, label in server_qualities.items():
        tab_start_m = re.search(r'id=["\']' + tab_id + r'["\']', html)
        if tab_start_m:
            start_pos = tab_start_m.start()
            segment = html[start_pos:start_pos+2000]
            iframe_m = re.search(r'<iframe[^>]+src=["\']([^"\']+)["\']', segment, re.IGNORECASE)
            if not iframe_m:
                iframe_m = re.search(r'<IFRAME[^>]+SRC=["\']([^"\']+)["\']', segment)
            if iframe_m:
                embed_url = iframe_m.group(1)
                if embed_url.startswith("//"):
                    embed_url = "https:" + embed_url
                
                player_candidates.append({
                    'url': embed_url,
                    'label': label,
                    'score': score_quality(label)
                })

    # Sort candidates by quality score descending (e.g. 1080P first, then 720P)
    player_candidates.sort(key=lambda x: x['score'], reverse=True)
    
    # Fallback to general iframe/download scanning if no clean tabs are associated
    if not player_candidates:
        print("[*] No structured tabs detected. Scanning general embeds...")
        iframes = re.findall(r'<iframe[^>]+src="([^"]+)"', html, re.IGNORECASE)
        iframes += re.findall(r'<IFRAME[^>]+SRC="([^"]+)"', html)
        dl_links = re.findall(r'href="([^"]+)"[^>]*>[^<]*Download', html, re.IGNORECASE)
        dl_div_m = re.search(r'<div id="list-dl"[^>]*>(.*?)</div>', html, re.DOTALL)
        if dl_div_m:
            dl_links += re.findall(r'href="([^"]+)"', dl_div_m.group(1))
            
        general_urls = []
        for url in iframes:
            if url.startswith("//"):
                url = "https:" + url
            general_urls.append(url)
        for url in dl_links:
            if "speedostream" in url and "/embed-" not in url:
                general_urls.append(url.replace(".com/", ".com/embed-"))
            elif "stream" in url or "file" in url:
                general_urls.append(url)
                
        seen = set()
        general_urls = [x for x in general_urls if not (x in seen or seen.add(x))]
        for u in general_urls:
            player_candidates.append({
                'url': u,
                'label': 'General / Fallback',
                'score': 10
            })
            
    if not player_candidates:
        print("[-] No player iframes or links found on this page.")
        return None
        
    print(f"[*] Found {len(player_candidates)} player/embed servers (Sorted by quality):")
    for idx, cand in enumerate(player_candidates):
        print(f"  [{idx+1}] {cand['label']} -> {cand['url']}")
        
    # Attempt to resolve stream URL from sorted servers (starting with highest quality)
    for cand in player_candidates:
        print(f"[*] Trying server: {cand['label']}")
        stream_url = resolve_stream_from_player(cand['url'], movie_url)
        if stream_url:
            return stream_url, cand['url']
            
    return None

def browse_category(genre_path, category_name):
    page = 1
    while True:
        if page == 1:
            url = f"{BASE_URL}{genre_path}"
        else:
            url = f"{BASE_URL}{genre_path}page/{page}/"
            
        print(f"\n[*] Loading {category_name} - Page {page}...")
        html = fetch_html(url)
        if not html:
            break
            
        cards = parse_cards(html)
        if not cards:
            print("[-] No titles found.")
            break
            
        print(f"\n=== {category_name} (Page {page}) ===")
        for idx, card in enumerate(cards):
            print(f"[{idx+1}] {card['title']} [{card['quality']}] ({card['year']}) - IMDb: {card['imdb']}")
            
        print("\n[N] Next Page")
        print("[P] Previous Page")
        print("[Q] Main Menu / Back")
        
        choice = input("\nSelect a number or option: ").strip().lower()
        if choice == 'n':
            page += 1
        elif choice == 'p':
            if page > 1:
                page -= 1
            else:
                print("Already on page 1.")
        elif choice == 'q':
            break
        elif choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(cards):
                selected = cards[idx]
                stream_info = resolve_movie(selected['url'])
                if stream_info:
                    stream_url, embed_url = stream_info
                    print("\n" + "="*80)
                    print(f"SUCCESSFULLY SCRAPED STREAM LINK FOR: {selected['title']}")
                    print(f"Direct Stream URL:\n{stream_url}")
                    print(f"Playable in Kodi/VLC with headers:")
                    print(f"Referer: {embed_url}")
                    print(f"User-Agent: {USER_AGENT}")
                    print("="*80 + "\n")
                    play_with_vlc(stream_url, embed_url)
                    input("Press Enter to continue...")
                else:
                    print("[-] Failed to scrape stream links for this title.")
                    input("Press Enter to continue...")
            else:
                print("Invalid selection.")
        else:
            print("Invalid input.")

def search_movies():
    query = input("\nEnter search query: ").strip()
    if not query:
        return
        
    page = 1
    while True:
        if page == 1:
            url = f"{BASE_URL}/?s={urllib.parse.quote_plus(query)}"
        else:
            url = f"{BASE_URL}/page/{page}/?s={urllib.parse.quote_plus(query)}"
            
        print(f"\n[*] Searching for '{query}' - Page {page}...")
        html = fetch_html(url)
        if not html:
            break
            
        cards = parse_cards(html)
        if not cards:
            print("[-] No titles found.")
            break
            
        print(f"\n=== Search Results for '{query}' (Page {page}) ===")
        for idx, card in enumerate(cards):
            print(f"[{idx+1}] {card['title']} [{card['quality']}] ({card['year']}) - IMDb: {card['imdb']}")
            
        print("\n[N] Next Page")
        print("[P] Previous Page")
        print("[Q] Main Menu / Back")
        
        choice = input("\nSelect a number or option: ").strip().lower()
        if choice == 'n':
            page += 1
        elif choice == 'p':
            if page > 1:
                page -= 1
            else:
                print("Already on page 1.")
        elif choice == 'q':
            break
        elif choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(cards):
                selected = cards[idx]
                stream_info = resolve_movie(selected['url'])
                if stream_info:
                    stream_url, embed_url = stream_info
                    print("\n" + "="*80)
                    print(f"SUCCESSFULLY SCRAPED STREAM LINK FOR: {selected['title']}")
                    print(f"Direct Stream URL:\n{stream_url}")
                    print(f"Playable in Kodi/VLC with headers:")
                    print(f"Referer: {embed_url}")
                    print(f"User-Agent: {USER_AGENT}")
                    print("="*80 + "\n")
                    play_with_vlc(stream_url, embed_url)
                    input("Press Enter to continue...")
                else:
                    print("[-] Failed to scrape stream links for this title.")
                    input("Press Enter to continue...")
            else:
                print("Invalid selection.")
        else:
            print("Invalid input.")

def main():
    while True:
        print("\n" + "="*40)
        print("      YOMOVIES CLI SCRAPER (TESTER)")
        print("="*40)
        print("[1] Bollywood Movies")
        print("[2] Hollywood Movies")
        print("[3] Hollywood Dubbed")
        print("[4] South Indian Special / Dubbed")
        print("[5] Web Series (Hindi / Dubbed)")
        print("[6] Search Movie/TV Series")
        print("[7] Exit")
        
        choice = input("\nEnter choice: ").strip()
        if choice == '1':
            browse_category("/genre/bollywood/", "Bollywood Movies")
        elif choice == '2':
            browse_category("/genre/hollywood/", "Hollywood Movies")
        elif choice == '3':
            browse_category("/genre/hollywood-dubbed/", "Hollywood Dubbed")
        elif choice == '4':
            browse_category("/genre/south-special/", "South Dubbed")
        elif choice == '5':
            browse_category("/genre/web-series/", "Web Series")
        elif choice == '6':
            search_movies()
        elif choice == '7':
            print("Exiting...")
            sys.exit(0)
        else:
            print("Invalid choice. Try again.")

if __name__ == "__main__":
    main()
