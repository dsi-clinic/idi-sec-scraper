"""Provides storage utilities for use across the application."""

# Standard library imports
import gzip
import io
import json
import os
import pathlib
import shutil
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

# Files larger than this after compression use multipart upload; below it use put_object.
# 100MB is the recommended threshold for using multipart upload.
# https://docs.aws.amazon.com/AmazonS3/latest/userguide/mpuoverview.html.
_MULTIPART_THRESHOLD = 100 * 1024 * 1024


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


def _is_s3(path: str) -> bool:
    """Return True if path is an S3 URL."""
    return path.startswith("s3://")


def _parse_s3_url(file_path: str) -> tuple[str, str]:
    """Parse an ``s3://bucket/key`` URL into ``(bucket, key)``."""
    without_scheme = file_path[5:]
    bucket, _, key = without_scheme.partition("/")
    return bucket, key


def _s3_get_bytes(bucket: str, key: str) -> bytes | None:
    """Fetch an S3 object and return its bytes, decompressing if gzip-encoded.

    Returns ``None`` when the key does not exist.

    Raises:
        botocore.exceptions.ClientError: If an error other than ``NoSuchKey`` occurs.
    """
    try:
        response = _get_s3_client().get_object(Bucket=bucket, Key=key)
        body = response["Body"].read()
        if response.get("ContentEncoding") == "gzip":
            body = gzip.decompress(body)
        return body
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "NoSuchKey":
            return None
        raise


def _empty_for_return_type(return_type: str) -> dict | list:
    """Return empty dict or list per return_type."""
    if return_type == "dict":
        return {}
    if return_type == "list":
        return []
    raise ValueError(f"Invalid return type: {return_type}")


def load_json(file_path: str, return_type: str = "dict") -> dict | list:
    """Load a JSON file from a local path or S3 URL.

    Missing files return an empty container instead of raising. Compressed
    files are transparently decompressed.

    Args:
        file_path: Local filesystem path or ``s3://bucket/key`` URL.
        return_type: Expected top-level type — ``"dict"`` or ``"list"``.

    Returns:
        Parsed JSON as a ``dict`` or ``list``, or an empty container when missing.

    Raises:
        ValueError: If ``return_type`` is not ``"dict"`` or ``"list"``.
        botocore.exceptions.ClientError: If an S3 error other than ``NoSuchKey`` occurs.
        json.JSONDecodeError: If the file exists but contains invalid JSON.
    """
    body = load_content(file_path)
    return json.loads(body) if body else _empty_for_return_type(return_type)


def save_json(file_path: str, data: dict | list, compress: bool = False) -> None:
    """Save data as JSON to a local path or S3 URL.

    Args:
        file_path: Local filesystem path or ``s3://bucket/key`` URL.
        data: The JSON-serialisable dict or list to write.
        compress: If True, gzip-compress before uploading to S3.
    """
    save_content(file_path, json.dumps(data, indent=2).encode(), compress=compress)


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
    if not _is_s3(file_path):
        return pathlib.Path(file_path).exists()
    bucket, key = _parse_s3_url(file_path)
    try:
        _get_s3_client().head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
            return False
        raise


def load_content(file_path: str) -> bytes:
    """Load raw bytes from a local path or S3 URL.

    Missing files return ``b""`` instead of raising. S3 objects with
    ``ContentEncoding: gzip`` are transparently decompressed.

    Args:
        file_path: Local filesystem path or ``s3://`` URL.

    Returns:
        File contents as bytes, or ``b""`` when the file does not exist.

    Raises:
        botocore.exceptions.ClientError: If an S3 error other than ``NoSuchKey`` occurs.
    """
    if _is_s3(file_path):
        bucket, key = _parse_s3_url(file_path)
        body = _s3_get_bytes(bucket, key)
        return body if body is not None else b""
    try:
        return pathlib.Path(file_path).read_bytes()
    except FileNotFoundError:
        return b""


def save_content(file_path: str, content: bytes, compress: bool = False) -> None:
    """Save raw bytes to a local path or S3 URL.

    When ``compress=True``, S3 uploads are gzip-compressed with ``ContentEncoding:
    gzip``; files below ``_MULTIPART_THRESHOLD`` after compression use a single
    ``put_object`` call, larger files use multipart upload via ``upload_fileobj``.
    Local writes are always uncompressed raw bytes regardless of ``compress``.

    Args:
        file_path: Local filesystem path or ``s3://`` URL to write to.
        content: Raw bytes to write.
        compress: If True, gzip-compress before uploading to S3.
    """
    if _is_s3(file_path):
        bucket, key = _parse_s3_url(file_path)
        if compress:
            body = gzip.compress(content)
            extra: dict = {"ContentEncoding": "gzip"}
        else:
            body = content
            extra = {}
        if len(body) < _MULTIPART_THRESHOLD:
            _get_s3_client().put_object(Bucket=bucket, Key=key, Body=body, **extra)
        else:
            _get_s3_client().upload_fileobj(
                io.BytesIO(body), bucket, key, ExtraArgs=extra if extra else None
            )
    else:
        pathlib.Path(file_path).write_bytes(content)


def save_stream(fileobj: object, file_path: str) -> None:
    """Stream a file-like object to a local path or S3 URL.

    S3 destinations use multipart upload via ``upload_fileobj``. Local
    destinations are written in chunks via ``shutil.copyfileobj``.

    Args:
        fileobj: Readable binary file-like object to stream.
        file_path: Destination local filesystem path or ``s3://bucket/key`` URL.
    """
    if _is_s3(file_path):
        bucket, key = _parse_s3_url(file_path)
        _get_s3_client().upload_fileobj(fileobj, bucket, key)
    else:
        with pathlib.Path(file_path).open("wb") as f:
            shutil.copyfileobj(fileobj, f)


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
    if _is_s3(file_path):
        tp: dict = {"client": _get_s3_client()}
    elif headers:
        tp = {"headers": headers}
    else:
        tp = {}
    with smart_open.open(file_path, "rb", transport_params=tp) as f:
        with zipfile.ZipFile(f) as zf:
            yield zf
