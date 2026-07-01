import os
import urllib.request

from .. import config
from . import i18n

VALID_EXTENSIONS = (".gguf", ".safetensors", ".pth", ".bin")

# ---------------------------------------------------------------------------
# Model check
# ---------------------------------------------------------------------------
def check_models(service_id: str) -> list[dict]:
    manifest, m_path = config.find_manifest(service_id)
    if not manifest:
        return []

    models_rel_path = manifest.get("models_path", "models")
    models_dir = os.path.join(os.path.dirname(m_path), models_rel_path)
    
    # Sadece model kataloğu varsa klasörü oluştur
    if manifest.get("models_catalog"):
        os.makedirs(models_dir, exist_ok=True)
    elif not os.path.exists(models_dir):
        # Model kataloğu yoksa ve klasör de yoksa boş liste dön, klasörü oluşturma
        return []

    known: set[str] = set()
    results = []

    for m in manifest.get("models_catalog", []):
        entry, rel = _catalog_entry(service_id, m, models_dir)
        if rel:
            known.add(rel)
        results.append(entry)

    results.extend(_local_entries(models_dir, known, manifest))
    return results


def _catalog_entry(service_id: str, m: dict, models_dir: str) -> tuple[dict, str | None]:
    dl       = config.DOWNLOADING_MODELS.get(f"{service_id}:{m['id']}")
    m_folder = os.path.join(models_dir, m.get("folder", ""))
    
    if m.get("file_name"):
        size = _file_size(os.path.join(models_dir, m["file_name"]))
    else:
        size = _dir_size(m_folder) if os.path.isdir(m_folder) else 0

    rel = m.get("file_name") or m.get("folder") or m.get("id")

    # Kaç dosya bittiğini sayalım (Fiziksel kontrol)
    total_files = 1 if m.get("file_name") else len(m.get("files", []))
    finished_count = 0
    if m.get("file_name"):
        f_path = os.path.join(models_dir, m["file_name"])
        if os.path.exists(f_path) and os.path.getsize(f_path) > 0:
            finished_count = 1
    else:
        for f in m.get("files", []):
            f_path = os.path.join(m_folder, f)
            if os.path.exists(f_path) and os.path.getsize(f_path) > 0:
                finished_count += 1

    is_installed = (finished_count == total_files and total_files > 0)
    status_text = f"{finished_count}/{total_files} dosya bitti" if total_files > 1 else ""

    target_path = os.path.join(models_dir, m["file_name"]) if m.get("file_name") else m_folder

    return {
        "id": m["id"], "name": m["name"], "type": "catalog",
        "is_installed":      is_installed,
        "is_downloading":    dl is not None,
        "download_progress": dl["progress"] if dl else None,
        "total_expected_mb": dl["total_mb"] if dl else 0,
        "is_incomplete":     not is_installed and size > 0 and dl is None,
        "incomplete_status": status_text,
        "size_mb":           round(size / 1024 ** 2, 2),
        "manifest_size_mb":  m.get("size_mb", 0),
        "path": target_path, "rel_path": rel,
    }, rel


def _local_entries(models_dir: str, known: set, manifest: dict) -> list[dict]:
    results = []
    # 1. Seviye: Ana klasördeki dosyalar ve klasörlerin kendisi
    for entry in os.listdir(models_dir):
        if entry.startswith("."):
            continue
        full = os.path.join(models_dir, entry)
        
        # Dosya ise (Top-level)
        if os.path.isfile(full) and entry.endswith(VALID_EXTENSIONS):
            if entry not in known:
                # Sadeleştirilmiş isim
                display_name = entry
                for ext in VALID_EXTENSIONS:
                    if display_name.lower().endswith(ext):
                        display_name = display_name[:-len(ext)]
                        break

                results.append({
                    "id": entry, "name": display_name, "type": "local",
                    "is_installed": True, "is_downloading": False, "is_incomplete": False,
                    "size_mb": round(os.path.getsize(full) / 1024 ** 2, 2),
                    "path": full, "rel_path": entry,
                })
        
        # Klasör ise
        elif os.path.isdir(full):
            # Klasörün kendisini bir bütün olarak ekle (Klasör seçimi için)
            if entry not in known:
                # Klasör içinde geçerli dosya var mı kontrolü
                if any(f.endswith(VALID_EXTENSIONS) for f in os.listdir(full)):
                    results.append({
                        "id": entry, "name": entry, "type": "local",
                        "is_installed": True, "is_downloading": False, "is_incomplete": False,
                        "size_mb": round(_dir_size(full) / 1024 ** 2, 2),
                        "path": full, "rel_path": entry,
                    })

            # 2. Seviye: Klasörün içindeki dosyaları tara
            for sub_entry in os.listdir(full):
                if sub_entry.startswith("."): continue
                sub_full = os.path.join(full, sub_entry)
                
                if os.path.isfile(sub_full) and sub_entry.endswith(VALID_EXTENSIONS):
                    # Embedding servislerinde mmproj/vision dosyalarını tekil olarak listeleme
                    is_embed = manifest.get("category") == "embedding" if manifest else False
                    if not is_embed and ("mmproj" in sub_entry.lower() or "vision" in sub_entry.lower()):
                        rel_sub = os.path.join(entry, sub_entry).replace("\\", "/")
                        if rel_sub not in known:
                            display_name = sub_entry
                            for ext in VALID_EXTENSIONS:
                                if display_name.lower().endswith(ext):
                                    display_name = display_name[:-len(ext)]
                                    break
                            
                            results.append({
                                "id": rel_sub, "name": display_name, "type": "local",
                                "is_installed": True, "is_downloading": False, "is_incomplete": False,
                                "size_mb": round(os.path.getsize(sub_full) / 1024 ** 2, 2),
                                "path": sub_full, "rel_path": rel_sub,
                            })
    return results


def _dir_size(path: str) -> int:
    if not os.path.isdir(path): return 0
    return sum(os.path.getsize(os.path.join(r, f))
               for r, _, files in os.walk(path) 
               for f in files if not f.startswith(".") and not f.endswith(".downloading"))


def _file_size(path: str) -> int:
    return os.path.getsize(path) if os.path.exists(path) else 0


def _first_valid_model(folder: str, prefix: str) -> str | None:
    return next((os.path.join(prefix, f).replace("\\", "/")
                 for f in os.listdir(folder) if f.endswith(VALID_EXTENSIONS)), None) if os.path.isdir(folder) else None


# ---------------------------------------------------------------------------
# Background task
# ---------------------------------------------------------------------------
def run_model_download(service_id: str, service_dir: str, model_data: dict):
    model_key = f"{service_id}:{model_data['id']}"
    config.DOWNLOADING_MODELS[model_key] = {"progress": "Başlatılıyor...", "total_mb": 0}
    try:
        manifest, _ = config.find_manifest(service_id)
        models_rel_path = manifest.get("models_path", "models") if manifest else "models"
        target_dir = os.path.join(service_dir, models_rel_path, model_data.get("folder", ""))
        os.makedirs(target_dir, exist_ok=True)

        for f in os.listdir(target_dir):
            if f.endswith(".downloading"):
                try: os.remove(os.path.join(target_dir, f))
                except Exception: pass

        files = ([(model_data["download_url"], model_data["file_name"])] if "file_name" in model_data
                 else [(model_data["download_url"] + f, f) for f in model_data.get("files", [])])

        total_bytes = sum(_remote_size(url) for url, _ in files)
        config.DOWNLOADING_MODELS[model_key]["total_mb"] = round(total_bytes / 1024 ** 2, 2)


        downloaded = 0
        for idx, (url, fname) in enumerate(files):
            if config.SHOULD_EXIT: break
            
            prefix   = f"{idx + 1}/{len(files)}"
            dest, tmp = os.path.join(target_dir, fname), os.path.join(target_dir, fname + ".downloading")
            expected  = _remote_size(url)

            # Eğer dosya zaten tam olarak inmişse indirmiş gibi say ve atla
            if os.path.exists(dest):
                actual = os.path.getsize(dest)
                if expected > 0 and abs(actual - expected) < 1024:
                    print(f"[MODEL] {fname} zaten mevcut, atlanıyor.")
                    downloaded += actual
                    continue

            req = urllib.request.Request(url)
            req.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
            with urllib.request.urlopen(req) as resp, open(tmp, "wb") as out:
                while True:
                    if config.SHOULD_EXIT: break
                    chunk = resp.read(1024 * 1024)
                    if not chunk: break
                    out.write(chunk)
                    downloaded += len(chunk)
                    if total_bytes > 0:
                        pct = int(downloaded / total_bytes * 100)
                        config.DOWNLOADING_MODELS[model_key]["progress"] = f"{prefix} (%{pct})"
                    else:
                        config.DOWNLOADING_MODELS[model_key]["progress"] = f"{prefix} (İndiriliyor...)"

            if config.SHOULD_EXIT:
                if os.path.exists(tmp): os.remove(tmp)
                print(f"[SYSTEM] İndirme iptal edildi: {fname}")
                break

            actual = os.path.getsize(tmp)
            if expected > 0 and abs(actual - expected) > 1024:
                if os.path.exists(tmp): os.remove(tmp)
                raise ValueError(f"{fname}: boyut uyuşmazlığı (beklenen {expected}, alınan {actual})")

            # Windows WinError 32 (dosya kilitli) için daha sabırlı finalize
            import time
            import shutil
            success = False
            for i in range(12):
                try:
                    os.replace(tmp, dest)
                    success = True
                    break
                except OSError as e:
                    if i == 11:
                        break
                    time.sleep(0.5 + i * 0.25)

            if not success and os.path.exists(tmp):
                for i in range(6):
                    try:
                        shutil.copyfile(tmp, dest)
                        os.remove(tmp)
                        success = True
                        break
                    except OSError:
                        time.sleep(0.5 + i * 0.5)

            if success:
                print(f"[MODEL] {fname} tamamlandı.")

        if not config.SHOULD_EXIT:
            print("[MODEL] Tüm dosyalar başarıyla indirildi.")
        else:
            print("[SYSTEM] İndirme yarıda kesildi.")
    except Exception as e:
        print(f"[DOWNLOAD ERROR] {model_key}: {e}")
    finally:
        config.DOWNLOADING_MODELS.pop(model_key, None)


def _remote_size(url: str) -> int:
    try:
        # HEAD isteği daha hızlıdır ve sadece header'ları çeker
        req = urllib.request.Request(url, method="HEAD")
        req.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
        with urllib.request.urlopen(req, timeout=5) as r:
            return int(r.getheader("Content-Length", 0))
    except Exception:
        try:
            # HEAD başarısız olursa GET ile dene (bazı sunucular HEAD desteklemez)
            req = urllib.request.Request(url)
            req.add_header("User-Agent", "Mozilla/5.0")
            with urllib.request.urlopen(req, timeout=5) as r:
                return int(r.getheader("Content-Length", 0))
        except Exception:
            return 0


def delete_model(service_id: str, model_id: str) -> dict:
    models_list = check_models(service_id)
    model_entry = next((m for m in models_list if m["id"] == model_id), None)
    if not model_entry:
        return {"status": "error", "message": i18n.t("MSG_MODEL_NOT_FOUND")}
    
    path = model_entry.get("path")
    if not path or not os.path.exists(path):
        return {"status": "error", "message": i18n.t("MSG_MODEL_FILE_NOT_FOUND")}
        
    manifest, m_path = config.find_manifest(service_id)
    if not manifest:
        return {"status": "error", "message": i18n.t("MSG_MANIFEST_NOT_FOUND")}
        
    models_rel_path = manifest.get("models_path", "models")
    models_dir = os.path.abspath(os.path.join(os.path.dirname(m_path), models_rel_path))
    
    resolved_path = os.path.abspath(path)
    if not resolved_path.startswith(models_dir):
        return {"status": "error", "message": i18n.t("MSG_INVALID_PATH")}
        
    try:
        import shutil
        if os.path.isdir(resolved_path):
            shutil.rmtree(resolved_path)
        else:
            os.remove(resolved_path)
        return {"status": "success", "message": i18n.t("MSG_MODEL_DELETED_SUCCESS", model_entry['name'])}
    except Exception as e:
        return {"status": "error", "message": i18n.t("MSG_MODEL_DELETE_FAILED", str(e))}
