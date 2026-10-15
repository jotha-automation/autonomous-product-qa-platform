import asyncio
import ssl

import pytest
from fastapi import HTTPException

from src.connectors.google_drive.errors import raise_for_drive_error


def test_raise_for_drive_error_maps_timeout():
    with pytest.raises(HTTPException) as exc_info:
        raise_for_drive_error(TimeoutError("timed out"))
    assert exc_info.value.status_code == 503
    assert "oauth2.googleapis.com" in str(exc_info.value.detail)


def test_raise_for_drive_error_maps_asyncio_timeout():
    with pytest.raises(HTTPException) as exc_info:
        raise_for_drive_error(asyncio.TimeoutError())
    assert exc_info.value.status_code == 503


def test_raise_for_drive_error_maps_ssl_error():
    with pytest.raises(HTTPException) as exc_info:
        raise_for_drive_error(ssl.SSLError("wrong version number"))
    assert exc_info.value.status_code == 503
    assert "SSL" in str(exc_info.value.detail)
