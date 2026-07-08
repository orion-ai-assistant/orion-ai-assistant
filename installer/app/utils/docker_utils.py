import os
import subprocess
import re

_IMAGE_PATTERN = re.compile(r"^\s*image:\s*(.+?)\s*$", re.IGNORECASE)

def _run_compose(files: list[str], action: str, cwd: str, env: dict, extra_args: list[str] = None) -> bool:
    success = False
    seen = set()
    for f in files:
        if not f or f in seen or not os.path.exists(os.path.join(cwd, f)):
            continue
        seen.add(f)
        cmd = ["docker-compose", "-f", f, action]
        if extra_args:
            cmd.extend(extra_args)
        res = subprocess.run(cmd, cwd=cwd, env={**os.environ, **env}, capture_output=True, stdin=subprocess.DEVNULL)
        _ = res.stdout.decode("utf-8", errors="replace")

        success = True
    return success

def _get_compose_image(compose_file: str, cwd: str) -> str:
    try:
        with open(os.path.join(cwd, compose_file), "r", encoding="utf-8") as f:
            for line in f:
                m = _IMAGE_PATTERN.match(line)
                if m:
                    return m.group(1).strip()
    except OSError:
        return ""
    return ""

def _image_exists(image_name: str) -> bool:
    if not image_name:
        return False
    try:
        res = subprocess.run(["docker", "image", "inspect", image_name], capture_output=True, stdin=subprocess.DEVNULL)
        return res.returncode == 0
    except Exception:
        return False

def get_running_containers() -> dict:
    containers = {}
    try:
        r = subprocess.run(["docker", "ps", "-a", "--format", "{{.Names}}|{{.Status}}"], capture_output=True, stdin=subprocess.DEVNULL)
        stdout = r.stdout.decode("utf-8", errors="replace")
        containers = {line.split("|")[0]: line.split("|")[1].startswith("Up") for line in stdout.splitlines() if "|" in line}
    except Exception:
        pass
    return containers

def _remove_image(image_name: str) -> bool:
    if not image_name:
        return False
    try:
        res = subprocess.run(["docker", "rmi", "-f", image_name], capture_output=True, stdin=subprocess.DEVNULL)
        return res.returncode == 0
    except Exception:
        return False
