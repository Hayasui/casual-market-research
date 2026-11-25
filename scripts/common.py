"""Small file and identity helpers for explicit-input tools."""
import hashlib
import json
import re
from datetime import date
from pathlib import Path

METRIC_SOURCE = "Sensor Tower"


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    tmp.replace(path)


def digest(data):
    raw = data if isinstance(data, bytes) else json.dumps(data, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(raw).hexdigest()


def ident(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value):
        raise ValueError("ID must contain 1–80 ASCII letters, digits, _ or -")
    return value


def months(start, end):
    a, b = date.fromisoformat(start), date.fromisoformat(end)
    if a > b:
        raise ValueError("window start exceeds end")
    result = []
    year, month = a.year, a.month
    while (year, month) <= (b.year, b.month):
        result.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return result
