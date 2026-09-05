from datetime import datetime
from pathlib import Path
from .constants import DEFAULT_TEMPLATE

def safe_filename(name: str) -> str:
    forbidden = '<>:"/\\|?*'
    cleaned = "".join("_" if ch in forbidden else ch for ch in name)
    cleaned = cleaned.strip().rstrip(". ")
    return cleaned or "media"


def unique_destination(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    number = 1
    while True:
        candidate = path.with_name(f"{stem} ({number}){suffix}")
        if not candidate.exists():
            return candidate
        number += 1


def sanitize_category(category):
    return safe_filename(str(category)).strip("_")


def leading_category_prefixes(path: Path, categories):
    remainder = path.stem
    found = []
    category_prefixes = []
    for item in categories:
        clean = sanitize_category(item["name"])
        if clean:
            category_prefixes.append((clean, clean.casefold()))
    category_prefixes.sort(key=lambda pair: len(pair[0]), reverse=True)

    while remainder:
        matched = False
        lower = remainder.casefold()
        for original, folded in category_prefixes:
            if lower == folded:
                found.append(folded)
                remainder = ""
                matched = True
                break
            prefix = folded + "_"
            if lower.startswith(prefix):
                found.append(folded)
                remainder = remainder[len(original) + 1:]
                matched = True
                break
        if not matched:
            break
    return set(found)

def filter_duplicate_tags(path: Path, tags, categories):
    existing = leading_category_prefixes(path, categories)
    result = []
    skipped = []
    for tag in tags:
        clean = sanitize_category(tag)
        if not clean:
            continue
        if clean.casefold() in existing:
            skipped.append(clean)
        elif clean.casefold() not in {x.casefold() for x in result}:
            result.append(clean)
    return result, skipped

def render_rename(path: Path, tags, index, template=DEFAULT_TEMPLATE):
    tags_clean = [sanitize_category(x) for x in tags if sanitize_category(x)]
    values = {
        "tags": "_".join(tags_clean),
        "category": tags_clean[0] if tags_clean else "",
        "original": path.name,
        "stem": path.stem,
        "ext": path.suffix,
        "index": f"{index + 1:04d}",
        "date": datetime.now().strftime("%Y-%m-%d"),
        "time": datetime.now().strftime("%H-%M-%S"),
    }
    template = str(template or DEFAULT_TEMPLATE)
    try:
        candidate = template.format(**values)
    except Exception:
        candidate = DEFAULT_TEMPLATE.format(**values)
    candidate = safe_filename(candidate)
    if path.suffix and not candidate.casefold().endswith(path.suffix.casefold()):
        candidate += path.suffix
    return candidate
