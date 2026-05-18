"""Provides storage utilities for use across the application."""

# Standard library imports
import json
import os
import pathlib
import tempfile
import threading
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager

# Third party imports
import boto3
import botocore.config
import smart_open
from botocore.exceptions import ClientError

_s3_client = None
_s3_client_lock = threading.Lock()


def _get_s3_client() -> boto3.client:
    """Return a shared S3 client, created once per process.

    Shared so TLS connections are established once and reused across threads.
    Pool sized to max_workers (default 15) so no thread ever waits for a slot.
    boto3 auto-discovers AWS_ENDPOINT_URL, credentials, and region from env.
    """
    global _s3_client
    if _s3_client is None:
        with _s3_client_lock:
            if _s3_client is None:
                max_workers = int(os.environ.get("MAX_WORKERS", "15"))
                cfg = botocore.config.Config(max_pool_connections=max_workers)
                _s3_client = boto3.session.Session().client("s3", config=cfg)
    return _s3_client


def _s3_tp(path: str, extra: dict | None = None) -> dict:
    """Return transport_params for smart_open; injects shared S3 client only for s3:// paths."""
    if not path.startswith("s3://"):
        return {}
    params = dict(extra or {})
    params["client"] = _get_s3_client()
    return params


def _empty_for_return_type(return_type: str) -> dict | list:
    """Return empty dict or list per return_type."""
    if return_type == "dict":
        return {}
    if return_type == "list":
        return []
    raise ValueError(f"Invalid return type: {return_type}")


def load_json(file_path: str, return_type: str = "dict") -> dict | list:
    """Load a JSON file from a local path or S3 URL.

    Supports any path scheme understood by ``smart_open`` (local, ``s3://``).
    Missing files — locally absent or absent on S3 — are treated as empty and
    return the appropriate empty container instead of raising.

    Args:
        file_path: Local filesystem path or ``s3://bucket/key`` URL of the JSON file.
        return_type: Expected top-level type of the JSON document — ``"dict"`` or
            ``"list"``. Controls the empty value returned when the file is absent.
            Raises ``ValueError`` for any other value.

    Returns:
        Parsed JSON content as a ``dict`` or ``list``. Returns an empty ``dict`` or
        ``list`` (per ``return_type``) when the file does not exist.

    Raises:
        ValueError: If ``return_type`` is not ``"dict"`` or ``"list"``.
        botocore.exceptions.ClientError: If an S3 error other than ``NoSuchKey`` occurs.
        json.JSONDecodeError: If the file exists but contains invalid JSON.
    """
    try:
        with smart_open.open(file_path, transport_params=_s3_tp(file_path)) as f:
            return json.load(f)

    except (FileNotFoundError, OSError):
        return _empty_for_return_type(return_type)

    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "NoSuchKey":
            return _empty_for_return_type(return_type)
        raise


def save_json(file_path: str, data: dict | list, mode: str = "w") -> None:
    """Save a JSON file to the given path.

    Efficient writing: https://github.com/piskvorky/smart_open/blob/develop/howto.md#how-to-write-to-s3-efficiently

    Can write in append mode for local files, S3 files are always overwritten.

    Args:
        file_path: The path to the JSON file.
        data: The JSON data to save to the file as a dictionary or list.
        mode: File open mode ("w" to overwrite, "a" to append). S3 paths always overwrite.
    """
    try:
        if file_path.startswith("s3://"):
            with tempfile.NamedTemporaryFile() as tmp:
                with smart_open.open(
                    file_path, "w", transport_params=_s3_tp(file_path, {"writebuffer": tmp})
                ) as fout:
                    json.dump(data, fout, indent=2)
        else:
            with smart_open.open(file_path, mode) as fout:
                json.dump(data, fout, indent=2)
    except ValueError as e:
        raise ValueError(f"Failed to save JSON to {file_path!r}: {e}") from e


def key_exists(file_path: str) -> bool:
    """Return True if the file at the given path exists.

    Supports local filesystem paths and ``s3://`` URLs. Uses HeadObject for S3
    to avoid opening a read stream.

    Args:
        file_path: Local path or ``s3://`` URL to check.

    Returns:
        True if the file exists, False if it does not.

    Raises:
        botocore.exceptions.ClientError: If an S3 error other than ``NoSuchKey``/404 occurs.
    """
    if not file_path.startswith("s3://"):
        return pathlib.Path(file_path).exists()
    without_scheme = file_path[5:]
    bucket, _, key = without_scheme.partition("/")
    try:
        _get_s3_client().head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
            return False
        raise


def load_content(file_path: str) -> str:
    """Load text content from a local path or S3 URL.

    Missing files return an empty string instead of raising.

    Args:
        file_path: Local filesystem path or ``s3://`` URL of the text file.

    Returns:
        File contents as a string, or ``""`` when the file does not exist.

    Raises:
        botocore.exceptions.ClientError: If an S3 error other than ``NoSuchKey`` occurs.
    """
    try:
        with smart_open.open(file_path, transport_params=_s3_tp(file_path)) as f:
            return f.read()
    except (FileNotFoundError, OSError):
        return ""
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "NoSuchKey":
            return ""
        raise


def save_content(file_path: str, content: str) -> None:
    """Save text content to a local path or S3 URL.

    Args:
        file_path: Local filesystem path or ``s3://`` URL to write to.
        content: Text content to write.
    """
    try:
        if file_path.startswith("s3://"):
            with tempfile.NamedTemporaryFile() as tmp:
                with smart_open.open(
                    file_path, "w", transport_params=_s3_tp(file_path, {"writebuffer": tmp})
                ) as fout:
                    fout.write(content)
        else:
            with smart_open.open(file_path, "w") as fout:
                fout.write(content)
    except ValueError as e:
        raise ValueError(f"Failed to save content to {file_path!r}: {e}") from e


def stream_to_s3(fileobj: object, s3_url: str) -> None:
    """Stream a file-like object directly to S3 using multipart upload.

    Args:
        fileobj: Readable file-like object to upload.
        s3_url: Destination ``s3://bucket/key`` URL.
    """
    without_scheme = s3_url[5:]
    bucket, _, key = without_scheme.partition("/")
    _get_s3_client().upload_fileobj(fileobj, bucket, key)


@contextmanager
def open_zip(file_path: str, headers: dict | None = None) -> Iterator[zipfile.ZipFile]:
    """Open a zip file from a local path, S3, or HTTPS URL.

    Supports any path scheme handled by smart_open (local, s3://, https://).
    HTTPS requires the server to support range requests (Accept-Ranges: bytes).

    Args:
        file_path: Path to the ZIP file — local filesystem path, ``s3://`` URL, or
            ``https://`` URL. HTTPS requires the server to support range requests
            (``Accept-Ranges: bytes``).
        headers: Optional HTTP headers passed as transport params (e.g. ``User-Agent``
            for SEC EDGAR). Ignored for local and S3 paths.

    Yields:
        An open ``zipfile.ZipFile`` object. The underlying stream is closed
        automatically when the context manager exits.

    Raises:
        zipfile.BadZipFile: If the file is not a valid ZIP archive.
        OSError: If the file cannot be opened or read.
    """
    if file_path.startswith("s3://"):
        tp = _s3_tp(file_path)
    elif headers:
        tp = {"headers": headers}
    else:
        tp = {}
    with smart_open.open(file_path, "rb", transport_params=tp) as f:
        with zipfile.ZipFile(f) as zf:
            yield zf
