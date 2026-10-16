from fastapi import APIRouter, BackgroundTasks, HTTPException
import fastapi
import os
from pydantic import BaseModel, StrictBool

from . import config
from .core import models
from .core import i18n
from . import services

router = APIRouter()


class RouterFolderSelection(BaseModel):
    root: str
    distribution: str = 'legacy-source'


class ContentMove(BaseModel):
    name: str
    destination: str
    expected_revision: str


class ContentRecovery(BaseModel):
    journal: str


class LauncherPreferences(BaseModel):
    start_with_windows: StrictBool
    show_client: StrictBool


@router.get('/api/installation/launcher')
def launcher_preferences():
    try:
        record = config.installation_registry().read()
        return {'registered': record is not None, 'windows': os.name == 'nt',
                'preferences': record['preferences'] if record else None}
    except (OSError, ValueError, RuntimeError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post('/api/installation/launcher')
def set_launcher_preferences(request: LauncherPreferences):
    from services.shared.launcher import preferences
    try:
        record = preferences(config.installation_registry(), start_with_windows=request.start_with_windows,
                             show_client=request.show_client)
        return {'preferences': record['preferences']}
    except (OSError, ValueError, RuntimeError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get('/api/installation/locations')
def installation_locations():
    from services.shared.content_migration import registry_revision
    from services.shared.installation_paths import resolve_location
    try:
        record = config.installation_registry().read()
        if record is None:
            return {'status': 'unregistered', 'revision': None, 'locations': {}}
        locations = {}
        for name in ('models', 'downloads'):
            path = resolve_location(record, name)
            locations[name] = {'path': str(path), 'available': path.is_dir()}
        return {'status': 'registered', 'revision': registry_revision(record), 'locations': locations}
    except (OSError, ValueError, RuntimeError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post('/api/installation/locations/move')
def move_installation_content(request: ContentMove):
    from services.shared.migration_host import move_content
    try:
        return move_content(config.installation_registry(), request.name, request.destination,
                            expected_revision=request.expected_revision)
    except (OSError, ValueError, RuntimeError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post('/api/installation/locations/recover')
def recover_installation_content(request: ContentRecovery):
    from services.shared.migration_host import recover_content
    try:
        return recover_content(config.installation_registry(), request.journal)
    except (OSError, ValueError, RuntimeError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get('/api/installation')
def installation_inventory():
    from services.shared.installation_registry import Registry, RegistryError
    try:
        record = config.installation_registry().read()
        return {'status': 'registered' if record is not None else 'unregistered', 'record': record}
    except RegistryError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post('/api/installation/router/inspect')
def inspect_router_selection(selection: RouterFolderSelection):
    from dataclasses import asdict
    from services.shared.router_installation import inspect_router_folder
    try:
        return asdict(inspect_router_folder(selection.root, distribution=selection.distribution))
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.post('/api/installation/router/register')
def register_router_selection(selection: RouterFolderSelection):
    from services.shared.installation_registry import Registry
    from services.shared.installation_control import register_router
    try:
        return register_router(config.installation_registry(), selection.root, distribution=selection.distribution)
    except (OSError, ValueError, RuntimeError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

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
    extra_params: str = "{}", auto_start: bool = False
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
        was_running = False
        try:
            for s in services.get_services():
                if s.get("id") == service_id:
                    was_running = s.get("is_running", False)
                    break
        except Exception:
            pass
            
        if was_running:
            services.stop_service(service_id)
                
        background_tasks.add_task(services.run_local_installation, service_id, service_dir, build_env, env_file_keys)
        
        msg = i18n.t("MSG_INSTALL_STARTED", "")
        if was_running:
            # If it was running, notify the user that we stopped it
            msg += " (Çalışan servis durduruldu)"
            
        return {"status": "success", "message": msg}

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
        auto_start,
    )
    return {"status": "success", "message": i18n.t("MSG_INSTALL_STARTED", kill_msg)}


@router.post("/api/services/{service_id}/stop")
def stop_service(service_id: str):
    try:
        stopped = services.stop_service(service_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not stopped:
        raise HTTPException(status_code=404, detail=i18n.t("MSG_SERVICE_NOT_FOUND"))
    return {"status": "success", "message": i18n.t("MSG_SERVICE_STOPPED")}

@router.post("/api/services/{service_id}/start-local")
def start_local_service(service_id: str):
    ok = services.start_local_service(service_id)
    if not ok:
        raise HTTPException(status_code=500, detail="Servis başlatılamadı. Kurulum tamamlandı mı?")
    return {"status": "success", "message": "Servis başlatılıyor..."}

# ---------------------------------------------------------------------------
# Service Remove
# ---------------------------------------------------------------------------
@router.post("/api/services/{service_id}/remove")
def remove_service(service_id: str, hardware: str = None, keep_data: bool = False):
    if not services.remove_service(service_id, hardware, keep_data=keep_data):
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
class LangUpdate(BaseModel):
    lang: str

@router.post("/api/system/lang")
def update_lang(data: LangUpdate):
    try:
        # Sadece .env.global.local dosyasına yazıyoruz

        # CLI_LANG ayarını .env.global.local dosyasına kaydet
        local_path = os.path.join(config.SERVICES_DIR, ".env.global.local")
        example_path = os.path.join(config.SERVICES_DIR, ".env.global.local.example")
        
        if not os.path.exists(local_path) and os.path.exists(example_path):
            import shutil
            try:
                shutil.copyfile(example_path, local_path)
            except OSError:
                pass

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
