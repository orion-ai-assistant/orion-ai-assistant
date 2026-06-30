import os

SERVICE_MOUNT_PREFIXES = {"embedding-infinity": "/app/.cache/"}

def _resolve_model_path(service_id: str, full_dir: str, selected: str, valid_exts: tuple) -> str:
    prefix = SERVICE_MOUNT_PREFIXES.get(service_id)

    # Infinity Otomatik Model Tespiti
    if service_id == "embedding-infinity" and (not selected or selected == prefix):
        if os.path.isdir(full_dir):
            for d in sorted(os.listdir(full_dir)):
                cand = os.path.join(full_dir, d)
                if os.path.isdir(cand) and d.lower() != "huggingface":
                    if os.path.exists(os.path.join(cand, "config.json")) and os.path.exists(os.path.join(cand, "model.safetensors")):
                        return f"{prefix.rstrip('/')}/{d}" if prefix else d

    if not selected: 
        return ""
        
    full_path = os.path.join(full_dir, selected)
    
    # Path yoksa, patlamaması için direkt selected dön (FileNotError fix)
    if not os.path.exists(full_path):
        return selected

    if os.path.isdir(full_path):
        # Klasör seçildiğinde ana modeli bul (mmproj OLMAYAN ilk .gguf)
        match = next(
            (n for n in sorted(os.listdir(full_path)) 
             if n.lower().endswith(valid_exts) and "mmproj" not in n.lower() and "vision" not in n.lower()), 
            None
        )
        # Eğer yukarıdaki filtreyle bir şey bulunamazsa (belki sadece mmproj vardır), herhangi bir geçerli dosyayı al
        if not match:
            match = next((n for n in sorted(os.listdir(full_path)) if n.lower().endswith(valid_exts)), None)
            
        return os.path.join(selected, match).replace("\\", "/") if match else selected

    return selected
