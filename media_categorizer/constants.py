from collections import OrderedDict

APP_VERSION = "6.5.1"
APP_NAME = "Media Categorizer"
APP_FOLDER = "MediaCategorizer"

IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp", ".tif", ".tiff"
}
VIDEO_EXTENSIONS = {
    ".mp4", ".mov", ".mkv", ".avi", ".wmv", ".m4v", ".webm", ".mpeg", ".mpg"
}
SUPPORTED_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS

ACTION_LABELS = OrderedDict([
    ("rename", "Переименовать"),
    ("move", "Переместить"),
    ("copy", "Скопировать"),
    ("rename_move", "Переименовать + переместить"),
    ("rename_copy", "Переименовать + скопировать"),
])
ACTIONS_WITH_DESTINATION = {"move", "copy", "rename_move", "rename_copy"}
ACTIONS_WITH_RENAME = {"rename", "rename_move", "rename_copy"}

DEFAULT_CATEGORIES = [
    {"name": "GOOD", "shortcut": "1", "action": "rename", "destination": ""},
    {"name": "BAD", "shortcut": "2", "action": "rename", "destination": ""},
    {"name": "EDIT", "shortcut": "3", "action": "rename", "destination": ""},
    {"name": "POST", "shortcut": "4", "action": "rename", "destination": ""},
]

DEFAULT_TEMPLATE = "{tags}_{original}"
TEMPLATE_PRESETS = [
    "{tags}_{original}",
    "{tags}_{stem}{ext}",
    "{date}_{tags}_{original}",
    "{tags}_{index}_{original}",
    "{category}_{stem}{ext}",
]

# Image preloading is asynchronous. The cache is capped both by count and memory.
IMAGE_PRELOAD_FORWARD = 5
IMAGE_PRELOAD_BACKWARD = 2
IMAGE_CACHE_MAX_ITEMS = 12
IMAGE_CACHE_MAX_BYTES = 512 * 1024 * 1024

# One active video decoder and one optional preloader.
VIDEO_SLOT_COUNT = 2
VIDEO_PRELOAD_COUNT = 1
VIDEO_SCAN_AHEAD = 8
