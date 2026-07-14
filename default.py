import urllib.request
import urllib.parse
import urllib.error
import ssl
import re
import sys
import os
import json

BASE_URL = "https://yomovies.business"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# Create a default context that ignores SSL verification
SSL_CONTEXT = ssl.create_default_context()
SSL_CONTEXT.check_hostname = False
SSL_CONTEXT.verify_mode = ssl.CERT_NONE

# Detect if we are running inside Kodi or as a CLI script
try:
    import xbmc
    import xbmcgui
    import xbmcplugin
    import xbmcaddon
    import xbmcvfs
    KODI_MODE = True
except ImportError:
    KODI_MODE = False

# ==============================================================================
# SCRAPER CORE LOGIC (Shared between Kodi and CLI fallback)
# ==============================================================================

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
        if KODI_MODE:
            xbmc.log(f"[YoMovies] Failed to fetch {url}: {e}", xbmc.LOGERROR)
        else:
            print(f"\n[Error] Failed to fetch {url}: {e}")
        return ""

def score_quality(label):
    """Assigns a score to quality labels so we can prioritize the highest quality server."""
    label = label.lower()
    if 'uhd' in label or '4k' in label or '2160' in label:
        return 100
    if '1080' in label or 'fhd' in label:
        return 80
    if '720' in label:
        return 60
    if 'hd' in label:
        return 50
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
        
        href_m = re.search(r'href="([^"]+)"[^>]*class="[^"]*ml-mask[^"]*"', segment)
        if not href_m:
            href_m = re.search(r'class="ml-mask[^"]*"[^>]*href="([^"]+)"', segment)
            
        if href_m:
            href = href_m.group(1)
            
            title_m = re.search(r'oldtitle="([^"]+)"', segment)
            if not title_m:
                title_m = re.search(r'<h2>([^<]+)</h2>', segment)
            if not title_m:
                title_m = re.search(r'alt="([^"]+)"', segment)
            title = title_m.group(1).strip() if title_m else "Unknown Title"
            
            img_m = re.search(r'data-original="([^"]+)"', segment)
            if not img_m:
                img_m = re.search(r'src="([^"]+)"[^>]*class="[^"]*lazy', segment)
            poster = img_m.group(1) if img_m else ""
            
            quality_m = re.search(r'class="mli-quality">([^<]+)</span>', segment)
            quality = quality_m.group(1).strip() if quality_m else "HD"
            
            year_m = re.search(r'/release-year/(\d{4})/', segment)
            year = year_m.group(1) if year_m else ""
            
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
    """Decodes Dean Edwards packed JavaScript code (p.a.c.k-e.r)."""
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

def resolve_m3u8_playlist(master_url, referer_url):
    """Fetches the master .m3u8. In Kodi mode, returns the master URL directly. In CLI, returns highest quality."""
    if '.m3u8' not in master_url:
        return master_url
        
    if KODI_MODE:
        # Return the master playlist so Kodi's inputstream.adaptive can parse it natively
        return master_url
        
    # CLI Mode: Extract highest quality for VLC
    content = fetch_html(master_url, referer=referer_url)
    if not content or '#EXT-X-STREAM-INF' not in content:
        return master_url
        
    lines = content.split('\n')
    streams = []
    current_resolution = (0, 0)
    current_bandwidth = 0
    
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
            full_url = line
            if not line.startswith('http'):
                if line.startswith('/'):
                    domain_m = re.match(r'(https?://[^/]+)', master_url)
                    full_url = domain_m.group(1) + line if domain_m else line
                else:
                    full_url = base_dir + line
                
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
        streams.sort(key=lambda x: (x['width'] * x['height'], x['bandwidth']), reverse=True)
        return streams[0]['url']
        
    return master_url

def resolve_stream_from_player(player_url, referer_url):
    """Fetches player HTML and resolves direct video stream link (.m3u8 or .mp4)."""
    player_html = fetch_html(player_url, referer=referer_url)
    if not player_html:
        return None
        
    if "eval(function(p,a,c,k,e," in player_html:
        packed_blocks = re.findall(r'(eval\(function\(p,a,c,k,e,.*?\)\))', player_html)
        for block in packed_blocks:
            unpacked = unpack_dean_edwards(block)
            player_html += "\n" + unpacked
            
    stream_m = re.search(r'(?:file|source|url)\s*:\s*["\'](https?://[^\s"\'>]+\.(?:m3u8|mp4)[^\s"\'>]*)["\']', player_html, re.IGNORECASE)
    if not stream_m:
        stream_m = re.search(r'["\'](https?://[^\s"\'>]+\.(?:m3u8|mp4)[^\s"\'>]*)["\']', player_html, re.IGNORECASE)
        
    if stream_m:
        stream_url = stream_m.group(1).replace("\\/", "/")
        selected_url = resolve_m3u8_playlist(stream_url, player_url)
        return selected_url
        
    return None

def resolve_movie(movie_url):
    """Scrapes yomovies detail page, sorts servers by quality, and resolves video stream."""
    html = fetch_html(movie_url)
    if not html:
        return None
        
    tabs = re.findall(r'href=["\']#(tab\d+)["\'][^>]*>(.*?)</a>', html, re.IGNORECASE)
    server_qualities = {}
    for tab_id, label in tabs:
        label_clean = re.sub(r'<[^>]*>', '', label).strip()
        server_qualities[tab_id] = label_clean
        
    player_candidates = []
    
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

    player_candidates.sort(key=lambda x: x['score'], reverse=True)
    
    if not player_candidates:
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
                'label': 'General',
                'score': 10
            })
            
    if not player_candidates:
        return None
        
    for cand in player_candidates:
        print(f"[*] Trying server: {cand['label']}")
        stream_url = resolve_stream_from_player(cand['url'], movie_url)
        if stream_url:
            return stream_url, cand['url']
            
    return None

# ==============================================================================
# WATCH HISTORY MANAGEMENT (Shared storage)
# ==============================================================================

def get_history_file():
    if KODI_MODE:
        addon = xbmcaddon.Addon()
        profile_dir = xbmcvfs.translatePath(addon.getAddonInfo('profile'))
        if not os.path.exists(profile_dir):
            try:
                os.makedirs(profile_dir)
            except:
                pass
        return os.path.join(profile_dir, 'history.json')
    else:
        return 'history.json'

def load_history():
    filepath = get_history_file()
    if os.path.exists(filepath):
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return []

def save_to_history(movie_url, title, poster, quality, year, imdb):
    history = load_history()
    # Remove existing to avoid duplicates and move to the top
    history = [item for item in history if item['url'] != movie_url]
    history.insert(0, {
        'url': movie_url,
        'title': title,
        'poster': poster,
        'quality': quality,
        'year': year,
        'imdb': imdb
    })
    # Limit to 50 items
    history = history[:50]
    filepath = get_history_file()
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(history, f, indent=4)
    except:
        pass

def clear_history_file():
    filepath = get_history_file()
    if os.path.exists(filepath):
        try:
            os.remove(filepath)
        except:
            pass

# ==============================================================================
# KODI ADDON INTEGRATION
# ==============================================================================

if KODI_MODE:
    
    def add_directory_item(name, query_params, is_folder=True, icon="", info_labels=None):
        """Helper to append standard directory items to Kodi lists."""
        url = sys.argv[0] + "?" + urllib.parse.urlencode(query_params)
        list_item = xbmcgui.ListItem(name)
        
        # Load assets from local addon path if requested
        addon = xbmcaddon.Addon()
        addon_path = addon.getAddonInfo('path')
        
        art = {}
        if icon:
            if icon.startswith('http'):
                art['thumb'] = icon
                art['icon'] = icon
            else:
                art['thumb'] = os.path.join(addon_path, icon)
                art['icon'] = os.path.join(addon_path, icon)
        else:
            # Fallback icon
            art['thumb'] = os.path.join(addon_path, 'icon.png')
            art['icon'] = os.path.join(addon_path, 'icon.png')
            
        list_item.setArt(art)
        
        if info_labels:
            try:
                info_tag = list_item.getVideoInfoTag()
                if 'title' in info_labels:
                    info_tag.setTitle(info_labels['title'])
                if 'plot' in info_labels:
                    info_tag.setPlot(info_labels['plot'])
                if 'year' in info_labels and isinstance(info_labels['year'], int):
                    info_tag.setYear(info_labels['year'])
                if 'rating' in info_labels:
                    try:
                        info_tag.setRating(float(info_labels['rating']), 'imdb')
                    except:
                        pass
                if 'genre' in info_labels:
                    info_tag.setGenres([info_labels['genre']])
            except (AttributeError, TypeError):
                list_item.setInfo('video', info_labels)
            
        if not is_folder:
            list_item.setProperty('IsPlayable', 'true')
            
        xbmcplugin.addDirectoryItem(int(sys.argv[1]), url, list_item, is_folder)

    def main_menu():
        """Generates root directory listing."""
        add_directory_item("Bollywood Movies", {'action': 'browse', 'path': '/genre/bollywood/', 'page': 1})
        add_directory_item("Hollywood Movies", {'action': 'browse', 'path': '/genre/hollywood/', 'page': 1})
        add_directory_item("Hollywood Dubbed", {'action': 'browse', 'path': '/genre/hollywood-dubbed/', 'page': 1})
        add_directory_item("South Indian Special / Dubbed", {'action': 'browse', 'path': '/genre/south-special/', 'page': 1})
        add_directory_item("Web Series", {'action': 'browse', 'path': '/genre/web-series/', 'page': 1})
        add_directory_item("Search", {'action': 'search'})
        add_directory_item("Recently Watched / Resume", {'action': 'history'})
        xbmcplugin.endOfDirectory(int(sys.argv[1]))

    def browse_category(genre_path, page):
        """Loads and lists items for a category path and page."""
        if page == 1:
            url = f"{BASE_URL}{genre_path}"
        else:
            url = f"{BASE_URL}{genre_path}page/{page}/"
            
        html = fetch_html(url)
        if not html:
            xbmcgui.Dialog().notification("YoMovies", "Failed to load page content.", xbmcgui.NOTIFICATION_ERROR)
            xbmcplugin.endOfDirectory(int(sys.argv[1]), False)
            return

        cards = parse_cards(html)
        for card in cards:
            info = {
                'title': card['title'],
                'plot': card['title'],
                'genre': genre_path.strip('/').replace('-', ' ').title(),
            }
            if card['year'].isdigit():
                info['year'] = int(card['year'])
            if card['imdb']:
                try:
                    info['rating'] = float(card['imdb'])
                except ValueError:
                    pass
            
            # Displays quality label next to title
            title_display = f"{card['title']} [{card['quality']}]"
            play_params = {
                'action': 'play',
                'url': card['url'],
                'title': card['title'],
                'poster': card['poster'],
                'quality': card['quality'],
                'year': card['year'],
                'imdb': card['imdb']
            }
            add_directory_item(title_display, play_params, is_folder=False, icon=card['poster'], info_labels=info)

        # Pagination: add "Next Page" item if cards count matches standard page size (40)
        if len(cards) >= 40:
            next_label = f"[COLOR orange]>> Next Page (Page {page + 1}) >>[/COLOR]"
            add_directory_item(next_label, {'action': 'browse', 'path': genre_path, 'page': page + 1})
            
        xbmcplugin.endOfDirectory(int(sys.argv[1]))

    def run_search(query=None, page=1):
        """Prompts search input or lists search query results."""
        if not query:
            keyboard = xbmc.Keyboard('', 'Search YoMovies')
            keyboard.doModal()
            if keyboard.isConfirmed():
                query = keyboard.getText().strip()
                
        if not query:
            xbmcplugin.endOfDirectory(int(sys.argv[1]), False)
            return

        if page == 1:
            url = f"{BASE_URL}/?s={urllib.parse.quote_plus(query)}"
        else:
            url = f"{BASE_URL}/page/{page}/?s={urllib.parse.quote_plus(query)}"
            
        html = fetch_html(url)
        if not html:
            xbmcgui.Dialog().notification("YoMovies", "Failed to load search results.", xbmcgui.NOTIFICATION_ERROR)
            xbmcplugin.endOfDirectory(int(sys.argv[1]), False)
            return

        cards = parse_cards(html)
        for card in cards:
            info = {
                'title': card['title'],
                'plot': card['title']
            }
            if card['year'].isdigit():
                info['year'] = int(card['year'])
            if card['imdb']:
                try:
                    info['rating'] = float(card['imdb'])
                except ValueError:
                    pass
                    
            title_display = f"{card['title']} [{card['quality']}]"
            play_params = {
                'action': 'play',
                'url': card['url'],
                'title': card['title'],
                'poster': card['poster'],
                'quality': card['quality'],
                'year': card['year'],
                'imdb': card['imdb']
            }
            add_directory_item(title_display, play_params, is_folder=False, icon=card['poster'], info_labels=info)

        # Pagination
        if len(cards) >= 40:
            next_label = f"[COLOR orange]>> Next Page (Page {page + 1}) >>[/COLOR]"
            add_directory_item(next_label, {'action': 'search', 'query': query, 'page': page + 1})
            
        xbmcplugin.endOfDirectory(int(sys.argv[1]))

    def show_history():
        """Lists recently watched items stored in history.json."""
        history = load_history()
        if not history:
            xbmcgui.Dialog().notification("YoMovies", "No watch history found.", xbmcgui.NOTIFICATION_INFO)
            xbmcplugin.endOfDirectory(int(sys.argv[1]), False)
            return

        for item in history:
            info = {
                'title': item['title'],
                'plot': item['title']
            }
            if item['year'].isdigit():
                info['year'] = int(item['year'])
            if item['imdb']:
                try:
                    info['rating'] = float(item['imdb'])
                except ValueError:
                    pass
            
            title_display = f"{item['title']} [{item['quality']}]"
            play_params = {
                'action': 'play',
                'url': item['url'],
                'title': item['title'],
                'poster': item['poster'],
                'quality': item['quality'],
                'year': item['year'],
                'imdb': item['imdb']
            }
            add_directory_item(title_display, play_params, is_folder=False, icon=item['poster'], info_labels=info)

        # Clear history button
        add_directory_item("[COLOR red]Clear Recently Watched[/COLOR]", {'action': 'clear_history'}, is_folder=False)
        xbmcplugin.endOfDirectory(int(sys.argv[1]))

    def play_video(movie_url, title, poster, quality, year, imdb):
        """Resolves embed and plays direct .m3u8/.mp4 stream in Kodi player."""
        # Save this play attempt to history list
        save_to_history(movie_url, title, poster, quality, year, imdb)
        
        p_dialog = xbmcgui.DialogProgress()
        p_dialog.create("YoMovies", "Resolving stream link...")
        
        try:
            stream_info = resolve_movie(movie_url)
            p_dialog.close()
            
            if stream_info:
                stream_url, embed_url = stream_info
                
                # Append headers using Kodi's standard pipe scheme
                play_url = stream_url + '|Referer=' + urllib.parse.quote(embed_url) + '&User-Agent=' + urllib.parse.quote(USER_AGENT)
                
                # Create ListItem
                list_item = xbmcgui.ListItem(path=play_url)
                
                # If HLS, configure to play adaptively in Kodi
                if '.m3u8' in stream_url:
                    list_item.setProperty('inputstream', 'inputstream.adaptive')
                    list_item.setProperty('inputstream.adaptive.manifest_type', 'hls')
                    list_item.setMimeType('application/vnd.apple.mpegurl')
                    list_item.setContentLookup(False)
                
                xbmcplugin.setResolvedUrl(int(sys.argv[1]), True, list_item)
            else:
                xbmcgui.Dialog().notification("YoMovies", "Failed to resolve stream link.", xbmcgui.NOTIFICATION_ERROR)
                xbmcplugin.setResolvedUrl(int(sys.argv[1]), False, xbmcgui.ListItem())
        except Exception as e:
            p_dialog.close()
            xbmcgui.Dialog().notification("YoMovies", f"Error playing file: {e}", xbmcgui.NOTIFICATION_ERROR)
            xbmcplugin.setResolvedUrl(int(sys.argv[1]), False, xbmcgui.ListItem())

    # ==============================================================================
    # ROUTER AND ENTRYPOINT
    # ==============================================================================
    
    def router():
        params = {}
        if sys.argv[2]:
            params = dict(urllib.parse.parse_qsl(sys.argv[2].lstrip('?')))
            
        action = params.get('action')
        
        if not action:
            main_menu()
        elif action == 'browse':
            path = params.get('path')
            page = int(params.get('page', 1))
            browse_category(path, page)
        elif action == 'search':
            query = params.get('query')
            page = int(params.get('page', 1))
            run_search(query, page)
        elif action == 'history':
            show_history()
        elif action == 'clear_history':
            clear_history_file()
            xbmcgui.Dialog().notification("YoMovies", "History cleared.", xbmcgui.NOTIFICATION_INFO)
            xbmc.executebuiltin("Container.Refresh")
        elif action == 'play':
            url = params.get('url')
            title = params.get('title', 'Movie')
            poster = params.get('poster', '')
            quality = params.get('quality', '')
            year = params.get('year', '')
            imdb = params.get('imdb', '')
            play_video(url, title, poster, quality, year, imdb)

    if __name__ == "__main__":
        router()

# ==============================================================================
# COMMAND LINE FALLBACK (For local CLI testing/running)
# ==============================================================================
else:
    import subprocess
    
    def play_with_vlc(stream_url, referer_url):
        vlc_paths = [
            r"C:\Program Files\VideoLAN\VLC\vlc.exe",
            r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe",
            os.path.expandvars(r"%PROGRAMFILES%\VideoLAN\VLC\vlc.exe"),
            os.path.expandvars(r"%PROGRAMFILES(X86)%\VideoLAN\VLC\vlc.exe"),
            "vlc"
        ]
        
        vlc_path = None
        for path in vlc_paths:
            if path == "vlc" or os.path.exists(path):
                vlc_path = path
                break
                
        if not vlc_path:
            print("[!] Could not find VLC player. Play URL manually.")
            return False
            
        print(f"[*] Launching VLC: {vlc_path}...")
        try:
            cmd = [vlc_path, stream_url, f"--http-referrer={referer_url}"]
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print("[+] VLC launched successfully!")
            return True
        except Exception as e:
            print(f"[!] Error: {e}")
            return False

    def cli_browse_category(genre_path, category_name):
        page = 1
        while True:
            url = f"{BASE_URL}{genre_path}" if page == 1 else f"{BASE_URL}{genre_path}page/{page}/"
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
            print("\n[N] Next Page | [P] Prev Page | [Q] Back")
            choice = input("\nSelect index or option: ").strip().lower()
            if choice == 'n':
                page += 1
            elif choice == 'p':
                page = max(1, page - 1)
            elif choice == 'q':
                break
            elif choice.isdigit():
                idx = int(choice) - 1
                if 0 <= idx < len(cards):
                    selected = cards[idx]
                    stream_info = resolve_movie(selected['url'])
                    if stream_info:
                        stream_url, embed_url = stream_info
                        print(f"\nSUCCESS:\nStream: {stream_url}\nReferer: {embed_url}\n")
                        play_with_vlc(stream_url, embed_url)
                        input("Press Enter to continue...")
                    else:
                        print("[-] Failed to resolve.")
                        input("Press Enter to continue...")

    def cli_search():
        query = input("\nEnter search query: ").strip()
        if not query:
            return
        page = 1
        while True:
            url = f"{BASE_URL}/?s={urllib.parse.quote_plus(query)}" if page == 1 else f"{BASE_URL}/page/{page}/?s={urllib.parse.quote_plus(query)}"
            print(f"\n[*] Searching '{query}' - Page {page}...")
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
            print("\n[N] Next Page | [P] Prev Page | [Q] Back")
            choice = input("\nSelect index or option: ").strip().lower()
            if choice == 'n':
                page += 1
            elif choice == 'p':
                page = max(1, page - 1)
            elif choice == 'q':
                break
            elif choice.isdigit():
                idx = int(choice) - 1
                if 0 <= idx < len(cards):
                    selected = cards[idx]
                    stream_info = resolve_movie(selected['url'])
                    if stream_info:
                        stream_url, embed_url = stream_info
                        print(f"\nSUCCESS:\nStream: {stream_url}\nReferer: {embed_url}\n")
                        play_with_vlc(stream_url, embed_url)
                        input("Press Enter to continue...")
                    else:
                        print("[-] Failed to resolve.")
                        input("Press Enter to continue...")

    def cli_main():
        while True:
            print("\n" + "="*40)
            print("   YOMOVIES HYBRID CLI & KODI ADDON")
            print("="*40)
            print("[1] Bollywood Movies")
            print("[2] Hollywood Movies")
            print("[3] Hollywood Dubbed")
            print("[4] South Indian Special")
            print("[5] Web Series")
            print("[6] Search Movie/TV Series")
            print("[7] Exit")
            choice = input("\nEnter choice: ").strip()
            if choice == '1':
                cli_browse_category("/genre/bollywood/", "Bollywood Movies")
            elif choice == '2':
                cli_browse_category("/genre/hollywood/", "Hollywood Movies")
            elif choice == '3':
                cli_browse_category("/genre/hollywood-dubbed/", "Hollywood Dubbed")
            elif choice == '4':
                cli_browse_category("/genre/south-special/", "South Dubbed")
            elif choice == '5':
                cli_browse_category("/genre/web-series/", "Web Series")
            elif choice == '6':
                cli_search()
            elif choice == '7':
                sys.exit(0)

    if __name__ == "__main__":
        cli_main()
