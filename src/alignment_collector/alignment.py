from __future__ import annotations

import logging
import ssl
from collections.abc import Mapping
from pathlib import Path

import httpx
import truststore

from .models import SourceDocument, UploadResult

LOGGER = logging.getLogger(__name__)


class AlignmentApiError(RuntimeError):
    pass


class RetryableUploadError(AlignmentApiError):
    pass


class PermanentUploadError(AlignmentApiError):
    pass


def _mapping(value: object) -> Mapping[str, object] | None:
    return value if isinstance(value, Mapping) else None


def _error_message(response: httpx.Response) -> str:
    try:
        body: object = response.json()
    except ValueError:
        return f"Alignment API HTTP {response.status_code}"
    mapping = _mapping(body)
    message = mapping.get("message") if mapping else None
    if isinstance(message, str):
        return message
    if isinstance(message, list):
        messages = [item for item in message if isinstance(item, str)]
        if messages:
            return "; ".join(messages)
    return f"Alignment API HTTP {response.status_code}"


class AlignmentClient:
    def __init__(
        self,
        base_url: str,
        email: str,
        password: str,
        timeout_seconds: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._email = email
        self._password = password
        self._token: str | None = None
        self._owns_client = client is None
        if client is None:
            ssl_context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            self._client = httpx.AsyncClient(timeout=timeout_seconds, verify=ssl_context)
        else:
            self._client = client

    async def __aenter__(self) -> AlignmentClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _login(self) -> None:
        response = await self._client.post(
            f"{self._base_url}/v1/auth/login",
            json={"email": self._email, "password": self._password},
        )
        if response.status_code >= 500 or response.status_code == 429:
            raise RetryableUploadError(_error_message(response))
        if response.status_code >= 400:
            raise PermanentUploadError(_error_message(response))
        body = _mapping(response.json())
        data = _mapping(body.get("data")) if body else None
        token = data.get("accessToken") if data else None
        if not isinstance(token, str) or not token:
            raise PermanentUploadError("Response login tidak memiliki accessToken")
        self._token = token

    async def upload(self, document: SourceDocument, pdf_path: Path, filename: str) -> UploadResult:
        if self._token is None:
            await self._login()
        response = await self._upload_once(document, pdf_path, filename)
        if response.status_code == 401:
            self._token = None
            await self._login()
            response = await self._upload_once(document, pdf_path, filename)
        if response.status_code == 400 and document.law_year is not None:
            LOGGER.warning("Alignment API menolak metadata lawYear multipart; retry tanpa lawYear")
            response = await self._upload_once(
                document,
                pdf_path,
                filename,
                include_law_year=False,
            )
        if response.status_code == 409:
            return UploadResult(document_id=None, duplicate=True)
        if response.status_code == 429 or response.status_code >= 500:
            raise RetryableUploadError(_error_message(response))
        if response.status_code >= 400:
            raise PermanentUploadError(_error_message(response))

        body = _mapping(response.json())
        data = _mapping(body.get("data")) if body else None
        document_id = data.get("documentId") if data else None
        if not isinstance(document_id, str) or not document_id:
            raise PermanentUploadError("Response upload tidak memiliki documentId")
        return UploadResult(document_id=document_id, duplicate=False)

    async def _upload_once(
        self,
        document: SourceDocument,
        pdf_path: Path,
        filename: str,
        *,
        include_law_year: bool = True,
    ) -> httpx.Response:
        if self._token is None:
            raise RuntimeError("Alignment client belum login")
        form = {
            "sourceName": "JDIHN",
            "title": document.title,
            "documentKind": "NATIONAL_REGULATION",
        }
        if document.law_number:
            form["lawNumber"] = document.law_number
        if include_law_year and document.law_year is not None:
            form["lawYear"] = str(document.law_year)
        if document.law_type:
            form["lawType"] = document.law_type
        with pdf_path.open("rb") as pdf_file:
            return await self._client.post(
                f"{self._base_url}/v1/documents",
                headers={"Authorization": f"Bearer {self._token}"},
                data=form,
                files={"file": (filename, pdf_file, "application/pdf")},
            )
