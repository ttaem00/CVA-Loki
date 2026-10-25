from __future__ import annotations

def hms_to_sec(value: str) -> int:
    text = str(value or "").strip()
    if not text:
        raise ValueError("empty timestamp")
    parts = text.split(":")
    if len(parts) == 2:
        hh = 0
        mm, ss = parts
    elif len(parts) == 3:
        hh, mm, ss = parts
    else:
        raise ValueError(f"invalid timestamp: {value}")
    return int(hh) * 3600 + int(mm) * 60 + int(ss)
