"""Pure URL boundary shared by Elio evidence validation and source acquisition."""

import ipaddress

from urllib.parse import urlparse

def validate_public_media_url(value: str) -> str:
    """Accept one public HTTP(S) URL and reject credential/local network forms."""

    raw = str(value or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("one public http(s) video URL is required")
    if parsed.username or parsed.password:
        raise ValueError("credentials in media URLs are forbidden")
    host = parsed.hostname.casefold().rstrip(".")
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        raise ValueError("local/private media hosts are forbidden")
    try:
        address = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        address = None
    if address and not address.is_global:
        raise ValueError("local/private media addresses are forbidden")
    return raw
