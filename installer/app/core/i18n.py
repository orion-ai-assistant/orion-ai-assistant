import os
from .. import config

import json
from .. import config

_TRANSLATIONS = {}

def _load_translations(lang: str) -> dict:
    global _TRANSLATIONS
    lang = lang.lower()
    if lang in _TRANSLATIONS:
        return _TRANSLATIONS[lang]
    
    # Load from localize directory
    core_dir = os.path.dirname(os.path.abspath(__file__))
    app_dir = os.path.dirname(core_dir)
    json_path = os.path.join(app_dir, "localize", f"{lang}.json")
    
    if os.path.exists(json_path):
        try:
            with open(json_path, encoding="utf-8") as f:
                _TRANSLATIONS[lang] = json.load(f)
                return _TRANSLATIONS[lang]
        except Exception:
            pass
            
    # Fallback to en if not loaded and lang isn't en
    if lang != "en":
        return _load_translations("en")
        
    return {}

def t(key: str, *args) -> str:
    lang = config._load_global_env().get("CLI_LANG", "en").lower()
    translations = _load_translations(lang)
    text = translations.get(key, key)
    if args:
        try:
            text = text.format(*args)
        except:
            pass
    return text
