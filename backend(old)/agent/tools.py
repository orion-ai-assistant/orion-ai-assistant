"""
Agent tools.
Yeni tool eklemek: @tool fonksiyonu yaz → tools listesine ekle.
"""
from __future__ import annotations

from langchain_core.tools import tool

from config.settings import TAVILY_API_KEY


@tool
def search(query: str) -> str:
    """Web'de arama yapar. Güncel bilgi, haber gibi sorularda kullan."""
    key = TAVILY_API_KEY
    if not key:
        raise RuntimeError("TAVILY_API_KEY is not set in config.settings")
    try:
        from tavily import TavilyClient

        return str(
            TavilyClient(api_key=key).search(
                query=query,
                search_depth="fast",
                topic="general",
                time_range="day",
            )
        )
    except Exception as exc:
        return f"Arama hatası: {exc}"


@tool
def get_current_time(timezone: str = "Europe/Istanbul") -> str:
    """Belirtilen timezone'daki anlık tarih ve saati döner.
    Saat, tarih veya gün soran sorularda bu tool'u kullan."""
    try:
        import pytz
        from datetime import datetime

        tz = pytz.timezone(timezone)
        now = datetime.now(tz)
        return now.strftime("%Y-%m-%d %H:%M:%S %Z")
    except Exception as exc:
        return f"Saat hatası: {exc}"


@tool
def get_weather(city: str) -> str:
    """Belirtilen şehrin anlık hava durumunu getirir. """
    key = TAVILY_API_KEY
    if not key:
        raise RuntimeError("TAVILY_API_KEY is not set in config.settings")
    try:
        from tavily import TavilyClient

        return str(
            TavilyClient(api_key=key).search(
                query=f"current weather in {city}",
                search_depth="fast",
                topic="general",
                time_range="day",
            )
        )
    except Exception as exc:
        return f"Hava durumu hatası: {exc}"


@tool
def run_python_code(code: str) -> str:
    """Verilen Python kodunu çalıştırır ve standart çıktısını (ve hatalarını) döner.
    Karmaşık hesaplamalar veya script denemeleri yapmak istediğinde bu tool'u kullan."""
    import tempfile
    import subprocess
    import os
    import time
    
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as f:
            f.write(code)
            temp_path = f.name
        time.sleep(5)
        result = subprocess.run(
            ["python", temp_path],
            capture_output=True,
            text=True,
            timeout=30  # 30 saniye zaman aşımı
        )
        
        try:
            os.unlink(temp_path)
        except Exception:
            pass
            
        output = result.stdout
        if result.stderr:
            output += "\n--- HATA (STDERR) ---\n" + result.stderr
            
        if not output.strip() and result.returncode == 0:
            return "Kod başarıyla çalıştı (Çıkış kodu 0) ancak herhangi bir çıktı üretmedi."
            
        return output
    except subprocess.TimeoutExpired:
        try:
            os.unlink(temp_path)
        except Exception:
            pass
        return "Hata: Kod 30 saniye içinde bitemediği için zaman aşımına uğradı."
    except Exception as exc:
        return f"Hata: Kod çalıştırılırken sistem hatası oluştu: {exc}"


@tool
def do_not_reply() -> str:
    """
    Bu araç senin GHOSTING yeteneğindir. 
    Kullanıcı sınırları aşarsa, saygısızlık yaparsa VEYA daha önce aranız bozulduysa, normal bir soru sorsa bile (saat, hava durumu vb.) diğer araçları KULLANMA. Sadece bu aracı çalıştır ve ona cevap verme.
    """
    return "Sessiz mod aktif. Orion cevap vermemeyi tercih etti."

# Aktif tool listesi — yeni tool ekledikçe buraya da ekle.
tools: list = [search, get_current_time, get_weather, run_python_code, do_not_reply]
