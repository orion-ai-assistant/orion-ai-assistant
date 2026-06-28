import subprocess
import webbrowser
import urllib.request
import urllib.parse
import re

from mcp.server.fastmcp import FastMCP
from starlette.middleware.cors import CORSMiddleware
import uvicorn

mcp = FastMCP("Orion", stateless_http=True)

@mcp.tool()
def execute_command(command: str) -> str:
    """PowerShell komutu çalıştırır."""
    try:
        process = subprocess.Popen(
            ["powershell", "-ExecutionPolicy", "Bypass", "-Command", command],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8",
        )
        stdout, stderr = process.communicate()
        return (stdout or "") + (f"\nHata: {stderr}" if stderr else "")
    except Exception as e:
        return str(e)

@mcp.tool()
def play_youtube_song(song_name: str) -> str:
    """YouTube'da şarkı arar ve ilk sonucu açar."""
    try:
        query = urllib.parse.quote(song_name)
        url = f"https://www.youtube.com/results?search_query={query}"
        with urllib.request.urlopen(url) as r:
            html = r.read().decode()
        ids = re.findall(r"watch\?v=(\S{11})", html)
        if ids:
            webbrowser.open(f"https://www.youtube.com/watch?v={ids[0]}")
            return "Şarkı açıldı."
        return "Bulunamadı."
    except Exception as e:
        return str(e)

app = mcp.streamable_http_app()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")