"""Tests for processor.pipeline."""

# Standard library imports
import dataclasses
import datetime

# Application imports
from idi_sec_scraper.processor.document_filters import FormTypeConfig
from idi_sec_scraper.processor.failures import FailureType
from idi_sec_scraper.processor.pipeline import (
    DailySECScraperPipeline,
    HistoricalSECScraperPipeline,
    _new_scraped_filing,
    _s3_prefix,
    _scraped_filing_from_dict,
)
from idi_sec_scraper.processor.types import (
    DailyPipelineConfig,
    DiscoveredFiling,
    HistoricalPipelineConfig,
    ParsedDocument,
    ParsedFiling,
    ScrapedDocument,
    ScrapedFiling,
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
    """Instantiate a pipeline with a mocked document filter load."""
    mocker.patch(
        "idi_sec_scraper.processor.pipeline.load_document_filters",
        return_value=mocker.MagicMock(form_types={}),
    )
    sec_client = mocker.MagicMock()
    sec_client.SEC_HEADERS = {}
    return cls(config, sec_client)


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


class TestS3Prefix:
    """Tests for _s3_prefix()."""

    def test_structure(self):
        prefix = _s3_prefix(_BUCKET, _FILING)
        assert prefix == ("s3://test-bucket/2026-02-24/8-K/320193/000114036126006577")

    def test_unsafe_form_type_chars_replaced(self):
        filing = dataclasses.replace(_FILING, form_type="10-K/A")
        prefix = _s3_prefix(_BUCKET, filing)
        assert "/10-K_A/" in prefix

    def test_space_in_form_type_replaced(self):
        filing = dataclasses.replace(_FILING, form_type="SCHEDULE 13G/A")
        prefix = _s3_prefix(_BUCKET, filing)
        assert "/SCHEDULE_13G_A/" in prefix

    def test_accession_number_dashes_removed(self):
        prefix = _s3_prefix(_BUCKET, _FILING)
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

    def test_last_scraped_at_is_utc_iso_string(self):
        result = _new_scraped_filing(_FILING)
        dt = datetime.datetime.fromisoformat(result.last_scraped_at)
        assert dt.tzinfo == datetime.UTC

    def test_index_url_and_company_name_from_discovered_filing(self):
        result = _new_scraped_filing(_FILING)
        assert result.index_url == _FILING.url
        assert result.company_name == "Apple Inc."

    def test_failure_reason_defaults_to_empty(self):
        result = _new_scraped_filing(_FILING)
        assert result.failure_reason == ""


class TestScrapedFilingFromDict:
    """Tests for _scraped_filing_from_dict()."""

    def test_round_trips_via_dataclasses_asdict(self):
        original = ScrapedFiling(
            cik="320193",
            accession_number="0001140361-26-006577",
            form_type="8-K",
            filing_date="2026-02-24",
            last_scraped_at="2026-02-24T12:00:00+00:00",
            index_url="https://www.sec.gov/Archives/edgar/data/320193/000114036126006577/0001140361-26-006577-index.htm",
            company_name="Apple Inc.",
            documents=[
                ScrapedDocument(
                    seq="1",
                    description="8-K",
                    filename="doc.htm",
                    type="8-K",
                    s3_key="s3://bucket/doc.htm",
                    url="https://sec.gov/doc.htm",
                )
            ],
        )
        result = _scraped_filing_from_dict(dataclasses.asdict(original))
        assert result == original

    def test_missing_documents_defaults_to_empty(self):
        data = {
            "cik": "1",
            "accession_number": "0000000001-26-000001",
            "form_type": "8-K",
            "filing_date": "2026-01-01",
            "last_scraped_at": "2026-01-01T00:00:00+00:00",
            "index_url": "https://www.sec.gov/index.htm",
            "company_name": "Test Co.",
        }
        result = _scraped_filing_from_dict(data)
        assert result.documents == []

    def test_legacy_manifest_without_index_url_and_company_name(self):
        data = {
            "cik": "1",
            "accession_number": "0000000001-26-000001",
            "form_type": "8-K",
            "filing_date": "2026-01-01",
            "last_scraped_at": "2026-01-01T00:00:00+00:00",
        }
        result = _scraped_filing_from_dict(data)
        assert result.index_url == ""
        assert result.company_name == ""

    def test_failure_reason_defaults_to_empty_string(self):
        data = {
            "cik": "1",
            "accession_number": "0000000001-26-000001",
            "form_type": "8-K",
            "filing_date": "2026-01-01",
            "last_scraped_at": "2026-01-01T00:00:00+00:00",
        }
        result = _scraped_filing_from_dict(data)
        assert result.failure_reason == ""

    def test_failure_reason_round_trips(self):
        original = ScrapedFiling(
            cik="1",
            accession_number="0000000001-26-000001",
            form_type="8-K",
            filing_date="2026-01-01",
            last_scraped_at="2026-01-01T00:00:00+00:00",
            index_url="",
            company_name="",
            failure_reason="cik_mismatch",
        )
        result = _scraped_filing_from_dict(dataclasses.asdict(original))
        assert result.failure_reason == "cik_mismatch"

    def test_report_date_defaults_to_empty_string(self):
        data = {
            "cik": "1",
            "accession_number": "0000000001-26-000001",
            "form_type": "8-K",
            "filing_date": "2026-01-01",
            "last_scraped_at": "2026-01-01T00:00:00+00:00",
        }
        result = _scraped_filing_from_dict(data)
        assert result.report_date == ""

    def test_report_date_round_trips(self):
        original = ScrapedFiling(
            cik="1",
            accession_number="0000000001-26-000001",
            form_type="8-K",
            filing_date="2026-01-01",
            last_scraped_at="2026-01-01T00:00:00+00:00",
            index_url="",
            company_name="",
            report_date="2026-01-15",
        )
        result = _scraped_filing_from_dict(dataclasses.asdict(original))
        assert result.report_date == "2026-01-15"


# ---------------------------------------------------------------------------
# load_input tests
# ---------------------------------------------------------------------------


class TestHistoricalLoadInput:
    """Tests for HistoricalSECScraperPipeline.load_input()."""

    def test_calls_discover_historical_with_match_patterns(self, mocker):
        from idi_sec_scraper.processor.document_filters import DocumentFilterConfig, FormTypeConfig

        filter_config = DocumentFilterConfig(
            form_types={
                "10-K": FormTypeConfig(match="10-?K"),
                "8-K": FormTypeConfig(match="8-K"),
            }
        )
        mocker.patch(
            "idi_sec_scraper.processor.pipeline.load_document_filters",
            return_value=filter_config,
        )
        mock_cls = mocker.patch("idi_sec_scraper.processor.pipeline.HistoricalDiscovery")
        mock_cls.return_value.discover.return_value = []
        sec_client = mocker.MagicMock()
        sec_client.SEC_HEADERS = {}
        config = HistoricalPipelineConfig(
            bucket=_BUCKET,
            document_filters_path=_FILTERS_PATH,
            submissions_url="s3://bucket/submissions.zip",
        )
        pipeline = HistoricalSECScraperPipeline(config, sec_client)
        pipeline.load_input()

        mock_cls.assert_called_once_with(sec_client, ["10-?K", "8-K"], pipeline.failure_registry)
        mock_cls.return_value.discover.assert_called_once_with("s3://bucket/submissions.zip", None)

    def test_discovery_held_as_attribute(self, mocker):
        from idi_sec_scraper.processor.document_filters import DocumentFilterConfig, FormTypeConfig

        filter_config = DocumentFilterConfig(form_types={"8-K": FormTypeConfig(match="8-K")})
        mocker.patch(
            "idi_sec_scraper.processor.pipeline.load_document_filters",
            return_value=filter_config,
        )
        mock_cls = mocker.patch("idi_sec_scraper.processor.pipeline.HistoricalDiscovery")
        mock_cls.return_value.discover.return_value = []
        sec_client = mocker.MagicMock()
        sec_client.SEC_HEADERS = {}
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
        from idi_sec_scraper.processor.document_filters import DocumentFilterConfig, FormTypeConfig

        filter_config = DocumentFilterConfig(form_types={"8-K": FormTypeConfig(match="8-K")})
        mocker.patch(
            "idi_sec_scraper.processor.pipeline.load_document_filters",
            return_value=filter_config,
        )
        mock_cls = mocker.patch("idi_sec_scraper.processor.pipeline.DailyDiscovery")
        mock_cls.return_value.discover.return_value = []
        sec_client = mocker.MagicMock()
        sec_client.SEC_HEADERS = {}
        config = DailyPipelineConfig(
            bucket=_BUCKET,
            document_filters_path=_FILTERS_PATH,
            start_date=datetime.date(2026, 4, 1),
            end_date=datetime.date(2026, 4, 3),
        )
        pipeline = DailySECScraperPipeline(config, sec_client)
        pipeline.load_input()

        mock_cls.assert_called_once_with(sec_client, ["8-K"])
        mock_cls.return_value.discover.assert_called_once_with(
            datetime.date(2026, 4, 1), datetime.date(2026, 4, 3)
        )

    def test_discovery_held_as_attribute(self, mocker):
        from idi_sec_scraper.processor.document_filters import DocumentFilterConfig, FormTypeConfig

        filter_config = DocumentFilterConfig(form_types={"8-K": FormTypeConfig(match="8-K")})
        mocker.patch(
            "idi_sec_scraper.processor.pipeline.load_document_filters",
            return_value=filter_config,
        )
        mock_cls = mocker.patch("idi_sec_scraper.processor.pipeline.DailyDiscovery")
        mock_cls.return_value.discover.return_value = []
        sec_client = mocker.MagicMock()
        sec_client.SEC_HEADERS = {}
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
        last_scraped_at=datetime.datetime.now(datetime.UTC),
        available_documents=docs,
    )


_MANIFEST_DICT = {
    "cik": _FILING.cik,
    "accession_number": _FILING.accession_number,
    "form_type": _FILING.form_type,
    "filing_date": str(_FILING.filing_date),
    "last_scraped_at": "2026-02-24T00:00:00+00:00",
    "index_url": _FILING.url,
    "company_name": _FILING.company_name,
    "documents": [],
}


def _patch_scrape_deps(mocker, *, index_html="<html/>", docs=None, cached=False):
    """Patch all external dependencies of _scrape_filing and return mocks."""
    if docs is None:
        docs = []

    mocker.patch("idi_sec_scraper.processor.pipeline.key_exists", return_value=cached)
    mocker.patch("idi_sec_scraper.processor.pipeline.load_content", return_value=index_html)
    mocker.patch("idi_sec_scraper.processor.pipeline.load_json", return_value=_MANIFEST_DICT)
    mocker.patch("idi_sec_scraper.processor.pipeline.save_content")
    mocker.patch("idi_sec_scraper.processor.pipeline.save_json")
    mocker.patch(
        "idi_sec_scraper.processor.pipeline.parse_index_htm",
        return_value=(_make_parsed_filing(docs), None),
    )
    mocker.patch(
        "idi_sec_scraper.processor.pipeline.select_and_filter_documents",
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
            "idi_sec_scraper.processor.pipeline.save_json",
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
            "idi_sec_scraper.processor.pipeline.save_json",
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
            "idi_sec_scraper.processor.pipeline.save_json",
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
        mock_save = mocker.patch("idi_sec_scraper.processor.pipeline.save_content")
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
        mock_save_json = mocker.patch("idi_sec_scraper.processor.pipeline.save_json")
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
            "idi_sec_scraper.processor.pipeline.parse_index_htm",
            return_value=(_make_parsed_filing([_DOC]), None),
        )
        mock_save_json = mocker.patch("idi_sec_scraper.processor.pipeline.save_json")
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
            "idi_sec_scraper.processor.pipeline.parse_index_htm",
            return_value=(parsed, None),
        )
        mock_save_json = mocker.patch("idi_sec_scraper.processor.pipeline.save_json")
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
        mock_save = mocker.patch("idi_sec_scraper.processor.pipeline.save_content")
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

    def test_document_in_manifest_is_skipped(self, mocker):
        doc = ParsedDocument(
            seq="1",
            description="8-K",
            filename="report.htm",
            type="8-K",
            url="https://www.sec.gov/Archives/edgar/data/320193/000114036126006577/report.htm",
        )
        existing_manifest = {
            "cik": "320193",
            "accession_number": "0001140361-26-006577",
            "form_type": "8-K",
            "filing_date": "2026-02-24",
            "last_scraped_at": "2026-02-24T00:00:00+00:00",
            "documents": [
                {
                    "seq": "1",
                    "description": "8-K",
                    "filename": "report.htm",
                    "type": "8-K",
                    "s3_key": "s3://bucket/report.htm",
                    "url": "https://www.sec.gov/Archives/edgar/data/320193/000114036126006577/report.htm",
                }
            ],
        }
        _patch_scrape_deps(mocker, docs=[doc], cached=True)
        mocker.patch("idi_sec_scraper.processor.pipeline.load_json", return_value=existing_manifest)
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

        assert pipeline.stats.skipped_documents == 1
        assert pipeline.sec_client.query_endpoint.call_count == 0

    def test_reads_from_s3_when_manifest_cached_historical(self, mocker):
        _patch_scrape_deps(mocker, cached=True)
        mock_load_content = mocker.patch(
            "idi_sec_scraper.processor.pipeline.load_content", return_value="<html/>"
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
            "idi_sec_scraper.processor.pipeline.load_content", return_value="<html/>"
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
            "idi_sec_scraper.processor.pipeline.select_and_filter_documents",
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
            "idi_sec_scraper.processor.pipeline.parse_index_htm",
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
            "idi_sec_scraper.processor.pipeline.parse_index_htm",
            return_value=(_make_parsed_filing([]), FailureType.CIK_MISMATCH),
        )
        mock_save_json = mocker.patch("idi_sec_scraper.processor.pipeline.save_json")
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
        mock_save_json = mocker.patch("idi_sec_scraper.processor.pipeline.save_json")
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
        assert pipeline.stats.skipped_filings == 1
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

        assert pipeline.stats.skipped_filings == 1
        assert pipeline.stats.failed_filings == 0
        assert pipeline.stats.total_filings == 1

    def test_successful_filing_counted(self, mocker):
        from idi_sec_scraper.processor.document_filters import DocumentFilterConfig

        mocker.patch(
            "idi_sec_scraper.processor.pipeline.load_document_filters",
            return_value=DocumentFilterConfig(form_types={"8-K": FormTypeConfig(match="8-K")}),
        )
        _patch_scrape_deps(mocker, docs=[_DOC])
        sec_client = mocker.MagicMock()
        sec_client.SEC_HEADERS = {}
        pipeline = DailySECScraperPipeline(
            DailyPipelineConfig(bucket=_BUCKET, document_filters_path=_FILTERS_PATH),
            sec_client,
        )
        pipeline.sec_client.query_endpoint.return_value = {"status_code": 200, "data": "<html/>"}

        results = pipeline.process([_FILING])

        assert len(results) == 1
        assert pipeline.stats.scraped_filings == 1
        assert pipeline.stats.failed_filings == 0
        assert pipeline.stats.form_type_filings_total["8-K"] == 1
        assert pipeline.stats.form_type_documents_total["8-K"] == 1

    def test_failed_filing_counted(self, mocker):
        from idi_sec_scraper.processor.document_filters import DocumentFilterConfig

        mocker.patch(
            "idi_sec_scraper.processor.pipeline.load_document_filters",
            return_value=DocumentFilterConfig(form_types={"8-K": FormTypeConfig(match="8-K")}),
        )
        _patch_scrape_deps(mocker)
        sec_client = mocker.MagicMock()
        sec_client.SEC_HEADERS = {}
        pipeline = DailySECScraperPipeline(
            DailyPipelineConfig(bucket=_BUCKET, document_filters_path=_FILTERS_PATH),
            sec_client,
        )
        pipeline.sec_client.query_endpoint.return_value = {"status_code": 404, "error": "Not Found"}

        results = pipeline.process([_FILING])

        assert results == []
        assert pipeline.stats.failed_filings == 1
        assert pipeline.stats.skipped_filings == 0
        assert pipeline.stats.form_type_filings_total["8-K"] == 1

    def test_fully_cached_filing_counted_as_skipped_not_scraped(self, mocker):
        existing_manifest = {
            "cik": "320193",
            "accession_number": "0001140361-26-006577",
            "form_type": "8-K",
            "filing_date": "2026-02-24",
            "last_scraped_at": "2026-02-24T00:00:00+00:00",
            "index_url": _FILING.url,
            "company_name": "Apple Inc.",
            "documents": [
                {
                    "seq": "1",
                    "description": "8-K",
                    "filename": "report.htm",
                    "type": "8-K",
                    "s3_key": "s3://bucket/report.htm",
                    "url": _DOC.url,
                }
            ],
        }
        _patch_scrape_deps(mocker, docs=[_DOC], cached=True)
        mocker.patch("idi_sec_scraper.processor.pipeline.load_json", return_value=existing_manifest)
        pipeline = _make_pipeline(
            mocker,
            HistoricalSECScraperPipeline,
            HistoricalPipelineConfig(
                bucket=_BUCKET,
                document_filters_path=_FILTERS_PATH,
                submissions_url="s3://x/s.zip",
            ),
        )

        results = pipeline.process([_FILING])

        assert results == []
        assert pipeline.stats.skipped_filings == 1
        assert pipeline.stats.scraped_filings == 0
        assert pipeline.stats.failed_filings == 0
        pipeline.sec_client.query_endpoint.assert_not_called()

    def test_partially_cached_filing_counted_as_scraped(self, mocker):
        """Index cached but one new document → not fully cached, counts as scraped."""
        existing_manifest = {
            "cik": "320193",
            "accession_number": "0001140361-26-006577",
            "form_type": "8-K",
            "filing_date": "2026-02-24",
            "last_scraped_at": "2026-02-24T00:00:00+00:00",
            "index_url": _FILING.url,
            "company_name": "Apple Inc.",
            "documents": [],  # no existing docs → report.htm will be newly fetched
        }
        _patch_scrape_deps(mocker, docs=[_DOC], cached=True)
        mocker.patch("idi_sec_scraper.processor.pipeline.load_json", return_value=existing_manifest)
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

        results = pipeline.process([_FILING])

        assert len(results) == 1
        assert pipeline.stats.scraped_filings == 1
        assert pipeline.stats.skipped_filings == 0
