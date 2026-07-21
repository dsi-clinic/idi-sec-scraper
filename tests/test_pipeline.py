"""Tests for processor.pipeline."""

# Standard library imports
import dataclasses
import datetime

from idi_ftm2j_shared.api import SecClient
from idi_ftm2j_shared.types import DiscoveredFiling, ScrapedDocument, ScrapedFiling

# Application imports
from idi_sec_scraper.document_filters import FormTypeConfig
from idi_sec_scraper.failures import FailureType
from idi_sec_scraper.paths import filing_s3_prefix
from idi_sec_scraper.pipeline import (
    DailySECScraperPipeline,
    HistoricalSECScraperPipeline,
    _new_scraped_filing,
)
from idi_sec_scraper.types import (
    DailyPipelineConfig,
    HistoricalPipelineConfig,
    ParsedDocument,
    ParsedFiling,
)

_FILTERS_PATH = "config/document_filters.yaml"
_BUCKET = "test-bucket"

_DOC = ParsedDocument(
    seq="1",
    description="8-K",
    filename="report.htm",
    type="8-K",
    url="https://www.sec.gov/Archives/edgar/data/320193/000114036126006577/report.htm",
)

_FILING = DiscoveredFiling(
    cik="320193",
    accession_number="0001140361-26-006577",
    form_type="8-K",
    filing_date=datetime.date(2026, 2, 24),
    url="https://www.sec.gov/Archives/edgar/data/320193/000114036126006577/0001140361-26-006577-index.htm",
    company_name="Apple Inc.",
)


def _make_pipeline(mocker, cls, config):
    """Instantiate a pipeline with a mocked document filter load and manifest writer."""
    mocker.patch(
        "idi_sec_scraper.pipeline.load_document_filters",
        return_value=mocker.MagicMock(form_types={}),
    )
    mocker.patch("idi_sec_scraper.pipeline.ManifestWriter")
    sec_client = mocker.MagicMock(spec_set=SecClient)
    sec_client.sec_headers = {}
    return cls(config, sec_client)


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


class TestFilingS3Prefix:
    """Tests for filing_s3_prefix()."""

    def test_structure(self):
        prefix = filing_s3_prefix(_BUCKET, _FILING)
        assert prefix == ("s3://test-bucket/sec/2026-02-24/8-K/320193/000114036126006577")

    def test_unsafe_form_type_chars_replaced(self):
        filing = dataclasses.replace(_FILING, form_type="10-K/A")
        prefix = filing_s3_prefix(_BUCKET, filing)
        assert "/10-K_A/" in prefix

    def test_space_in_form_type_replaced(self):
        filing = dataclasses.replace(_FILING, form_type="SCHEDULE 13G/A")
        prefix = filing_s3_prefix(_BUCKET, filing)
        assert "/SCHEDULE_13G_A/" in prefix

    def test_accession_number_dashes_removed(self):
        prefix = filing_s3_prefix(_BUCKET, _FILING)
        assert "000114036126006577" in prefix
        assert "0001140361-26-006577" not in prefix


class TestNewScrapedFiling:
    """Tests for _new_scraped_filing()."""

    def test_fields_from_discovered_filing(self):
        result = _new_scraped_filing(_FILING)
        assert result.cik == "320193"
        assert result.accession_number == "0001140361-26-006577"
        assert result.form_type == "8-K"
        assert result.filing_date == "2026-02-24"
        assert result.documents == []

    def test_index_url_and_company_name_from_discovered_filing(self):
        result = _new_scraped_filing(_FILING)
        assert result.index_url == _FILING.url
        assert result.company_name == "Apple Inc."

    def test_failure_reason_defaults_to_empty(self):
        result = _new_scraped_filing(_FILING)
        assert result.failure_reason == ""


# ---------------------------------------------------------------------------
# load_input tests
# ---------------------------------------------------------------------------


class TestHistoricalLoadInput:
    """Tests for HistoricalSECScraperPipeline.load_input()."""

    def test_calls_discover_historical_with_match_patterns(self, mocker):
        from idi_sec_scraper.document_filters import DocumentFilterConfig, FormTypeConfig

        filter_config = DocumentFilterConfig(
            form_types={
                "10-K": FormTypeConfig(match="10-?K"),
                "8-K": FormTypeConfig(match="8-K"),
            }
        )
        mocker.patch(
            "idi_sec_scraper.pipeline.load_document_filters",
            return_value=filter_config,
        )
        mock_cls = mocker.patch("idi_sec_scraper.pipeline.HistoricalDiscovery")
        mock_cls.return_value.discover.return_value = []
        sec_client = mocker.MagicMock(spec_set=SecClient)
        sec_client.sec_headers = {}
        config = HistoricalPipelineConfig(
            bucket=_BUCKET,
            document_filters_path=_FILTERS_PATH,
            submissions_url="s3://bucket/submissions.zip",
        )
        pipeline = HistoricalSECScraperPipeline(config, sec_client)
        pipeline.load_input()

        mock_cls.assert_called_once_with(
            sec_client,
            ["10-?K", "8-K"],
            pipeline.failure_registry,
            cutoffs={"10-?K": None, "8-K": None},
        )
        mock_cls.return_value.discover.assert_called_once_with("s3://bucket/submissions.zip", None)

    def test_https_url_streams_to_s3_then_discovers_from_s3(self, mocker):
        mocker.patch(
            "idi_sec_scraper.pipeline.load_document_filters",
            return_value=mocker.MagicMock(form_types={}),
        )
        mock_cls = mocker.patch("idi_sec_scraper.pipeline.HistoricalDiscovery")
        mock_cls.return_value.discover.return_value = []
        mock_stream = mocker.patch("idi_sec_scraper.pipeline.save_stream")
        sec_client = mocker.MagicMock(spec_set=SecClient)
        sec_client.sec_headers = {"User-Agent": "test"}
        https_url = "https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip"
        config = HistoricalPipelineConfig(
            bucket=_BUCKET,
            document_filters_path=_FILTERS_PATH,
            submissions_url=https_url,
        )
        pipeline = HistoricalSECScraperPipeline(config, sec_client)
        pipeline.load_input()

        sec_client.session.get.assert_called_once_with(
            https_url, headers={"User-Agent": "test"}, stream=True
        )
        mock_stream.assert_called_once_with(
            sec_client.session.get.return_value.raw,
            f"s3://{_BUCKET}/sec/submissions.zip",
        )
        mock_cls.return_value.discover.assert_called_once_with(
            f"s3://{_BUCKET}/sec/submissions.zip", None
        )

    def test_s3_url_skips_download(self, mocker):
        mocker.patch(
            "idi_sec_scraper.pipeline.load_document_filters",
            return_value=mocker.MagicMock(form_types={}),
        )
        mock_cls = mocker.patch("idi_sec_scraper.pipeline.HistoricalDiscovery")
        mock_cls.return_value.discover.return_value = []
        mock_stream = mocker.patch("idi_sec_scraper.pipeline.save_stream")
        sec_client = mocker.MagicMock(spec_set=SecClient)
        sec_client.sec_headers = {}
        config = HistoricalPipelineConfig(
            bucket=_BUCKET,
            document_filters_path=_FILTERS_PATH,
            submissions_url="s3://bucket/submissions.zip",
        )
        pipeline = HistoricalSECScraperPipeline(config, sec_client)
        pipeline.load_input()

        mock_stream.assert_not_called()
        sec_client.session.get.assert_not_called()
        mock_cls.return_value.discover.assert_called_once_with("s3://bucket/submissions.zip", None)

    def test_discovery_held_as_attribute(self, mocker):
        from idi_sec_scraper.document_filters import DocumentFilterConfig, FormTypeConfig

        filter_config = DocumentFilterConfig(form_types={"8-K": FormTypeConfig(match="8-K")})
        mocker.patch(
            "idi_sec_scraper.pipeline.load_document_filters",
            return_value=filter_config,
        )
        mock_cls = mocker.patch("idi_sec_scraper.pipeline.HistoricalDiscovery")
        mock_cls.return_value.discover.return_value = []
        sec_client = mocker.MagicMock(spec_set=SecClient)
        sec_client.sec_headers = {}
        config = HistoricalPipelineConfig(
            bucket=_BUCKET,
            document_filters_path=_FILTERS_PATH,
            submissions_url="s3://x/s.zip",
        )
        pipeline = HistoricalSECScraperPipeline(config, sec_client)
        assert pipeline.discovery is mock_cls.return_value


class TestDailyLoadInput:
    """Tests for DailySECScraperPipeline.load_input()."""

    def test_calls_discover_daily_with_date_range_and_patterns(self, mocker):
        from idi_sec_scraper.document_filters import DocumentFilterConfig, FormTypeConfig

        filter_config = DocumentFilterConfig(form_types={"8-K": FormTypeConfig(match="8-K")})
        mocker.patch(
            "idi_sec_scraper.pipeline.load_document_filters",
            return_value=filter_config,
        )
        mock_cls = mocker.patch("idi_sec_scraper.pipeline.DailyDiscovery")
        mock_cls.return_value.discover.return_value = []
        sec_client = mocker.MagicMock(spec_set=SecClient)
        sec_client.sec_headers = {}
        config = DailyPipelineConfig(
            bucket=_BUCKET,
            document_filters_path=_FILTERS_PATH,
            start_date=datetime.date(2026, 4, 1),
            end_date=datetime.date(2026, 4, 3),
        )
        pipeline = DailySECScraperPipeline(config, sec_client)
        pipeline.load_input()

        mock_cls.assert_called_once_with(sec_client, ["8-K"], cutoffs={"8-K": None})
        mock_cls.return_value.discover.assert_called_once_with(
            datetime.date(2026, 4, 1), datetime.date(2026, 4, 3)
        )

    def test_discovery_held_as_attribute(self, mocker):
        from idi_sec_scraper.document_filters import DocumentFilterConfig, FormTypeConfig

        filter_config = DocumentFilterConfig(form_types={"8-K": FormTypeConfig(match="8-K")})
        mocker.patch(
            "idi_sec_scraper.pipeline.load_document_filters",
            return_value=filter_config,
        )
        mock_cls = mocker.patch("idi_sec_scraper.pipeline.DailyDiscovery")
        mock_cls.return_value.discover.return_value = []
        sec_client = mocker.MagicMock(spec_set=SecClient)
        sec_client.sec_headers = {}
        config = DailyPipelineConfig(bucket=_BUCKET, document_filters_path=_FILTERS_PATH)
        pipeline = DailySECScraperPipeline(config, sec_client)
        assert pipeline.discovery is mock_cls.return_value


# ---------------------------------------------------------------------------
# _scrape_filing tests
# ---------------------------------------------------------------------------


def _make_parsed_filing(docs: list[ParsedDocument]) -> ParsedFiling:
    return ParsedFiling(
        url=_FILING.url,
        cik=_FILING.cik,
        accession_number=_FILING.accession_number,
        form_type=_FILING.form_type,
        filing_date=datetime.date(2026, 2, 24),
        report_date=datetime.date(2026, 2, 24),
        available_documents=docs,
    )


_DEFAULT_SCRAPED_FILING = ScrapedFiling(
    cik=_FILING.cik,
    accession_number=_FILING.accession_number,
    form_type=_FILING.form_type,
    filing_date=str(_FILING.filing_date),
    index_url=_FILING.url,
    company_name=_FILING.company_name,
    documents=[],
)


def _patch_scrape_deps(mocker, *, index_html=b"<html/>", docs=None, cached=False):
    """Patch all external dependencies of _scrape_filing and return mocks."""
    if docs is None:
        docs = []

    mocker.patch("idi_sec_scraper.pipeline.key_exists", return_value=cached)
    mocker.patch("idi_sec_scraper.pipeline.load_content", return_value=index_html)
    if cached:
        mocker.patch("idi_sec_scraper.pipeline.get_filing", return_value=_DEFAULT_SCRAPED_FILING)
    mocker.patch("idi_sec_scraper.pipeline.save_content")
    mocker.patch("idi_sec_scraper.pipeline.save_json")
    mocker.patch(
        "idi_sec_scraper.pipeline.parse_index_htm",
        return_value=(_make_parsed_filing(docs), None),
    )
    mocker.patch(
        "idi_sec_scraper.pipeline.select_and_filter_documents",
        return_value=("8-K", docs),
    )


class TestScrapeFiling:
    """Tests for SECScraperPipeline._scrape_filing()."""

    def test_returns_scraped_filing_on_success(self, mocker):
        _patch_scrape_deps(mocker, docs=[_DOC])
        pipeline = _make_pipeline(
            mocker,
            DailySECScraperPipeline,
            DailyPipelineConfig(
                bucket=_BUCKET,
                document_filters_path=_FILTERS_PATH,
            ),
        )
        pipeline.sec_client.query_endpoint.return_value = {"status_code": 200, "data": "<html/>"}
        result = pipeline._scrape_filing(_FILING)

        assert result is not None
        scraped_filing, was_fully_cached = result
        assert isinstance(scraped_filing, ScrapedFiling)
        assert scraped_filing.cik == "320193"
        assert scraped_filing.accession_number == "0001140361-26-006577"
        assert was_fully_cached is False

    def test_returns_none_on_index_fetch_error(self, mocker):
        _patch_scrape_deps(mocker)
        pipeline = _make_pipeline(
            mocker,
            DailySECScraperPipeline,
            DailyPipelineConfig(
                bucket=_BUCKET,
                document_filters_path=_FILTERS_PATH,
            ),
        )
        pipeline.sec_client.query_endpoint.return_value = {"status_code": 404, "error": "Not Found"}
        result = pipeline._scrape_filing(_FILING)

        assert result is None

    def test_unexpected_exception_returns_none(self, mocker):
        _patch_scrape_deps(mocker, docs=[_DOC])
        mocker.patch(
            "idi_sec_scraper.pipeline.save_json",
            side_effect=RuntimeError("S3 write failed"),
        )
        pipeline = _make_pipeline(
            mocker,
            DailySECScraperPipeline,
            DailyPipelineConfig(
                bucket=_BUCKET,
                document_filters_path=_FILTERS_PATH,
            ),
        )
        pipeline.sec_client.query_endpoint.return_value = {"status_code": 200, "data": "<html/>"}

        result = pipeline._scrape_filing(_FILING)

        assert result is None

    def test_unexpected_exception_does_not_register_failure(self, mocker):
        _patch_scrape_deps(mocker, docs=[_DOC])
        mocker.patch(
            "idi_sec_scraper.pipeline.save_json",
            side_effect=RuntimeError("S3 write failed"),
        )
        pipeline = _make_pipeline(
            mocker,
            DailySECScraperPipeline,
            DailyPipelineConfig(
                bucket=_BUCKET,
                document_filters_path=_FILTERS_PATH,
            ),
        )
        pipeline.sec_client.query_endpoint.return_value = {"status_code": 200, "data": "<html/>"}

        pipeline._scrape_filing(_FILING)

        failure_key = (_FILING.cik, _FILING.accession_number)
        assert failure_key not in pipeline.failure_registry._entries

    def test_unexpected_exception_is_logged(self, mocker):
        _patch_scrape_deps(mocker, docs=[_DOC])
        mocker.patch(
            "idi_sec_scraper.pipeline.save_json",
            side_effect=RuntimeError("S3 write failed"),
        )
        pipeline = _make_pipeline(
            mocker,
            DailySECScraperPipeline,
            DailyPipelineConfig(
                bucket=_BUCKET,
                document_filters_path=_FILTERS_PATH,
            ),
        )
        pipeline.sec_client.query_endpoint.return_value = {"status_code": 200, "data": "<html/>"}
        mock_logger = mocker.patch.object(pipeline, "logger")

        pipeline._scrape_filing(_FILING)

        mock_logger.exception.assert_called_once()
        call_args = mock_logger.exception.call_args
        assert _FILING.cik in call_args.args
        assert _FILING.accession_number in call_args.args
        assert _FILING.form_type in call_args.args
        assert _FILING.url in call_args.args

    def test_index_written_to_s3_on_daily_run(self, mocker):
        _patch_scrape_deps(mocker, docs=[_DOC])
        mock_save = mocker.patch("idi_sec_scraper.pipeline.save_content")
        pipeline = _make_pipeline(
            mocker,
            DailySECScraperPipeline,
            DailyPipelineConfig(
                bucket=_BUCKET,
                document_filters_path=_FILTERS_PATH,
            ),
        )
        pipeline.sec_client.query_endpoint.return_value = {"status_code": 200, "data": "<html/>"}
        pipeline._scrape_filing(_FILING)

        saved_paths = [call.args[0] for call in mock_save.call_args_list]
        assert any("index.htm" in p for p in saved_paths)

    def test_manifest_written_to_s3(self, mocker):
        _patch_scrape_deps(mocker, docs=[_DOC])
        mock_save_json = mocker.patch("idi_sec_scraper.pipeline.save_json")
        pipeline = _make_pipeline(
            mocker,
            DailySECScraperPipeline,
            DailyPipelineConfig(
                bucket=_BUCKET,
                document_filters_path=_FILTERS_PATH,
            ),
        )
        pipeline.sec_client.query_endpoint.return_value = {"status_code": 200, "data": "<html/>"}
        pipeline._scrape_filing(_FILING)

        assert mock_save_json.called
        manifest_path = mock_save_json.call_args.args[0]
        assert "manifest.json" in manifest_path

    def test_report_date_written_to_manifest(self, mocker):
        _patch_scrape_deps(mocker, docs=[_DOC])
        mocker.patch(
            "idi_sec_scraper.pipeline.parse_index_htm",
            return_value=(_make_parsed_filing([_DOC]), None),
        )
        mock_save_json = mocker.patch("idi_sec_scraper.pipeline.save_json")
        pipeline = _make_pipeline(
            mocker,
            DailySECScraperPipeline,
            DailyPipelineConfig(
                bucket=_BUCKET,
                document_filters_path=_FILTERS_PATH,
            ),
        )
        pipeline.sec_client.query_endpoint.return_value = {"status_code": 200, "data": "<html/>"}
        pipeline._scrape_filing(_FILING)

        manifest = mock_save_json.call_args.args[1]
        assert manifest["report_date"] == "2026-02-24"

    def test_report_date_empty_when_not_on_index(self, mocker):
        parsed = _make_parsed_filing([_DOC])
        parsed.report_date = None
        _patch_scrape_deps(mocker, docs=[_DOC])
        mocker.patch(
            "idi_sec_scraper.pipeline.parse_index_htm",
            return_value=(parsed, None),
        )
        mock_save_json = mocker.patch("idi_sec_scraper.pipeline.save_json")
        pipeline = _make_pipeline(
            mocker,
            DailySECScraperPipeline,
            DailyPipelineConfig(
                bucket=_BUCKET,
                document_filters_path=_FILTERS_PATH,
            ),
        )
        pipeline.sec_client.query_endpoint.return_value = {"status_code": 200, "data": "<html/>"}
        pipeline._scrape_filing(_FILING)

        manifest = mock_save_json.call_args.args[1]
        assert manifest["report_date"] == ""

    def test_document_fetched_and_written_to_s3(self, mocker):
        doc = ParsedDocument(
            seq="1",
            description="8-K",
            filename="report.htm",
            type="8-K",
            url="https://www.sec.gov/Archives/edgar/data/320193/000114036126006577/report.htm",
        )
        _patch_scrape_deps(mocker, docs=[doc])
        mock_save = mocker.patch("idi_sec_scraper.pipeline.save_content")
        pipeline = _make_pipeline(
            mocker,
            DailySECScraperPipeline,
            DailyPipelineConfig(
                bucket=_BUCKET,
                document_filters_path=_FILTERS_PATH,
            ),
        )
        pipeline.sec_client.query_endpoint.return_value = {"status_code": 200, "data": "<html/>"}
        pipeline._scrape_filing(_FILING)

        saved_paths = [call.args[0] for call in mock_save.call_args_list]
        assert any("report.htm" in p for p in saved_paths)

    def test_newly_fetched_document_gets_utc_iso_date_scraped(self, mocker):
        doc = ParsedDocument(
            seq="1",
            description="8-K",
            filename="report.htm",
            type="8-K",
            url="https://www.sec.gov/Archives/edgar/data/320193/000114036126006577/report.htm",
        )
        _patch_scrape_deps(mocker, docs=[doc])
        pipeline = _make_pipeline(
            mocker,
            DailySECScraperPipeline,
            DailyPipelineConfig(
                bucket=_BUCKET,
                document_filters_path=_FILTERS_PATH,
            ),
        )
        pipeline.sec_client.query_endpoint.return_value = {"status_code": 200, "data": "<html/>"}
        result = pipeline._scrape_filing(_FILING)

        assert result is not None
        scraped_filing, _ = result
        assert len(scraped_filing.documents) == 1
        date_scraped = scraped_filing.documents[0].date_scraped
        parsed = datetime.datetime.fromisoformat(date_scraped)
        assert parsed.tzinfo == datetime.UTC

    def test_document_in_manifest_is_skipped(self, mocker):
        doc = ParsedDocument(
            seq="1",
            description="8-K",
            filename="report.htm",
            type="8-K",
            url="https://www.sec.gov/Archives/edgar/data/320193/000114036126006577/report.htm",
        )
        _patch_scrape_deps(mocker, docs=[doc], cached=True)
        mocker.patch(
            "idi_sec_scraper.pipeline.get_filing",
            return_value=ScrapedFiling(
                cik=_FILING.cik,
                accession_number=_FILING.accession_number,
                form_type=_FILING.form_type,
                filing_date=str(_FILING.filing_date),
                index_url=_FILING.url,
                company_name=_FILING.company_name,
                documents=[
                    ScrapedDocument(
                        seq="1",
                        description="8-K",
                        filename="report.htm",
                        type="8-K",
                        s3_key="s3://bucket/report.htm",
                        url="https://www.sec.gov/Archives/edgar/data/320193/000114036126006577/report.htm",
                        date_scraped="2026-02-24T00:00:00+00:00",
                    )
                ],
            ),
        )
        pipeline = _make_pipeline(
            mocker,
            HistoricalSECScraperPipeline,
            HistoricalPipelineConfig(
                bucket=_BUCKET,
                document_filters_path=_FILTERS_PATH,
                submissions_url="s3://x/s.zip",
            ),
        )
        result = pipeline._scrape_filing(_FILING)

        assert pipeline.stats.skipped_documents == 1
        assert pipeline.sec_client.query_endpoint.call_count == 0
        # A skipped (already-present) document retains its prior date_scraped.
        assert result is not None
        scraped_filing, _ = result
        assert scraped_filing.documents[0].date_scraped == "2026-02-24T00:00:00+00:00"

    def test_reads_from_s3_when_manifest_cached_historical(self, mocker):
        _patch_scrape_deps(mocker, cached=True)
        mock_load_content = mocker.patch(
            "idi_sec_scraper.pipeline.load_content", return_value=b"<html/>"
        )
        pipeline = _make_pipeline(
            mocker,
            HistoricalSECScraperPipeline,
            HistoricalPipelineConfig(
                bucket=_BUCKET,
                document_filters_path=_FILTERS_PATH,
                submissions_url="s3://x/s.zip",
            ),
        )
        pipeline._scrape_filing(_FILING)

        mock_load_content.assert_called_once()
        pipeline.sec_client.query_endpoint.assert_not_called()

    def test_reads_from_s3_when_manifest_cached_daily(self, mocker):
        _patch_scrape_deps(mocker, cached=True)
        mock_load_content = mocker.patch(
            "idi_sec_scraper.pipeline.load_content", return_value=b"<html/>"
        )
        pipeline = _make_pipeline(
            mocker,
            DailySECScraperPipeline,
            DailyPipelineConfig(
                bucket=_BUCKET,
                document_filters_path=_FILTERS_PATH,
            ),
        )
        pipeline._scrape_filing(_FILING)

        mock_load_content.assert_called_once()
        pipeline.sec_client.query_endpoint.assert_not_called()

    def test_known_filing_in_daily_index_logs_warning(self, mocker):
        _patch_scrape_deps(mocker, cached=True)
        mocker.patch("idi_sec_scraper.pipeline.load_content", return_value=b"<html/>")
        pipeline = _make_pipeline(
            mocker,
            DailySECScraperPipeline,
            DailyPipelineConfig(
                bucket=_BUCKET,
                document_filters_path=_FILTERS_PATH,
            ),
        )
        mock_logger = mocker.patch.object(pipeline, "logger")
        pipeline._scrape_filing(_FILING)

        warnings = [call.args[0] for call in mock_logger.warning.call_args_list if call.args]
        assert any("Known filing seen in daily index" in msg for msg in warnings)

    def test_known_filing_in_historical_run_does_not_warn(self, mocker):
        _patch_scrape_deps(mocker, cached=True)
        mocker.patch("idi_sec_scraper.pipeline.load_content", return_value=b"<html/>")
        pipeline = _make_pipeline(
            mocker,
            HistoricalSECScraperPipeline,
            HistoricalPipelineConfig(
                bucket=_BUCKET,
                document_filters_path=_FILTERS_PATH,
                submissions_url="s3://x/s.zip",
            ),
        )
        mock_logger = mocker.patch.object(pipeline, "logger")
        pipeline._scrape_filing(_FILING)

        warnings = [call.args[0] for call in mock_logger.warning.call_args_list if call.args]
        assert not any("Known filing seen in daily index" in msg for msg in warnings)

    def test_failed_document_fetch_increments_stat(self, mocker):
        doc = ParsedDocument(
            seq="1",
            description="8-K",
            filename="report.htm",
            type="8-K",
            url="https://www.sec.gov/Archives/edgar/data/320193/000114036126006577/report.htm",
        )
        _patch_scrape_deps(mocker, docs=[doc])
        pipeline = _make_pipeline(
            mocker,
            DailySECScraperPipeline,
            DailyPipelineConfig(
                bucket=_BUCKET,
                document_filters_path=_FILTERS_PATH,
            ),
        )
        pipeline.sec_client.query_endpoint.side_effect = [
            {"status_code": 200, "data": "<html/>"},  # index fetch
            {"status_code": 404, "error": "Not Found"},  # doc fetch
        ]
        pipeline._scrape_filing(_FILING)

        assert pipeline.stats.failed_documents == 1

    def test_query_endpoint_called_for_index_and_each_document(self, mocker):
        doc = ParsedDocument(
            seq="1",
            description="8-K",
            filename="report.htm",
            type="8-K",
            url="https://www.sec.gov/Archives/edgar/data/320193/000114036126006577/report.htm",
        )
        _patch_scrape_deps(mocker, docs=[doc])
        pipeline = _make_pipeline(
            mocker,
            DailySECScraperPipeline,
            DailyPipelineConfig(
                bucket=_BUCKET,
                document_filters_path=_FILTERS_PATH,
            ),
        )
        pipeline.sec_client.query_endpoint.return_value = {"status_code": 200, "data": "<html/>"}
        pipeline._scrape_filing(_FILING)

        # once for index fetch, once for the document fetch
        assert pipeline.sec_client.query_endpoint.call_count == 2

    def test_index_fetch_api_error_returns_none_without_registering(self, mocker):
        _patch_scrape_deps(mocker)
        pipeline = _make_pipeline(
            mocker,
            DailySECScraperPipeline,
            DailyPipelineConfig(
                bucket=_BUCKET,
                document_filters_path=_FILTERS_PATH,
            ),
        )
        pipeline.sec_client.query_endpoint.return_value = {"status_code": 404, "error": "Not Found"}
        result = pipeline._scrape_filing(_FILING)

        assert result is None
        failure_key = (_FILING.cik, _FILING.accession_number)
        assert failure_key not in pipeline.failure_registry._entries

    def test_index_fetch_rate_limit_returns_none_without_registering(self, mocker):
        _patch_scrape_deps(mocker)
        pipeline = _make_pipeline(
            mocker,
            DailySECScraperPipeline,
            DailyPipelineConfig(
                bucket=_BUCKET,
                document_filters_path=_FILTERS_PATH,
            ),
        )
        pipeline.sec_client.query_endpoint.return_value = {
            "status_code": 429,
            "error": "Too Many Requests",
        }
        result = pipeline._scrape_filing(_FILING)

        assert result is None
        failure_key = (_FILING.cik, _FILING.accession_number)
        assert failure_key not in pipeline.failure_registry._entries

    def test_document_fetch_rate_limit_does_not_register_failure(self, mocker):
        _patch_scrape_deps(mocker, docs=[_DOC])
        pipeline = _make_pipeline(
            mocker,
            DailySECScraperPipeline,
            DailyPipelineConfig(
                bucket=_BUCKET,
                document_filters_path=_FILTERS_PATH,
            ),
        )
        pipeline.sec_client.query_endpoint.side_effect = [
            {"status_code": 200, "data": "<html/>"},  # index fetch
            {"status_code": 429, "error": "Too Many Requests"},  # doc fetch
        ]
        pipeline._scrape_filing(_FILING)

        failure_key = (_FILING.cik, _FILING.accession_number)
        assert failure_key not in pipeline.failure_registry._entries
        assert pipeline.stats.failed_documents == 1

    def test_failed_document_fetch_does_not_register_failure(self, mocker):
        _patch_scrape_deps(mocker, docs=[_DOC])
        pipeline = _make_pipeline(
            mocker,
            DailySECScraperPipeline,
            DailyPipelineConfig(
                bucket=_BUCKET,
                document_filters_path=_FILTERS_PATH,
            ),
        )
        pipeline.sec_client.query_endpoint.side_effect = [
            {"status_code": 200, "data": "<html/>"},  # index fetch
            {"status_code": 404, "error": "Not Found"},  # doc fetch
        ]
        pipeline._scrape_filing(_FILING)

        failure_key = (_FILING.cik, _FILING.accession_number)
        assert failure_key not in pipeline.failure_registry._entries

    def test_no_filtered_docs_registers_retryable_failure_and_returns_none(self, mocker):
        _patch_scrape_deps(mocker, docs=[])
        pipeline = _make_pipeline(
            mocker,
            DailySECScraperPipeline,
            DailyPipelineConfig(
                bucket=_BUCKET,
                document_filters_path=_FILTERS_PATH,
            ),
        )
        add_spy = mocker.spy(pipeline.failure_registry, "add")
        pipeline.sec_client.query_endpoint.return_value = {"status_code": 200, "data": "<html/>"}

        result = pipeline._scrape_filing(_FILING)

        assert result is None
        failure_key = (_FILING.cik, _FILING.accession_number)
        assert failure_key not in pipeline.failure_registry._entries
        add_spy.assert_any_call(failure_key, FailureType.DOCUMENTS_MISSING)

    def test_form_not_configured_registers_failure_and_returns_none(self, mocker):
        _patch_scrape_deps(mocker, docs=[_DOC])
        mocker.patch(
            "idi_sec_scraper.pipeline.select_and_filter_documents",
            return_value=(None, []),
        )
        pipeline = _make_pipeline(
            mocker,
            DailySECScraperPipeline,
            DailyPipelineConfig(
                bucket=_BUCKET,
                document_filters_path=_FILTERS_PATH,
            ),
        )
        result = pipeline._scrape_filing(_FILING)

        assert result is None
        failure_key = (_FILING.cik, _FILING.accession_number)
        assert failure_key in pipeline.failure_registry._entries
        assert pipeline.failure_registry._reasons[failure_key] == str(
            FailureType.FORM_NOT_CONFIGURED
        )


class TestScrapingWithParseValidationFailure:
    """Integration test: parse_index_htm returning a failure stops _scrape_filing."""

    def test_parse_failure_registers_failure_and_returns_none(self, mocker):
        _patch_scrape_deps(mocker)
        mocker.patch(
            "idi_sec_scraper.pipeline.parse_index_htm",
            return_value=(_make_parsed_filing([]), FailureType.CIK_MISMATCH),
        )
        pipeline = _make_pipeline(
            mocker,
            DailySECScraperPipeline,
            DailyPipelineConfig(
                bucket=_BUCKET,
                document_filters_path=_FILTERS_PATH,
            ),
        )

        result = pipeline._scrape_filing(_FILING)

        assert result is None
        failure_key = (_FILING.cik, _FILING.accession_number)
        assert failure_key in pipeline.failure_registry._entries
        assert pipeline.failure_registry._reasons[failure_key] == str(FailureType.CIK_MISMATCH)

    def test_parse_failure_writes_manifest_with_failure_reason(self, mocker):
        _patch_scrape_deps(mocker)
        mocker.patch(
            "idi_sec_scraper.pipeline.parse_index_htm",
            return_value=(_make_parsed_filing([]), FailureType.CIK_MISMATCH),
        )
        mock_save_json = mocker.patch("idi_sec_scraper.pipeline.save_json")
        pipeline = _make_pipeline(
            mocker,
            DailySECScraperPipeline,
            DailyPipelineConfig(
                bucket=_BUCKET,
                document_filters_path=_FILTERS_PATH,
            ),
        )

        pipeline._scrape_filing(_FILING)

        assert mock_save_json.called
        manifest = mock_save_json.call_args.args[1]
        assert manifest["failure_reason"] == str(FailureType.CIK_MISMATCH)
        assert manifest["documents"] == []

    def test_documents_missing_writes_manifest_with_failure_reason(self, mocker):
        _patch_scrape_deps(mocker, docs=[])
        mock_save_json = mocker.patch("idi_sec_scraper.pipeline.save_json")
        pipeline = _make_pipeline(
            mocker,
            DailySECScraperPipeline,
            DailyPipelineConfig(
                bucket=_BUCKET,
                document_filters_path=_FILTERS_PATH,
            ),
        )
        pipeline.sec_client.query_endpoint.return_value = {"status_code": 200, "data": "<html/>"}

        pipeline._scrape_filing(_FILING)

        assert mock_save_json.called
        manifest = mock_save_json.call_args.args[1]
        assert manifest["documents"] == []
        assert manifest["failure_reason"] == str(FailureType.DOCUMENTS_MISSING)


# ---------------------------------------------------------------------------
# process() tests
# ---------------------------------------------------------------------------


class TestProcess:
    """Tests for SECScraperPipeline.process()."""

    def test_known_failure_is_skipped_and_counted(self, mocker):
        _patch_scrape_deps(mocker)
        pipeline = _make_pipeline(
            mocker,
            DailySECScraperPipeline,
            DailyPipelineConfig(
                bucket=_BUCKET,
                document_filters_path=_FILTERS_PATH,
            ),
        )
        failure_key = (_FILING.cik, _FILING.accession_number)
        pipeline.failure_registry._entries.add(failure_key)

        pipeline.process([_FILING])

        pipeline.sec_client.query_endpoint.assert_not_called()
        assert pipeline.stats.skipped_known_failure_filings == 1
        assert pipeline.stats.failed_filings == 0

    def test_known_failure_not_double_counted_as_failed(self, mocker):
        _patch_scrape_deps(mocker)
        pipeline = _make_pipeline(
            mocker,
            DailySECScraperPipeline,
            DailyPipelineConfig(
                bucket=_BUCKET,
                document_filters_path=_FILTERS_PATH,
            ),
        )
        failure_key = (_FILING.cik, _FILING.accession_number)
        pipeline.failure_registry._entries.add(failure_key)

        pipeline.process([_FILING])

        assert pipeline.stats.skipped_known_failure_filings == 1
        assert pipeline.stats.failed_filings == 0
        assert pipeline.stats.total_filings == 1

    def test_successful_filing_counted(self, mocker):
        from idi_sec_scraper.document_filters import DocumentFilterConfig

        mocker.patch(
            "idi_sec_scraper.pipeline.load_document_filters",
            return_value=DocumentFilterConfig(form_types={"8-K": FormTypeConfig(match="8-K")}),
        )
        mocker.patch("idi_sec_scraper.pipeline.ManifestWriter")
        _patch_scrape_deps(mocker, docs=[_DOC])
        sec_client = mocker.MagicMock(spec_set=SecClient)
        sec_client.sec_headers = {}
        pipeline = DailySECScraperPipeline(
            DailyPipelineConfig(bucket=_BUCKET, document_filters_path=_FILTERS_PATH),
            sec_client,
        )
        pipeline.sec_client.query_endpoint.return_value = {"status_code": 200, "data": "<html/>"}

        pipeline.process([_FILING])

        assert pipeline.manifest_writer.add.call_count == 1
        assert pipeline.stats.scraped_filings == 1
        assert pipeline.stats.failed_filings == 0
        assert pipeline.stats.form_type_filings_total["8-K"] == 1
        assert pipeline.stats.form_type_documents_total["8-K"] == 1

    def test_failed_filing_counted(self, mocker):
        from idi_sec_scraper.document_filters import DocumentFilterConfig

        mocker.patch(
            "idi_sec_scraper.pipeline.load_document_filters",
            return_value=DocumentFilterConfig(form_types={"8-K": FormTypeConfig(match="8-K")}),
        )
        mocker.patch("idi_sec_scraper.pipeline.ManifestWriter")
        _patch_scrape_deps(mocker)
        sec_client = mocker.MagicMock(spec_set=SecClient)
        sec_client.sec_headers = {}
        pipeline = DailySECScraperPipeline(
            DailyPipelineConfig(bucket=_BUCKET, document_filters_path=_FILTERS_PATH),
            sec_client,
        )
        pipeline.sec_client.query_endpoint.return_value = {"status_code": 404, "error": "Not Found"}

        pipeline.process([_FILING])

        pipeline.manifest_writer.add.assert_not_called()
        assert pipeline.stats.failed_filings == 1
        assert pipeline.stats.skipped_cached_filings == 0
        assert pipeline.stats.skipped_known_failure_filings == 0
        assert pipeline.stats.form_type_filings_total["8-K"] == 1

    def test_fully_cached_filing_counted_as_skipped_not_scraped(self, mocker):
        _patch_scrape_deps(mocker, docs=[_DOC], cached=True)
        mocker.patch(
            "idi_sec_scraper.pipeline.get_filing",
            return_value=ScrapedFiling(
                cik=_FILING.cik,
                accession_number=_FILING.accession_number,
                form_type=_FILING.form_type,
                filing_date=str(_FILING.filing_date),
                index_url=_FILING.url,
                company_name=_FILING.company_name,
                documents=[
                    ScrapedDocument(
                        seq="1",
                        description="8-K",
                        filename="report.htm",
                        type="8-K",
                        s3_key="s3://bucket/report.htm",
                        url=_DOC.url,
                        date_scraped="2026-02-24T00:00:00+00:00",
                    )
                ],
            ),
        )
        pipeline = _make_pipeline(
            mocker,
            HistoricalSECScraperPipeline,
            HistoricalPipelineConfig(
                bucket=_BUCKET,
                document_filters_path=_FILTERS_PATH,
                submissions_url="s3://x/s.zip",
            ),
        )

        pipeline.process([_FILING])

        pipeline.manifest_writer.add.assert_not_called()
        assert pipeline.stats.skipped_cached_filings == 1
        assert pipeline.stats.scraped_filings == 0
        assert pipeline.stats.failed_filings == 0
        pipeline.sec_client.query_endpoint.assert_not_called()

    def test_partially_cached_filing_counted_as_scraped(self, mocker):
        """Index cached but one new document → not fully cached, counts as scraped."""
        _patch_scrape_deps(mocker, docs=[_DOC], cached=True)
        pipeline = _make_pipeline(
            mocker,
            HistoricalSECScraperPipeline,
            HistoricalPipelineConfig(
                bucket=_BUCKET,
                document_filters_path=_FILTERS_PATH,
                submissions_url="s3://x/s.zip",
            ),
        )
        pipeline.sec_client.query_endpoint.return_value = {"status_code": 200, "data": "content"}

        pipeline.process([_FILING])

        assert pipeline.manifest_writer.add.call_count == 1
        assert pipeline.stats.scraped_filings == 1
        assert pipeline.stats.skipped_cached_filings == 0
        assert pipeline.stats.skipped_known_failure_filings == 0
