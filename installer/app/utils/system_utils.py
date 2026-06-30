def _read_env(path: str) -> dict[str, str]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return {k.strip(): v.strip() for line in f if "=" in line and not line.strip().startswith("#") for k, v in [line.split("=", 1)]}
    except OSError:
        return {}

async def check_and_resolve_port_conflict(port: int = 20128) -> dict:
    try:
        import psutil
    except ImportError:
        return {"status": "error", "message": "psutil kütüphanesi eksik, port taraması yapılamıyor."}
    
    found_pid = None
    process_name = ""
    
    try:
        for conn in psutil.net_connections(kind='inet'):
            if conn.laddr.port == port and conn.status == 'LISTEN':
                found_pid = conn.pid
                break
                
        if found_pid:
            try:
                proc = psutil.Process(found_pid)
                process_name = proc.name()
                proc.kill()
                proc.wait(timeout=3)
                return {"status": "success", "message": f"Port {port}'u kullanan {process_name} (PID: {found_pid}) basariyla kapatildi."}
            except psutil.AccessDenied:
                return {"status": "error", "message": f"Port {port}'u kullanan uygulama kapatilamadi (Erisim engellendi). Lutfen manuel kapatin."}
            except psutil.NoSuchProcess:
                return {"status": "success", "message": "Islem zaten kapanmis."}
            except Exception as e:
                return {"status": "error", "message": f"Kapatma hatasi: {e}"}
                
        return {"status": "success", "message": f"Port {port} temiz, cakisca yok."}
    except psutil.AccessDenied:
        # Some OS restrict net_connections
        return {"status": "warning", "message": "Port taramasi icin yetki yetersiz, lutfen portlarin bos oldugundan emin olun."}
    except Exception as e:
        return {"status": "error", "message": f"Port tarama hatasi: {e}"}
