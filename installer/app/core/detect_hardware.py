"""
Orion Hardware Detection
Çalıştır: python detect_hardware.py
Çıktı: installer/.env.global
"""
import os
import platform
import subprocess


def get_nvidia_info() -> dict | None:
    import json
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=index,name,memory.total,compute_cap", "--format=csv,noheader,nounits"],
            encoding="utf-8", stdin=subprocess.DEVNULL
        )
        lines = out.strip().split("\n")
        gpus = []
        for line in lines:
            if not line.strip(): continue
            parts = line.split(", ")
            if len(parts) == 4:
                gpus.append({"id": parts[0], "name": parts[1], "vram": parts[2], "cap": parts[3]})
        
        if not gpus:
            return None

        return {
            "DETECTED_GPU_VENDOR": "nvidia",
            "DETECTED_GPU_NAME": gpus[0]["name"],
            "DETECTED_VRAM_GB": str(round(int(gpus[0]["vram"]) / 1024)),
            "DETECTED_GPU_CAP": gpus[0]["cap"],
            "DETECTED_GPU_LIST": json.dumps(gpus)
        }
    except Exception:
        return None


def get_amd_info() -> dict | None:
    try:
        out = subprocess.check_output(["wmic", "path", "win32_VideoController", "get", "name"], encoding="utf-8", stdin=subprocess.DEVNULL)
        if "Radeon" in out or "AMD" in out:
            return {
                "DETECTED_GPU_VENDOR": "amd",
                "DETECTED_GPU_NAME": "AMD GPU (WMIC)",
                "DETECTED_VRAM_GB": "8"
            }
    except Exception:
        pass
    return None


def get_cpu_info() -> str:
    try:
        # Windows için wmic kullanarak CPU ismi al
        out = subprocess.check_output(["wmic", "cpu", "get", "name"], encoding="utf-8", stdin=subprocess.DEVNULL)
        lines = [line.strip() for line in out.split("\n") if line.strip()]
        if len(lines) > 1:
            return lines[1].strip()
    except Exception:
        pass
    return platform.processor() or "Bilinmeyen İşlemci"


def detect() -> dict:
    info = get_nvidia_info() or get_amd_info() or {
        "DETECTED_GPU_VENDOR": "cpu",
        "DETECTED_GPU_NAME": "Generic CPU",
        "DETECTED_VRAM_GB": "0"
    }
    info["DETECTED_CPU"] = get_cpu_info()
    info["OS_PLATFORM"] = platform.system().lower()
    return info


if __name__ == "__main__":
    # Konsolda test için
    print(detect())
