from fastapi import APIRouter, BackgroundTasks, HTTPException
import fastapi
import os

from . import config
from .core import models
from .core import i18n
from . import services

router = APIRouter()

# ---------------------------------------------------------------------------
# Hardware
# ---------------------------------------------------------------------------
@router.get("/api/hardware")
def get_hardware():
    hw = config.get_env_global()
    hw["install_mode"] = os.environ.get("ORION_INSTALL_MODE", "docker")
    return hw

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
        
    if compose_file == "local":
        background_tasks.add_task(services.run_local_installation, service_id, service_dir)
        return {"status": "success", "message": i18n.t("MSG_INSTALL_STARTED", "")}

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
    return {"status": "success", "message": i18n.t("MSG_INSTALL_STARTED", kill_msg)}


@router.post("/api/services/{service_id}/stop")
def stop_service(service_id: str):
    if not services.stop_service(service_id):
        raise HTTPException(status_code=404, detail=i18n.t("MSG_SERVICE_NOT_FOUND"))
    return {"status": "success", "message": i18n.t("MSG_SERVICE_STOPPED")}

# ---------------------------------------------------------------------------
# Service Remove
# ---------------------------------------------------------------------------
@router.post("/api/services/{service_id}/remove")
def remove_service(service_id: str, keep_data: bool = False):
    if not services.remove_service(service_id, keep_data=keep_data):
        raise HTTPException(status_code=404, detail=i18n.t("MSG_SERVICE_NOT_FOUND"))
    return {"status": "success", "message": i18n.t("MSG_SERVICE_DELETED")}

@router.post("/api/services/{service_id}/wipe")
def wipe_service_data(service_id: str):
    if not services.wipe_service_data(service_id):
        raise HTTPException(status_code=404, detail=i18n.t("MSG_SERVICE_NOT_FOUND"))
    # Return success, using a generic message if MSG_DATA_WIPED_SUCCESS isn't in locales
    return {"status": "success", "message": "Service data wiped successfully"}

@router.post("/api/services/{service_id}/remove-image")
def remove_image(service_id: str):
    if not services.remove_image(service_id):
        raise HTTPException(status_code=404, detail=i18n.t("MSG_IMAGE_NOT_FOUND"))
    return {"status": "success", "message": i18n.t("MSG_IMAGE_DELETED_SUCCESS")}

@router.post("/api/services/{service_id}/autostart")
def toggle_autostart(service_id: str):
    res = services.toggle_autostart(service_id)
    if res["status"] == "error":
        raise HTTPException(status_code=500, detail=res["message"])
    return res

# ---------------------------------------------------------------------------
# System Run
# ---------------------------------------------------------------------------
from pydantic import BaseModel
class LangUpdate(BaseModel):
    lang: str

@router.post("/api/system/lang")
def update_lang(data: LangUpdate):
    try:
        # Sadece .env.global.local dosyasına yazıyoruz

        # CLI_LANG ayarını .env.global.local dosyasına kaydet
        local_path = os.path.join(config.SERVICES_DIR, ".env.global.local")
        lines = []
        if os.path.exists(local_path):
            with open(local_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        else:
            lines = ["# Local overrides and auto-generated keys for Orion Services\n"]
            
        found = False
        for i, line in enumerate(lines):
            if line.startswith("CLI_LANG="):
                lines[i] = f"CLI_LANG={data.lang}\n"
                found = True
                break
        
        if not found:
            lines.append(f"\nCLI_LANG={data.lang}\n")
            
        with open(local_path, "w", encoding="utf-8") as f:
            f.writelines(lines)
            
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/api/system/start")
def start_system():
    res = services.start_active_services()
    if res["status"] == "error":
        raise HTTPException(status_code=500, detail=res["message"])
    
    return {"status": "success", "message": i18n.t("MSG_SYSTEM_STARTING")}

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
        return {"status": "error", "message": i18n.t("MSG_SERVICE_NOT_FOUND")}
    model = next((m for m in manifest.get("models_catalog", []) if m["id"] == model_id), None)
    if not model:
        return {"status": "error", "message": i18n.t("MSG_MODEL_NOT_FOUND")}

    model_key = f"{service_id}:{model_id}"
    if model_key in config.DOWNLOADING_MODELS:
        return {"status": "error", "message": i18n.t("MSG_MODEL_ALREADY_DOWNLOADING")}

    service_dir = m_path.replace("manifest.json", "")
    background_tasks.add_task(models.run_model_download, service_id, service_dir, model)
    return {"status": "success", "message": i18n.t("MSG_DOWNLOAD_STARTED")}


@router.post("/api/services/{service_id}/models/cancel_download")
def cancel_download(service_id: str, model_id: str):
    model_key = f"{service_id}:{model_id}"
    if model_key in config.DOWNLOADING_MODELS:
        config.DOWNLOADING_MODELS[model_key]["cancel"] = True
        return {"status": "success", "message": "İptal ediliyor..."}
    return {"status": "error", "message": i18n.t("MSG_MODEL_NOT_FOUND")}


@router.post("/api/services/{service_id}/models/delete")
def delete_model(service_id: str, model_id: str):
    return models.delete_model(service_id, model_id)
