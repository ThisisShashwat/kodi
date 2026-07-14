from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import os

WORKSPACE_DIR = r"C:\Users\iamsh\Documents\antigravity\zealous-mendeleev"

class CustomHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        # Add headers to support HLS streaming/range requests
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        super().end_headers()

def run_server():
    os.chdir(WORKSPACE_DIR)
    # ThreadingHTTPServer handles concurrent requests from Kodi in separate threads
    server = ThreadingHTTPServer(('0.0.0.0', 8080), CustomHandler)
    print("="*60)
    # Get local IP
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('10.255.255.255', 1))
        local_ip = s.getsockname()[0]
    except Exception:
        local_ip = '127.0.0.1'
    finally:
        s.close()
        
    print(f" MULTITHREADED KODI REPOSITORY SERVER RUNNING")
    print(f" Address: http://{local_ip}:8080/")
    print("="*60)
    print("Press Ctrl+C to stop the server.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")

if __name__ == "__main__":
    run_server()
