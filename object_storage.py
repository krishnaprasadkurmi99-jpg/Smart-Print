import os
import re
from pathlib import PurePosixPath
from typing import NoReturn
from urllib.parse import urlparse

import httpx
from storage3.exceptions import StorageApiError
from storage3.utils import StorageException
from supabase import create_client
from supabase.lib.client_options import ClientOptions


class ObjectStorageError(Exception):
    pass


class ObjectNotFoundError(ObjectStorageError):
    pass


class SupabaseObjectStorage:
    def __init__(
        self,
        *,
        url: str,
        service_role_key: str,
        bucket: str,
        client=None,
        timeout_seconds: int = 30,
        download_url_ttl_seconds: int = 300,
    ):
        parsed_url = urlparse(url)
        if (
            parsed_url.scheme != "https"
            or not parsed_url.netloc
            or parsed_url.username
            or parsed_url.password
            or parsed_url.query
            or parsed_url.fragment
        ):
            raise RuntimeError("SUPABASE_URL must be an HTTPS URL.")
        if not service_role_key:
            raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY must be configured.")
        if not bucket or not re.fullmatch(r"[A-Za-z0-9._-]+", bucket):
            raise RuntimeError("SUPABASE_BUCKET must be a valid bucket name.")
        if not 1 <= timeout_seconds <= 300:
            raise RuntimeError("SMARTPRINT_STORAGE_TIMEOUT_SECONDS must be between 1 and 300.")
        if not 60 <= download_url_ttl_seconds <= 3600:
            raise RuntimeError(
                "SMARTPRINT_STORAGE_DOWNLOAD_URL_TTL_SECONDS must be between 60 and 3600."
            )

        self.bucket_name = bucket
        self.timeout_seconds = timeout_seconds
        self.download_url_ttl_seconds = download_url_ttl_seconds
        self.client = client or create_client(
            url,
            service_role_key,
            options=ClientOptions(storage_client_timeout=timeout_seconds),
        )
        self.bucket = self.client.storage.from_(bucket)

    @classmethod
    def from_environment(cls):
        timeout = _positive_int_setting("SMARTPRINT_STORAGE_TIMEOUT_SECONDS", 30)
        download_url_ttl = _positive_int_setting(
            "SMARTPRINT_STORAGE_DOWNLOAD_URL_TTL_SECONDS", 300
        )
        return cls(
            url=os.getenv("SUPABASE_URL", "").strip(),
            service_role_key=os.getenv("SUPABASE_SERVICE_ROLE_KEY", ""),
            bucket=os.getenv("SUPABASE_BUCKET", "smartprint-backend").strip(),
            timeout_seconds=timeout,
            download_url_ttl_seconds=download_url_ttl,
        )

    @staticmethod
    def validate_key(key: str) -> str:
        path = PurePosixPath(key)
        if (
            not key
            or key.startswith("/")
            or "\\" in key
            or any(part in {"", ".", ".."} for part in key.split("/"))
            or path.is_absolute()
        ):
            raise ValueError("Invalid object key.")
        return key

    @staticmethod
    def _raise_storage_error(error: Exception) -> NoReturn:
        if isinstance(error, StorageApiError) and (
            error.status == 404 or error.code in {"NotFound", "NoSuchKey"}
        ):
            raise ObjectNotFoundError("The storage object was not found.") from None
        if isinstance(error, httpx.HTTPStatusError) and error.response.status_code == 404:
            raise ObjectNotFoundError("The storage object was not found.") from None
        if isinstance(error, (StorageApiError, StorageException, httpx.HTTPError, OSError)):
            raise ObjectStorageError("Supabase Storage request failed.") from None
        raise error

    def presign_upload(self, key: str, content_type: str, content_length: int) -> str:
        self.validate_key(key)
        if content_type != "application/pdf":
            raise ValueError("Only PDF uploads are supported.")
        if content_length < 1:
            raise ValueError("Upload size must be positive.")
        try:
            result = self.bucket.create_signed_upload_url(key)
        except (StorageApiError, StorageException, httpx.HTTPError) as error:
            self._raise_storage_error(error)
        signed_url = result.get("signed_url") or result.get("signedUrl")
        if not isinstance(signed_url, str) or not signed_url.startswith("https://"):
            raise ObjectStorageError("Supabase did not return a valid signed upload URL.")
        return signed_url

    def head(self, key: str) -> dict:
        try:
            result = self.bucket.info(self.validate_key(key))
        except (StorageApiError, StorageException, httpx.HTTPError) as error:
            self._raise_storage_error(error)
        metadata = result.get("metadata") if isinstance(result, dict) else None
        size = None
        if isinstance(metadata, dict):
            size = metadata.get("size", metadata.get("contentLength"))
        if size is None and isinstance(result, dict):
            size = result.get("size", result.get("contentLength"))
        try:
            content_length = int(size)
        except (TypeError, ValueError):
            raise ObjectStorageError("Supabase returned invalid object metadata.") from None
        if content_length < 0:
            raise ObjectStorageError("Supabase returned invalid object metadata.")
        return {"ContentLength": content_length}

    def exists(self, key: str) -> bool:
        try:
            return self.bucket.exists(self.validate_key(key))
        except (StorageApiError, StorageException, httpx.HTTPError) as error:
            try:
                self._raise_storage_error(error)
            except ObjectNotFoundError:
                return False

    def download_file(self, key: str, destination: str) -> None:
        try:
            result = self.bucket.create_signed_url(
                self.validate_key(key), self.download_url_ttl_seconds
            )
            signed_url = result.get("signedURL") or result.get("signedUrl")
            if not isinstance(signed_url, str) or not signed_url.startswith("https://"):
                raise ObjectStorageError("Supabase did not return a valid signed download URL.")
            timeout = httpx.Timeout(self.timeout_seconds, connect=min(10, self.timeout_seconds))
            with httpx.Client(timeout=timeout, follow_redirects=False) as client:
                with client.stream("GET", signed_url) as response:
                    response.raise_for_status()
                    with open(destination, "wb") as target:
                        for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                            target.write(chunk)
        except (StorageApiError, StorageException, httpx.HTTPError, OSError) as error:
            self._raise_storage_error(error)

    def upload_file(self, source: str, key: str, content_type: str) -> None:
        try:
            self.bucket.upload(
                self.validate_key(key),
                source,
                file_options={"content-type": content_type, "upsert": "false"},
            )
        except (StorageApiError, StorageException, httpx.HTTPError, OSError) as error:
            self._raise_storage_error(error)

    def put_bytes(self, data: bytes, key: str, content_type: str) -> None:
        try:
            self.bucket.upload(
                self.validate_key(key),
                data,
                file_options={"content-type": content_type, "upsert": "false"},
            )
        except (StorageApiError, StorageException, httpx.HTTPError) as error:
            self._raise_storage_error(error)

    def copy(self, source_key: str, destination_key: str) -> None:
        try:
            self.bucket.copy(
                self.validate_key(source_key),
                self.validate_key(destination_key),
            )
        except (StorageApiError, StorageException, httpx.HTTPError) as error:
            self._raise_storage_error(error)

    def delete(self, key: str) -> None:
        try:
            self.bucket.remove([self.validate_key(key)])
        except (StorageApiError, StorageException, httpx.HTTPError) as error:
            self._raise_storage_error(error)

    def presign_download(self, key: str, filename: str, content_type: str) -> str:
        safe_filename = (
            PurePosixPath(filename.replace("\\", "/"))
            .name.replace('"', "")
            .replace("\r", "")
            .replace("\n", "")
        )
        try:
            result = self.bucket.create_signed_url(
                self.validate_key(key),
                self.download_url_ttl_seconds,
                options={"download": safe_filename},
            )
        except (StorageApiError, StorageException, httpx.HTTPError) as error:
            self._raise_storage_error(error)
        signed_url = result.get("signedURL") or result.get("signedUrl")
        if not isinstance(signed_url, str) or not signed_url.startswith("https://"):
            raise ObjectStorageError("Supabase did not return a valid signed download URL.")
        return signed_url


def _positive_int_setting(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as error:
        raise RuntimeError(f"{name} must be a positive integer.") from error
    if value < 1:
        raise RuntimeError(f"{name} must be a positive integer.")
    return value
