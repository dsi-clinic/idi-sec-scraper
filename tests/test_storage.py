"""Tests for common.storage — focused on gzip compression and boto3 request behaviour."""

# Standard library imports
import gzip
import json
import pathlib
from unittest.mock import MagicMock

# Third party imports
import pytest
from botocore.exceptions import ClientError

# Application imports
from idi_sec_scraper.common.storage import load_content, load_json, save_content, save_json

TEST_CONTENT = b"<html><body>Hello SEC EDGAR</body></html>"
TEST_DATA = {"cik": "320193", "form_type": "10-K", "documents": []}


def _no_such_key_error() -> ClientError:
    return ClientError({"Error": {"Code": "NoSuchKey", "Message": "Not Found"}}, "GetObject")


def _mock_s3(mocker) -> MagicMock:
    """Patch _get_s3_client and return the mock client."""
    import idi_sec_scraper.common.storage as storage

    storage._s3_client = None
    client = MagicMock()
    mocker.patch("idi_sec_scraper.common.storage._get_s3_client", return_value=client)
    return client


# ---------------------------------------------------------------------------
# save_json — S3 paths
# ---------------------------------------------------------------------------


class TestSaveJsonS3:
    """Tests for save_json with S3 paths."""

    def test_uses_single_put_object(self, mocker):
        """save_json uses a single put_object call, not multipart."""
        s3 = _mock_s3(mocker)

        save_json("s3://my-bucket/sec/manifest.json", TEST_DATA)

        s3.put_object.assert_called_once()
        s3.upload_fileobj.assert_not_called()

    def test_body_is_valid_json(self, mocker):
        """The Body passed to put_object deserialises back to the original data."""
        s3 = _mock_s3(mocker)

        save_json("s3://my-bucket/sec/manifest.json", TEST_DATA)

        body = s3.put_object.call_args.kwargs["Body"]
        assert json.loads(body) == TEST_DATA

    def test_body_is_not_compressed(self, mocker):
        """JSON is stored uncompressed — no ContentEncoding set."""
        s3 = _mock_s3(mocker)

        save_json("s3://my-bucket/sec/manifest.json", TEST_DATA)

        kwargs = s3.put_object.call_args.kwargs
        assert "ContentEncoding" not in kwargs


# ---------------------------------------------------------------------------
# load_json — S3 paths
# ---------------------------------------------------------------------------


class TestLoadJsonS3:
    """Tests for load_json with S3 paths."""

    def test_reads_uncompressed_object(self, mocker):
        """Plain JSON objects are decoded correctly."""
        s3 = _mock_s3(mocker)
        s3.get_object.return_value = {
            "Body": MagicMock(read=lambda: json.dumps(TEST_DATA).encode()),
        }

        assert load_json("s3://my-bucket/sec/manifest.json") == TEST_DATA

    def test_decompresses_gzip_encoded_object(self, mocker):
        """Objects with ContentEncoding: gzip are transparently decompressed."""
        s3 = _mock_s3(mocker)
        compressed = gzip.compress(json.dumps(TEST_DATA).encode())
        s3.get_object.return_value = {
            "Body": MagicMock(read=lambda: compressed),
            "ContentEncoding": "gzip",
        }

        assert load_json("s3://my-bucket/sec/manifest.json") == TEST_DATA

    def test_missing_key_returns_empty_dict(self, mocker):
        """NoSuchKey returns an empty dict by default."""
        s3 = _mock_s3(mocker)
        s3.get_object.side_effect = _no_such_key_error()

        assert load_json("s3://my-bucket/sec/missing.json") == {}

    def test_missing_key_returns_empty_list_for_list_type(self, mocker):
        """NoSuchKey returns an empty list when return_type='list'."""
        s3 = _mock_s3(mocker)
        s3.get_object.side_effect = _no_such_key_error()

        assert load_json("s3://my-bucket/sec/missing.json", return_type="list") == []

    def test_other_client_error_propagates(self, mocker):
        """Non-NoSuchKey ClientErrors are re-raised."""
        s3 = _mock_s3(mocker)
        s3.get_object.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "Forbidden"}}, "GetObject"
        )

        with pytest.raises(ClientError):
            load_json("s3://my-bucket/sec/forbidden.json")

    def test_roundtrip_through_mocked_s3(self, mocker):
        """save_json then load_json recovers the original data end-to-end."""
        s3 = _mock_s3(mocker)

        saved: dict = {}

        def fake_put_object(**kwargs):
            saved["body"] = kwargs["Body"]

        s3.put_object.side_effect = fake_put_object
        save_json("s3://my-bucket/sec/roundtrip.json", TEST_DATA)

        s3.get_object.return_value = {"Body": MagicMock(read=lambda: saved["body"])}
        assert load_json("s3://my-bucket/sec/roundtrip.json") == TEST_DATA


# ---------------------------------------------------------------------------
# save_json / load_json — local filesystem paths
# ---------------------------------------------------------------------------


class TestSaveJsonLocal:
    """Tests for save_json with local filesystem paths."""

    def test_writes_valid_json(self, tmp_path):
        """Local file contains valid JSON that deserialises to the original data."""
        path = str(tmp_path / "manifest.json")
        save_json(path, TEST_DATA)

        assert json.loads(pathlib.Path(path).read_text()) == TEST_DATA

    def test_local_roundtrip(self, tmp_path):
        """save_json + load_json round-trips correctly for local files."""
        path = str(tmp_path / "manifest.json")
        save_json(path, TEST_DATA)
        assert load_json(path) == TEST_DATA


class TestLoadJsonLocal:
    """Tests for load_json with local filesystem paths."""

    def test_missing_local_file_returns_empty_dict(self, tmp_path):
        """Missing local file returns empty dict instead of raising."""
        assert load_json(str(tmp_path / "missing.json")) == {}


# ---------------------------------------------------------------------------
# save_content — S3 paths
# ---------------------------------------------------------------------------


class TestSaveContentS3:
    """Tests for save_content with S3 paths."""

    def test_default_no_compression(self, mocker):
        """save_content does not compress by default."""
        s3 = _mock_s3(mocker)

        save_content("s3://my-bucket/sec/filing.htm", TEST_CONTENT)

        kwargs = s3.put_object.call_args.kwargs
        assert kwargs["Body"] == TEST_CONTENT
        assert "ContentEncoding" not in kwargs

    def test_compress_sets_gzip_encoding(self, mocker):
        """compress=True sets ContentEncoding: gzip on the put_object call."""
        s3 = _mock_s3(mocker)

        save_content("s3://my-bucket/sec/filing.htm", TEST_CONTENT, compress=True)

        s3.put_object.assert_called_once()
        kwargs = s3.put_object.call_args.kwargs
        assert kwargs["Bucket"] == "my-bucket"
        assert kwargs["Key"] == "sec/filing.htm"
        assert kwargs["ContentEncoding"] == "gzip"

    def test_compress_body_is_valid_gzip(self, mocker):
        """The Body passed to put_object decompresses to the original bytes."""
        s3 = _mock_s3(mocker)

        save_content("s3://my-bucket/sec/filing.htm", TEST_CONTENT, compress=True)

        body = s3.put_object.call_args.kwargs["Body"]
        assert gzip.decompress(body) == TEST_CONTENT

    def test_compress_body_is_smaller_than_original(self, mocker):
        """Gzip output is smaller than the input for compressible content."""
        s3 = _mock_s3(mocker)
        content = TEST_CONTENT * 100

        save_content("s3://my-bucket/sec/filing.htm", content, compress=True)

        body = s3.put_object.call_args.kwargs["Body"]
        assert len(body) < len(content)

    def test_large_content_uses_multipart(self, mocker):
        """Content exceeding the threshold uses upload_fileobj."""
        s3 = _mock_s3(mocker)
        mocker.patch(
            "idi_sec_scraper.common.storage._MULTIPART_THRESHOLD",
            10,  # artificially low threshold
        )

        save_content("s3://my-bucket/sec/large.htm", TEST_CONTENT)

        s3.upload_fileobj.assert_called_once()
        s3.put_object.assert_not_called()

    def test_large_compressed_content_uses_multipart_with_encoding(self, mocker):
        """Large content with compress=True uses upload_fileobj with gzip encoding."""
        s3 = _mock_s3(mocker)
        mocker.patch(
            "idi_sec_scraper.common.storage._MULTIPART_THRESHOLD",
            10,  # artificially low threshold
        )

        save_content("s3://my-bucket/sec/large.htm", TEST_CONTENT, compress=True)

        s3.upload_fileobj.assert_called_once()
        s3.put_object.assert_not_called()
        assert s3.upload_fileobj.call_args.kwargs["ExtraArgs"]["ContentEncoding"] == "gzip"


# ---------------------------------------------------------------------------
# load_content — S3 paths
# ---------------------------------------------------------------------------


class TestLoadContentS3:
    """Tests for load_content with S3 paths."""

    def test_decompresses_gzip_encoded_object(self, mocker):
        """Objects with ContentEncoding: gzip are transparently decompressed."""
        s3 = _mock_s3(mocker)
        compressed = gzip.compress(TEST_CONTENT)
        s3.get_object.return_value = {
            "Body": MagicMock(read=lambda: compressed),
            "ContentEncoding": "gzip",
        }

        assert load_content("s3://my-bucket/sec/filing.htm") == TEST_CONTENT

    def test_reads_uncompressed_legacy_object(self, mocker):
        """Objects without ContentEncoding are read as plain bytes (legacy support)."""
        s3 = _mock_s3(mocker)
        s3.get_object.return_value = {
            "Body": MagicMock(read=lambda: TEST_CONTENT),
        }

        assert load_content("s3://my-bucket/sec/legacy.htm") == TEST_CONTENT

    def test_missing_key_returns_empty_bytes(self, mocker):
        """NoSuchKey from S3 returns empty bytes instead of raising."""
        s3 = _mock_s3(mocker)
        s3.get_object.side_effect = _no_such_key_error()

        assert load_content("s3://my-bucket/sec/missing.htm") == b""

    def test_other_client_error_propagates(self, mocker):
        """Non-NoSuchKey ClientErrors are re-raised."""
        s3 = _mock_s3(mocker)
        s3.get_object.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "Forbidden"}}, "GetObject"
        )

        with pytest.raises(ClientError):
            load_content("s3://my-bucket/sec/forbidden.htm")

    def test_roundtrip_through_mocked_s3(self, mocker):
        """save_content then load_content recovers the original string end-to-end."""
        s3 = _mock_s3(mocker)

        saved: dict = {}

        def fake_put_object(**kwargs):
            saved["body"] = kwargs["Body"]
            saved["encoding"] = kwargs.get("ContentEncoding")

        s3.put_object.side_effect = fake_put_object
        save_content("s3://my-bucket/sec/roundtrip.htm", TEST_CONTENT, compress=True)

        s3.get_object.return_value = {
            "Body": MagicMock(read=lambda: saved["body"]),
            "ContentEncoding": saved["encoding"],
        }

        assert load_content("s3://my-bucket/sec/roundtrip.htm") == TEST_CONTENT


# ---------------------------------------------------------------------------
# save_content / load_content — local filesystem paths
# ---------------------------------------------------------------------------


class TestSaveContentLocal:
    """Tests for save_content with local filesystem paths."""

    def test_local_write_is_raw_bytes(self, tmp_path):
        """Local files are written as raw uncompressed bytes."""
        path = str(tmp_path / "out.htm")
        save_content(path, TEST_CONTENT)

        assert pathlib.Path(path).read_bytes() == TEST_CONTENT

    def test_local_roundtrip(self, tmp_path):
        """save_content + load_content round-trips correctly for local files."""
        path = str(tmp_path / "roundtrip.htm")
        save_content(path, TEST_CONTENT)
        assert load_content(path) == TEST_CONTENT


class TestLoadContentLocal:
    """Tests for load_content with local filesystem paths."""

    def test_missing_local_file_returns_empty_bytes(self, tmp_path):
        """Missing local file returns empty bytes instead of raising."""
        assert load_content(str(tmp_path / "missing.htm")) == b""
