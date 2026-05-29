"""Tests for processor.manifest."""

# Standard library imports
import datetime

# Third party imports
import pandas as pd
from idi_ftm2j_shared.types import ScrapedDocument, ScrapedFiling

# Application imports
from idi_sec_scraper.manifest import (
    ManifestWriter,
    _filings_to_df,
    update_bucket_manifest,
)

_FILING = ScrapedFiling(
    cik="320193",
    accession_number="0001140361-26-006577",
    form_type="8-K",
    filing_date="2026-02-24",
    last_scraped_at=datetime.datetime(2026, 2, 24, tzinfo=datetime.UTC).isoformat(),
    index_url="https://www.sec.gov/Archives/edgar/data/320193/000114036126006577/0001140361-26-006577-index.htm",
    company_name="Apple Inc.",
    documents=[
        ScrapedDocument(
            seq="1",
            description="8-K",
            filename="report.htm",
            type="8-K",
            s3_key="s3://test-bucket/2026-02-24/8-K/320193/000114036126006577/report.htm",
            url="https://www.sec.gov/Archives/edgar/data/320193/000114036126006577/report.htm",
        ),
        ScrapedDocument(
            seq="2",
            description="Exhibit 99.1",
            filename="ex99.htm",
            type="EX-99.1",
            s3_key="s3://test-bucket/2026-02-24/8-K/320193/000114036126006577/ex99.htm",
            url="https://www.sec.gov/Archives/edgar/data/320193/000114036126006577/ex99.htm",
        ),
    ],
)

_FILING_NO_DOCS = ScrapedFiling(
    cik="789019",
    accession_number="0000789019-26-000001",
    form_type="10-K",
    filing_date="2026-01-15",
    last_scraped_at=datetime.datetime(2026, 1, 15, tzinfo=datetime.UTC).isoformat(),
    index_url="https://www.sec.gov/Archives/edgar/data/789019/000078901926000001/0000789019-26-000001-index.htm",
    company_name="Microsoft Corp.",
    documents=[],
)


class TestFilingsToDf:
    """Tests for _filings_to_df()."""

    def test_empty_list_returns_empty_df_with_columns(self):
        result = _filings_to_df([])
        assert result.empty
        assert list(result.columns) == [
            "cik",
            "accession_number",
            "filing_date",
            "form_type",
            "seq",
            "description",
            "filename",
            "type",
            "s3_key",
            "url",
        ]

    def test_filing_with_no_documents_produces_no_rows(self):
        result = _filings_to_df([_FILING_NO_DOCS])
        assert result.empty

    def test_filing_with_documents_produces_one_row_per_document(self):
        result = _filings_to_df([_FILING])
        assert len(result) == 2

    def test_row_values_match_filing_and_document(self):
        result = _filings_to_df([_FILING])
        row = result.iloc[0]
        assert row["cik"] == "320193"
        assert row["accession_number"] == "0001140361-26-006577"
        assert row["filing_date"] == "2026-02-24"
        assert row["form_type"] == "8-K"
        assert row["seq"] == "1"
        assert row["description"] == "8-K"
        assert row["filename"] == "report.htm"
        assert row["type"] == "8-K"
        assert (
            row["s3_key"] == "s3://test-bucket/2026-02-24/8-K/320193/000114036126006577/report.htm"
        )

    def test_multiple_filings_are_concatenated(self):
        filing2 = ScrapedFiling(
            cik="111111",
            accession_number="0001111111-26-000001",
            form_type="8-K",
            filing_date="2026-03-01",
            last_scraped_at="2026-03-01T00:00:00+00:00",
            index_url="https://example.com/index.htm",
            company_name="Acme Corp.",
            documents=[
                ScrapedDocument(
                    seq="1",
                    description="8-K",
                    filename="form.htm",
                    type="8-K",
                    s3_key="s3://test-bucket/2026-03-01/8-K/111111/000111111126000001/form.htm",
                    url="https://example.com/form.htm",
                )
            ],
        )
        result = _filings_to_df([_FILING, filing2])
        assert len(result) == 3
        assert set(result["cik"]) == {"320193", "111111"}


class TestUpdateBucketManifest:
    """Tests for update_bucket_manifest()."""

    def test_noop_when_all_filings_have_no_documents(self, mocker):
        read_mock = mocker.patch("idi_sec_scraper.manifest.pd.read_parquet")
        write_mock = mocker.patch("pandas.DataFrame.to_parquet")
        update_bucket_manifest("test-bucket", [_FILING_NO_DOCS])
        read_mock.assert_not_called()
        write_mock.assert_not_called()

    def test_noop_when_filings_list_is_empty(self, mocker):
        read_mock = mocker.patch("idi_sec_scraper.manifest.pd.read_parquet")
        write_mock = mocker.patch("pandas.DataFrame.to_parquet")
        update_bucket_manifest("test-bucket", [])
        read_mock.assert_not_called()
        write_mock.assert_not_called()

    def test_creates_new_manifest_when_none_exists(self, mocker):
        mocker.patch(
            "idi_sec_scraper.manifest.pd.read_parquet",
            side_effect=FileNotFoundError,
        )
        written: list[pd.DataFrame] = []

        def capture_write(self_df: pd.DataFrame, _path: str, **_kwargs: object) -> None:
            written.append(self_df.copy())

        mocker.patch("pandas.DataFrame.to_parquet", capture_write)
        update_bucket_manifest("test-bucket", [_FILING])

        assert len(written) == 1
        assert len(written[0]) == 2
        assert set(written[0]["s3_key"]) == {
            "s3://test-bucket/2026-02-24/8-K/320193/000114036126006577/report.htm",
            "s3://test-bucket/2026-02-24/8-K/320193/000114036126006577/ex99.htm",
        }

    def test_appends_new_rows_to_existing_manifest(self, mocker):
        existing = pd.DataFrame(
            [
                {
                    "cik": "000001",
                    "accession_number": "0000000001-25-000001",
                    "filing_date": "2025-01-01",
                    "form_type": "8-K",
                    "seq": "1",
                    "description": "8-K",
                    "filename": "old.htm",
                    "type": "8-K",
                    "s3_key": "s3://test-bucket/2025-01-01/8-K/000001/000000000125000001/old.htm",
                    "url": "https://example.com/old.htm",
                }
            ]
        )
        mocker.patch("idi_sec_scraper.manifest.pd.read_parquet", return_value=existing)
        written: list[pd.DataFrame] = []

        def capture_write(self_df: pd.DataFrame, _path: str, **_kwargs: object) -> None:
            written.append(self_df.copy())

        mocker.patch("pandas.DataFrame.to_parquet", capture_write)
        update_bucket_manifest("test-bucket", [_FILING])

        assert len(written) == 1
        assert len(written[0]) == 3

    def test_deduplicates_on_s3_key(self, mocker):
        existing = pd.DataFrame(
            [
                {
                    "cik": "320193",
                    "accession_number": "0001140361-26-006577",
                    "filing_date": "2026-02-24",
                    "form_type": "8-K",
                    "seq": "1",
                    "description": "old description",
                    "filename": "report.htm",
                    "type": "8-K",
                    "s3_key": "s3://test-bucket/2026-02-24/8-K/320193/000114036126006577/report.htm",
                    "url": "https://example.com/report.htm",
                }
            ]
        )
        mocker.patch("idi_sec_scraper.manifest.pd.read_parquet", return_value=existing)
        written: list[pd.DataFrame] = []

        def capture_write(self_df: pd.DataFrame, path: str, **kwargs: object) -> None:
            written.append(self_df.copy())

        mocker.patch("pandas.DataFrame.to_parquet", capture_write)
        update_bucket_manifest("test-bucket", [_FILING])

        assert len(written) == 1
        result = written[0]
        dupe_rows = result[
            result["s3_key"]
            == "s3://test-bucket/2026-02-24/8-K/320193/000114036126006577/report.htm"
        ]
        assert len(dupe_rows) == 1
        assert dupe_rows.iloc[0]["description"] == "8-K"

    def test_writes_to_correct_s3_path(self, mocker):
        mocker.patch(
            "idi_sec_scraper.manifest.pd.read_parquet",
            side_effect=FileNotFoundError,
        )
        paths: list[str] = []

        def capture_write(_self_df: pd.DataFrame, path: str, **_kwargs: object) -> None:
            paths.append(path)

        mocker.patch("pandas.DataFrame.to_parquet", capture_write)
        update_bucket_manifest("my-bucket", [_FILING])

        assert paths == ["s3://my-bucket/sec/manifest.parquet"]


class TestManifestWriter:
    """Tests for ManifestWriter."""

    def _patch_manifest(self, mocker):
        mocker.patch(
            "idi_sec_scraper.manifest.pd.read_parquet",
            side_effect=FileNotFoundError,
        )
        written: list[list] = []

        def capture_write(self_df, _path, **_kwargs):
            written.append(list(self_df["s3_key"]))

        mocker.patch("pandas.DataFrame.to_parquet", capture_write)
        return written

    def test_add_below_threshold_does_not_flush(self, mocker):
        written = self._patch_manifest(mocker)
        writer = ManifestWriter("test-bucket", flush_every=5)
        writer.add(_FILING)
        assert written == []

    def test_add_at_threshold_flushes(self, mocker):
        written = self._patch_manifest(mocker)
        writer = ManifestWriter("test-bucket", flush_every=2)
        writer.add(_FILING)
        assert written == []
        writer.add(_FILING)
        assert len(written) == 1

    def test_flush_writes_buffered_filings(self, mocker):
        written = self._patch_manifest(mocker)
        writer = ManifestWriter("test-bucket", flush_every=1000)
        writer.add(_FILING)
        writer.flush()
        assert len(written) == 1

    def test_flush_clears_buffer(self, mocker):
        written = self._patch_manifest(mocker)
        writer = ManifestWriter("test-bucket", flush_every=1000)
        writer.add(_FILING)
        writer.flush()
        writer.flush()
        assert len(written) == 1

    def test_flush_noop_when_buffer_empty(self, mocker):
        written = self._patch_manifest(mocker)
        writer = ManifestWriter("test-bucket", flush_every=1000)
        writer.flush()
        assert written == []

    def test_default_flush_every_is_1000(self):
        writer = ManifestWriter("test-bucket")
        assert writer._flush_every == 1000
