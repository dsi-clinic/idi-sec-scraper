"""Tests for common.storage — focused on gzip compression behaviour."""

# Standard library imports
import gzip
import pathlib
from unittest.mock import MagicMock

# Third party imports
import pytest
from botocore.exceptions import ClientError

# Application imports
from idi_sec_scraper.common.storage import load_content, save_content

TEST_CONTENT = "<html><body>Hello SEC EDGAR</body></html>"


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
# save_content — S3 paths
# ---------------------------------------------------------------------------


class TestSaveContentS3:
    """Tests for save_content with S3 paths."""

    def test_calls_put_object_with_gzip_encoding(self, mocker):
        """save_content sets ContentEncoding: gzip on the S3 put_object call."""
        s3 = _mock_s3(mocker)

        save_content("s3://my-bucket/sec/filing.htm", TEST_CONTENT)

        s3.put_object.assert_called_once()
        kwargs = s3.put_object.call_args.kwargs
        assert kwargs["Bucket"] == "my-bucket"
        assert kwargs["Key"] == "sec/filing.htm"
        assert kwargs["ContentEncoding"] == "gzip"

    def test_body_is_valid_gzip_of_original_content(self, mocker):
        """The Body passed to put_object decompresses to the original string."""
        s3 = _mock_s3(mocker)

        save_content("s3://my-bucket/sec/filing.htm", TEST_CONTENT)

        body = s3.put_object.call_args.kwargs["Body"]
        assert gzip.decompress(body).decode() == TEST_CONTENT

    def test_compressed_body_is_smaller_than_original(self, mocker):
        """Gzip output is smaller than the input for compressible text."""
        s3 = _mock_s3(mocker)
        content = TEST_CONTENT * 100

        save_content("s3://my-bucket/sec/filing.htm", content)

        body = s3.put_object.call_args.kwargs["Body"]
        assert len(body) < len(content.encode())


# ---------------------------------------------------------------------------
# load_content — S3 paths
# ---------------------------------------------------------------------------


class TestLoadContentS3:
    """Tests for load_content with S3 paths."""

    def test_decompresses_gzip_encoded_object(self, mocker):
        """Objects with ContentEncoding: gzip are transparently decompressed."""
        s3 = _mock_s3(mocker)
        compressed = gzip.compress(TEST_CONTENT.encode())
        s3.get_object.return_value = {
            "Body": MagicMock(read=lambda: compressed),
            "ContentEncoding": "gzip",
        }

        result = load_content("s3://my-bucket/sec/filing.htm")

        assert result == TEST_CONTENT

    def test_reads_uncompressed_legacy_object(self, mocker):
        """Objects without ContentEncoding are read as plain bytes (legacy support)."""
        s3 = _mock_s3(mocker)
        s3.get_object.return_value = {
            "Body": MagicMock(read=lambda: TEST_CONTENT.encode()),
        }

        result = load_content("s3://my-bucket/sec/legacy.htm")

        assert result == TEST_CONTENT

    def test_missing_key_returns_empty_string(self, mocker):
        """NoSuchKey from S3 returns an empty string instead of raising."""
        s3 = _mock_s3(mocker)
        s3.get_object.side_effect = _no_such_key_error()

        assert load_content("s3://my-bucket/sec/missing.htm") == ""

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

        # Capture what save_content puts into S3
        saved: dict = {}

        def fake_put_object(**kwargs):
            saved["body"] = kwargs["Body"]
            saved["encoding"] = kwargs.get("ContentEncoding")

        s3.put_object.side_effect = fake_put_object

        save_content("s3://my-bucket/sec/roundtrip.htm", TEST_CONTENT)

        # Wire load_content to return what was saved
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

    def test_local_write_is_plain_text(self, tmp_path):
        """Local files are written uncompressed."""
        path = str(tmp_path / "out.htm")
        save_content(path, TEST_CONTENT)

        with pathlib.Path(path).open() as f:
            assert f.read() == TEST_CONTENT

    def test_local_roundtrip(self, tmp_path):
        """save_content + load_content round-trips correctly for local files."""
        path = str(tmp_path / "roundtrip.htm")
        save_content(path, TEST_CONTENT)
        assert load_content(path) == TEST_CONTENT


class TestLoadContentLocal:
    """Tests for load_content with local filesystem paths."""

    def test_missing_local_file_returns_empty_string(self, tmp_path):
        """Missing local file returns empty string instead of raising."""
        assert load_content(str(tmp_path / "missing.htm")) == ""
