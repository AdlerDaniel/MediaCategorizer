import json
import os
import tempfile
from pathlib import Path

from PySide6.QtGui import QKeySequence
from .constants import APP_FOLDER, ACTION_LABELS, DEFAULT_CATEGORIES, DEFAULT_TEMPLATE

def app_config_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:
        base = Path.home() / ".config"
    folder = base / APP_FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def settings_path() -> Path:
    return app_config_dir() / "settings_v4.json"


def v3_settings_path() -> Path:
    return app_config_dir() / "settings_v3.json"


def v2_settings_path() -> Path:
    return app_config_dir() / "settings_v2.json"


def log_path() -> Path:
    return app_config_dir() / "processing_log_v4.jsonl"


def normalize_categories(data):
    result = []
    if isinstance(data, list):
        for index, item in enumerate(data):
            if isinstance(item, str):
                name = item.strip()
                shortcut = str(index + 1) if index < 9 else ""
                action = "rename"
                destination = ""
            elif isinstance(item, dict):
                name = str(item.get("name", "")).strip()
                shortcut = str(item.get("shortcut", "")).strip()
                action = str(item.get("action", "rename")).strip()
                destination = str(item.get("destination", "")).strip()
            else:
                continue

            if not name:
                continue
            if action not in ACTION_LABELS:
                action = "rename"
            shortcut = QKeySequence(shortcut).toString(QKeySequence.PortableText)
            result.append({
                "name": name,
                "shortcut": shortcut,
                "action": action,
                "destination": destination,
            })
    return result or [dict(x) for x in DEFAULT_CATEGORIES]


def load_settings():
    defaults = {
        "categories": [dict(x) for x in DEFAULT_CATEGORIES],
        "last_folder": "",
        "multi_tag_mode": False,
        "loop_video": True,
        "muted": False,
        "playback_rate": 1.0,
        "rename_template": DEFAULT_TEMPLATE,
        "window_maximized": True,
    }

    path = settings_path()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                defaults.update(data)
                defaults["categories"] = normalize_categories(defaults.get("categories"))
                return defaults
        except Exception:
            pass

    # Migrate useful settings from V3 automatically.
    old3 = v3_settings_path()
    if old3.exists():
        try:
            data = json.loads(old3.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                defaults.update(data)
                defaults["categories"] = normalize_categories(defaults.get("categories"))
                return defaults
        except Exception:
            pass

    # Migrate useful settings from V2 automatically.
    old = v2_settings_path()
    if old.exists():
        try:
            data = json.loads(old.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for key in ("last_folder", "multi_tag_mode", "loop_video", "muted"):
                    if key in data:
                        defaults[key] = data[key]
                defaults["categories"] = normalize_categories(data.get("categories"))
        except Exception:
            pass

    return defaults


def save_settings(data):
    destination = settings_path()
    fd, name = tempfile.mkstemp(prefix="settings-", suffix=".tmp", dir=destination.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
        os.replace(name, destination)
    finally:
        Path(name).unlink(missing_ok=True)
