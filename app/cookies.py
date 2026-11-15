"""Ubah cookie hasil export Chrome ke format yang diterima Playwright."""

import json
from typing import Any

DEFAULT_DOMAIN = ".elevenlabs.io"

_SAMESITE = {
    "no_restriction": "None",
    "none": "None",
    "lax": "Lax",
    "strict": "Strict",
}


class CookieFormatError(ValueError):
    pass


def _parse_header(raw: str) -> list[dict[str, Any]]:
    raw = raw.strip()
    if raw.lower().startswith("cookie:"):
        raw = raw[len("cookie:"):]
    out = []
    for part in raw.split(";"):
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        out.append({"name": name.strip(), "value": value.strip(), "domain": DEFAULT_DOMAIN})
    return out


def normalize_cookies(raw: list[dict[str, Any]] | str) -> list[dict[str, Any]]:
    if isinstance(raw, str):
        s = raw.strip()
        raw = json.loads(s) if s.startswith("[") else _parse_header(s)

    if not isinstance(raw, list) or not raw:
        raise CookieFormatError("cookies kosong atau formatnya tidak dikenali")

    out: list[dict[str, Any]] = []
    for c in raw:
        if not isinstance(c, dict) or not c.get("name") or "value" not in c:
            raise CookieFormatError(f"cookie tidak valid: {c!r}")

        item: dict[str, Any] = {
            "name": str(c["name"]),
            "value": str(c["value"]),
            "domain": c.get("domain") or DEFAULT_DOMAIN,
            "path": c.get("path") or "/",
            "httpOnly": bool(c.get("httpOnly", False)),
            "secure": bool(c.get("secure", False)),
        }

        expires = c.get("expirationDate", c.get("expires"))
        if expires is not None and not c.get("session") and float(expires) > 0:
            item["expires"] = float(expires)

        same_site = c.get("sameSite")
        mapped = _SAMESITE.get(str(same_site).lower()) if same_site else None
        if mapped:
            item["sameSite"] = mapped
            if mapped == "None":
                item["secure"] = True

        out.append(item)
    return out
