import os
import hashlib
import shutil
import tempfile
import urllib.request
import zipfile

from .. import config
from . import i18n

VALID_EXTENSIONS = (".gguf", ".safetensors", ".pth", ".bin")


def _sha256_matches(path: str, expected: str | None) -> bool:
    if not expected:
        return True
    with open(path, "rb") as stream:
        checksum = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(chunk)
    return checksum.hexdigest() == expected.lower()


def _download_zip_model(model_key: str, model_data: dict, target_dir: str) -> bool:
    """Verify a model archive, stage its files, then install the complete package."""
    names = model_data.get("files", [])
    hashes = model_data.get("sha256", {})
    archive_hash = model_data.get("archive_sha256")
    if not archive_hash or not names or any(not hashes.get(name) for name in names):
        raise ValueError("ZIP model requires archive and file SHA256 values")
    if any(name in ("", ".", "..") or "/" in name or "\\" in name or ":" in name for name in names):
        raise ValueError("Invalid ZIP model filename")
    if all(os.path.isfile(os.path.join(target_dir, name)) and
           _sha256_matches(os.path.join(target_dir, name), hashes[name]) for name in names):
        print("[MODEL] Model dosyaları zaten mevcut ve doğrulandı.")
        return True

    def cancelled():
        return config.SHOULD_EXIT or config.DOWNLOADING_MODELS.get(model_key, {}).get("cancel")

    url = model_data["download_url"]
    expected = _remote_size(url)
    config.DOWNLOADING_MODELS[model_key]["total_mb"] = round(expected / 1024 ** 2, 2)
    with tempfile.TemporaryDirectory(prefix=".model-extract-", dir=target_dir) as staging:
        archive_path = os.path.join(staging, "model.zip.downloading")
        request = urllib.request.Request(url, headers={"User-Agent": "Orion-Installer"})
        downloaded = 0
        with urllib.request.urlopen(request) as response, open(archive_path, "wb") as output:
            while True:
                if cancelled():
                    return False
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
                downloaded += len(chunk)
                progress = f"ZIP (%{int(downloaded / expected * 100)})" if expected else "ZIP (İndiriliyor...)"
                config.DOWNLOADING_MODELS[model_key]["progress"] = progress
        if cancelled():
            return False
        if expected and downloaded != expected:
            raise ValueError("ZIP boyutu uyuşmuyor")
        config.DOWNLOADING_MODELS[model_key]["progress"] = "ZIP doğrulanıyor..."
        if not _sha256_matches(archive_path, archive_hash):
            raise ValueError("ZIP SHA256 doğrulaması başarısız")
        config.DOWNLOADING_MODELS[model_key]["progress"] = "Model dosyaları açılıyor..."
        with zipfile.ZipFile(archive_path) as archive:
            members = archive.infolist()
            if len(members) != len(names) or {member.filename for member in members} != set(names):
                raise ValueError("ZIP içeriği model dosyalarıyla uyuşmuyor")
            for member in members:
                if cancelled():
                    return False
                if member.is_dir() or (member.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError("ZIP içinde geçersiz dosya")
                extracted = os.path.join(staging, member.filename)
                with archive.open(member) as source, open(extracted, "wb") as output:
                    while True:
                        if cancelled():
                            return False
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        output.write(chunk)
                if not _sha256_matches(extracted, hashes[member.filename]):
                    raise ValueError(f"{member.filename}: SHA256 doğrulaması başarısız")
        if cancelled():
            return False
        # No installed file changes until the entire archive has been verified.
        for name in names:
            os.replace(os.path.join(staging, name), os.path.join(target_dir, name))
    return True

# ---------------------------------------------------------------------------
# Model check
# ---------------------------------------------------------------------------
def check_models(service_id: str) -> list[dict]:
    with config.installation_operation():
        return _check_models(service_id)


def _check_models(service_id: str) -> list[dict]:
    manifest, m_path = config.find_manifest(service_id)
    if not manifest:
        return []

    models_dir = config.models_directory(service_id, os.path.dirname(m_path), manifest)
    
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
    status_text = f"{finished_count}/{total_files}" if total_files > 1 else ""

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
    # A move holds the same lock through copy, validation and registry switch.
    # No new native download may write through the previous location meanwhile.
    with config.installation_operation():
        return _run_model_download(service_id, service_dir, model_data)


def _run_model_download(service_id: str, service_dir: str, model_data: dict):
    model_key = f"{service_id}:{model_data['id']}"
    config.DOWNLOADING_MODELS[model_key] = {"progress": "Başlatılıyor...", "total_mb": 0}
    try:
        manifest, _ = config.find_manifest(service_id)
        target_dir = os.path.join(config.models_directory(service_id, service_dir, manifest or {}), model_data.get("folder", ""))
        os.makedirs(target_dir, exist_ok=True)

        if model_data.get("archive_format") == "zip":
            if _download_zip_model(model_key, model_data, target_dir):
                print("[MODEL] ZIP modeli doğrulandı ve kuruldu.")
            else:
                print("[SYSTEM] Model indirme iptal edildi.")
            return

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
                if expected > 0 and abs(actual - expected) < 1024 and _sha256_matches(
                    dest, model_data.get("sha256", {}).get(fname)
                ):
                    print(f"[MODEL] {fname} zaten mevcut, atlanıyor.")
                    downloaded += actual
                    continue

            req = urllib.request.Request(url)
            req.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
            with urllib.request.urlopen(req) as resp, open(tmp, "wb") as out:
                while True:
                    if config.SHOULD_EXIT or config.DOWNLOADING_MODELS.get(model_key, {}).get("cancel"): break
                    chunk = resp.read(1024 * 1024)
                    if not chunk: break
                    out.write(chunk)
                    downloaded += len(chunk)
                    if total_bytes > 0:
                        pct = int(downloaded / total_bytes * 100)
                        config.DOWNLOADING_MODELS[model_key]["progress"] = f"{prefix} (%{pct})"
                    else:
                        config.DOWNLOADING_MODELS[model_key]["progress"] = f"{prefix} (İndiriliyor...)"

            if config.SHOULD_EXIT or config.DOWNLOADING_MODELS.get(model_key, {}).get("cancel"):
                if os.path.exists(tmp): os.remove(tmp)
                print(f"[SYSTEM] İndirme iptal edildi: {fname}")
                break

            actual = os.path.getsize(tmp)
            if expected > 0 and abs(actual - expected) > 1024:
                if os.path.exists(tmp): os.remove(tmp)
                raise ValueError(f"{fname}: boyut uyuşmazlığı (beklenen {expected}, alınan {actual})")

            if not _sha256_matches(tmp, model_data.get("sha256", {}).get(fname)):
                os.remove(tmp)
                raise ValueError(f"{fname}: SHA256 doğrulaması başarısız")

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
    with config.installation_operation():
        return _delete_model(service_id, model_id)


def _delete_model(service_id: str, model_id: str) -> dict:
    models_list = _check_models(service_id)
    model_entry = next((m for m in models_list if m["id"] == model_id), None)
    if not model_entry:
        return {"status": "error", "message": i18n.t("MSG_MODEL_NOT_FOUND")}
    
    path = model_entry.get("path")
    if not path or not os.path.exists(path):
        return {"status": "error", "message": i18n.t("MSG_MODEL_FILE_NOT_FOUND")}
        
    manifest, m_path = config.find_manifest(service_id)
    if not manifest:
        return {"status": "error", "message": i18n.t("MSG_MANIFEST_NOT_FOUND")}
        
    from pathlib import Path
    models_dir = Path(config.models_directory(service_id, os.path.dirname(m_path), manifest)).resolve()
    resolved_path = Path(path).resolve()
    if resolved_path == models_dir or not resolved_path.is_relative_to(models_dir):
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
