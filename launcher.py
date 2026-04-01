"""ez-trading Launcher — double-click to start, browser opens automatically."""
import os
import sys
import time
import threading
import webbrowser
import socket

# IMPORTANT: Compute exe_dir BEFORE chdir
exe_dir = os.path.dirname(os.path.abspath(sys.argv[0]))

# Set project root for path resolution
if getattr(sys, 'frozen', False):
    os.environ['EZ_ROOT'] = sys._MEIPASS
    os.chdir(sys._MEIPASS)
else:
    os.environ['EZ_ROOT'] = os.path.dirname(os.path.abspath(__file__))
    os.chdir(os.environ['EZ_ROOT'])
env_file = os.path.join(exe_dir, '.env')
if os.path.exists(env_file):
    for line in open(env_file):
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, _, v = line.partition('=')
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v

# Ensure data directory exists (next to exe)
data_dir = os.path.join(exe_dir, 'data')
os.makedirs(data_dir, exist_ok=True)
os.environ['EZ_DATA_DIR'] = data_dir

PORT = 8000


def find_free_port(start=8000):
    for port in range(start, start + 100):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(('127.0.0.1', port)) != 0:
                return port
    return start


def open_browser(port):
    """Wait for server to start, then open browser."""
    url = f'http://localhost:{port}'
    for _ in range(30):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                if s.connect_ex(('127.0.0.1', port)) == 0:
                    webbrowser.open(url)
                    return
        except Exception:
            pass
        time.sleep(1)


def main():
    port = find_free_port(PORT)

    print(f"""
    ╔══════════════════════════════════════╗
    ║      ez-trading v0.2.12.1            ║
    ║  Agent-Native Quant Platform         ║
    ╠══════════════════════════════════════╣
    ║  Starting on http://localhost:{port}   ║
    ║  Browser will open automatically...  ║
    ║                                      ║
    ║  Press Ctrl+C to stop                ║
    ╚══════════════════════════════════════╝
    """)

    # Open browser in background thread
    threading.Thread(target=open_browser, args=(port,), daemon=True).start()

    # Start uvicorn
    import uvicorn
    uvicorn.run("ez.api.app:app", host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
