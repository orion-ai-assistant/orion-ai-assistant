"""
Orion TTS Service Installer Hook
================================
Executed automatically by the Orion Installer during local service installation.
Dispatches binary installations for enabled TTS engines (e.g. OmniVoice-GGUF).
"""

import os
import sys
import subprocess

def run_tts_install():
    current_dir = os.path.dirname(os.path.abspath(__file__))
    omnivoice_install = os.path.join(current_dir, "omnivoice-gguf", "install.py")

    print("[*] Orion TTS Service Installer Hook started.")

    if os.path.exists(omnivoice_install):
        print("[*] Found OmniVoice-GGUF engine. Running installer...")
        try:
            subprocess.run([sys.executable, omnivoice_install], check=True)
        except subprocess.CalledProcessError as e:
            print(f"[ERROR] OmniVoice-GGUF installation failed with code {e.returncode}")
            sys.exit(e.returncode)
    else:
        print("[*] No custom engine installer found for TTS.")

    print("[*] Orion TTS Service Installer completed successfully.")


if __name__ == "__main__":
    run_tts_install()
