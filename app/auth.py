import secrets

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

from .config import settings

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(key: str | None = Security(_api_key_header)) -> str:
    if key and any(secrets.compare_digest(key, k) for k in settings.api_key_set):
        return key
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or missing X-API-Key")
