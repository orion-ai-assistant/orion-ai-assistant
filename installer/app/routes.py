from fastapi import APIRouter, BackgroundTasks, HTTPException
import fastapi

from . import config
from .core import models
from . import services

router = APIRouter()

# ---------------------------------------------------------------------------
# Hardware
# ---------------------------------------------------------------------------
@router.get("/api/hardware")
def get_hardware():
    return config.get_env_global()

# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------
@router.get("/api/services")
def list_services():
    try:
        return services.get_services()
    except Exception as e:
        import traceback
        return fastapi.responses.JSONResponse(status_code=500, content={"error": str(e), "trace": traceback.format_exc()})


@router.post("/api/services/{service_id}/install")
async def install_service(
    service_id: str, background_tasks: BackgroundTasks,
    hardware: str = None, env_id: str = None,
    model_file: str = None, mmproj_file: str = None,
    extra_params: str = "{}"
):
    import json
    try:
        params_dict = json.loads(extra_params)
    except:
        params_dict = {}

    error, service_dir, compose_file, build_env, env_file_keys = services.prepare_install(
        service_id, hardware, env_id, model_file, mmproj_file, params_dict
    )
    if error:
        return error
        
    port_to_check = build_env.get("APP_PORT")
    kill_msg = ""
    if port_to_check:
        from .utils import system_utils
        conflict_res = await system_utils.check_and_resolve_port_conflict(int(port_to_check))
        if conflict_res["status"] == "error":
            return conflict_res
        elif conflict_res.get("message") and "kapatildi" in conflict_res["message"].lower():
            kill_msg = f" ({conflict_res['message']})"

    background_tasks.add_task(
        services.run_installation,
        service_id,
        service_dir,
        compose_file,
        build_env,
        env_file_keys,
    )
    return {"status": "success", "message": f"Kurulum başlatıldı{kill_msg}"}


@router.post("/api/services/{service_id}/stop")
def stop_service(service_id: str):
    if not services.stop_service(service_id):
        raise HTTPException(status_code=404, detail="Servis bulunamadı")
    return {"status": "success", "message": "Servis durduruldu"}

# ---------------------------------------------------------------------------
# Service Remove
# ---------------------------------------------------------------------------
@router.post("/api/services/{service_id}/remove")
def remove_service(service_id: str):
    if not services.remove_service(service_id):
        raise HTTPException(status_code=404, detail="Servis bulunamadi")
    return {"status": "success", "message": "Servis silindi"}

@router.post("/api/services/{service_id}/remove-image")
def remove_image(service_id: str):
    if not services.remove_image(service_id):
        raise HTTPException(status_code=404, detail="Imaj bulunamadi veya silinemedi")
    return {"status": "success", "message": "Imaj basariyla silindi"}

@router.post("/api/services/{service_id}/autostart")
def toggle_autostart(service_id: str):
    res = services.toggle_autostart(service_id)
    if res["status"] == "error":
        raise HTTPException(status_code=500, detail=res["message"])
    return res

# ---------------------------------------------------------------------------
# System Run
# ---------------------------------------------------------------------------
@router.post("/api/system/start")
def start_system():
    import subprocess
    import os
    project_root = os.path.dirname(config.SERVICES_DIR)
    script_path = os.path.join(project_root, "orion.ps1")
    if not os.path.exists(script_path):
        raise HTTPException(status_code=500, detail="orion.ps1 bulunamadi")
    
    subprocess.Popen(["powershell", "-ExecutionPolicy", "Bypass", "-File", script_path, "start"], cwd=project_root)
    return {"status": "success", "message": "Sistem baslatiliyor"}

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
@router.get("/api/services/{service_id}/models/check")
def check_models(service_id: str):
    return models.check_models(service_id)


@router.post("/api/services/{service_id}/models/download")
def download_model(service_id: str, model_id: str, background_tasks: BackgroundTasks):
    manifest, m_path = config.find_manifest(service_id)
    if not manifest:
        return {"status": "error", "message": "Servis bulunamadı"}
    model = next((m for m in manifest.get("models_catalog", []) if m["id"] == model_id), None)
    if not model:
        return {"status": "error", "message": "Model bulunamadı"}

    model_key = f"{service_id}:{model_id}"
    if model_key in config.DOWNLOADING_MODELS:
        return {"status": "error", "message": "Bu model zaten indiriliyor"}

    service_dir = m_path.replace("manifest.json", "")
    background_tasks.add_task(models.run_model_download, service_id, service_dir, model)
    return {"status": "success", "message": "İndirme başlatıldı"}
