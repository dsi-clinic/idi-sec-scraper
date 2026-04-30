"""Tests for processor.discovery — DailyDiscovery and HistoricalDiscovery."""

# Standard library imports
import datetime
import io
import json
import zipfile

# Application imports
from idi_sec_scraper.processor.discovery import (
    DailyDiscovery,
    Discovery,
    HistoricalDiscovery,
    _accession_from_url,
    _crawler_idx_url,
    _find_form_type_col,
    _matches_form_types,
    _parse_yyyymmdd,
)
from idi_sec_scraper.processor.types import DiscoveredFiling

# Minimal crawler.idx fixture matching the real SEC format.
# "Form Type" header label determines the column split point dynamically.
_SAMPLE_IDX = (
    "Description:           Daily Crawler Index\n"
    "Last Data Received:    Apr 1, 2026\n"
    "\n"
    "\n"
    "Company Name                                                  Form Type   CIK\n"
    "      Date Filed  URL \n"
    "-----------------------------------------------------------------------------------\n"
    "20/20 Biolabs, Inc.                                           8-K              1139685     20260401    http://www.sec.gov/Archives/edgar/data/1139685/0001213900-26-037770-index.htm\n"
    "180 DEGREE CAPITAL CORP. /NY/                                 N-8F/A           893739      20260401    http://www.sec.gov/Archives/edgar/data/893739/0000893739-26-000004-index.htm\n"
    "Apple Inc.                                                    10-K             320193      20260401    http://www.sec.gov/Archives/edgar/data/320193/0000320193-26-000123-index.htm\n"
    "Apple Inc.                                                    10-K/A           320193      20260401    http://www.sec.gov/Archives/edgar/data/320193/0000320193-26-000124-index.htm\n"
    "Some Fund                                                     13F-HR           999999      20260401    http://www.sec.gov/Archives/edgar/data/999999/0000999999-26-000001-index.htm\n"
    "Acme Holdings Corp.                                           SCHEDULE 13G/A   888888      20260401    http://www.sec.gov/Archives/edgar/data/888888/0000888888-26-000001-index.htm\n"
)


def _make_sec_client(mocker, content: str = _SAMPLE_IDX):
    client = mocker.MagicMock()
    client.SEC_HEADERS = {}
    client.query_endpoint.return_value = {"status_code": 200, "data": content}
    return client


class TestDiscoverDaily:
    """Tests for DailyDiscovery."""

    def test_returns_list_of_discovered_filings(self, mocker):
        client = _make_sec_client(mocker)
        result = DailyDiscovery(client, ["8-K"]).discover(
            datetime.date(2026, 4, 1), datetime.date(2026, 4, 1)
        )

        assert all(isinstance(f, DiscoveredFiling) for f in result)

    def test_filters_by_exact_form_type(self, mocker):
        client = _make_sec_client(mocker)
        result = DailyDiscovery(client, ["8-K"]).discover(
            datetime.date(2026, 4, 1), datetime.date(2026, 4, 1)
        )

        assert len(result) == 1
        assert result[0].form_type == "8-K"

    def test_filters_by_regex_form_type(self, mocker):
        client = _make_sec_client(mocker)
        result = DailyDiscovery(client, ["10-?K"]).discover(
            datetime.date(2026, 4, 1), datetime.date(2026, 4, 1)
        )

        assert len(result) == 2
        assert {f.form_type for f in result} == {"10-K", "10-K/A"}

    def test_form_type_with_space(self, mocker):
        client = _make_sec_client(mocker)
        result = DailyDiscovery(client, ["SCHEDULE 13G.*"]).discover(
            datetime.date(2026, 4, 1), datetime.date(2026, 4, 1)
        )

        assert len(result) == 1
        assert result[0].form_type == "SCHEDULE 13G/A"

    def test_multiple_patterns_match_union(self, mocker):
        client = _make_sec_client(mocker)
        result = DailyDiscovery(client, ["8-K", "13F-HR"]).discover(
            datetime.date(2026, 4, 1), datetime.date(2026, 4, 1)
        )

        assert len(result) == 2
        assert {f.form_type for f in result} == {"8-K", "13F-HR"}

    def test_no_matches_returns_empty_list(self, mocker):
        client = _make_sec_client(mocker)
        result = DailyDiscovery(client, ["20-F"]).discover(
            datetime.date(2026, 4, 1), datetime.date(2026, 4, 1)
        )

        assert result == []

    def test_no_header_returns_empty_list(self, mocker):
        client = _make_sec_client(mocker, content="no header here\nsome data line\n")
        result = DailyDiscovery(client, ["8-K"]).discover(
            datetime.date(2026, 4, 1), datetime.date(2026, 4, 1)
        )

        assert result == []

    def test_filing_fields(self, mocker):
        client = _make_sec_client(mocker)
        result = DailyDiscovery(client, ["8-K"]).discover(
            datetime.date(2026, 4, 1), datetime.date(2026, 4, 1)
        )

        f = result[0]
        assert f.cik == "1139685"
        assert f.accession_number == "0001213900-26-037770"
        assert f.form_type == "8-K"
        assert f.filing_date == datetime.date(2026, 4, 1)
        assert (
            f.url == "http://www.sec.gov/Archives/edgar/data/1139685/0001213900-26-037770-index.htm"
        )
        assert f.company_name == "20/20 Biolabs, Inc."

    def test_cik_is_not_padded(self, mocker):
        client = _make_sec_client(mocker)
        result = DailyDiscovery(client, ["10-?K"]).discover(
            datetime.date(2026, 4, 1), datetime.date(2026, 4, 1)
        )

        assert result[0].cik == "320193"

    def test_filing_date_is_date_object(self, mocker):
        client = _make_sec_client(mocker)
        result = DailyDiscovery(client, ["8-K"]).discover(
            datetime.date(2026, 4, 1), datetime.date(2026, 4, 1)
        )

        assert isinstance(result[0].filing_date, datetime.date)

    def test_header_and_separator_lines_skipped(self, mocker):
        client = _make_sec_client(mocker)
        result = DailyDiscovery(client, [".*"]).discover(
            datetime.date(2026, 4, 1), datetime.date(2026, 4, 1)
        )

        # 6 data rows match ".*" — header/separator lines must be excluded
        assert len(result) == 6

    def test_empty_response_returns_empty_list(self, mocker):
        client = _make_sec_client(mocker, content="")
        result = DailyDiscovery(client, ["8-K"]).discover(
            datetime.date(2026, 4, 1), datetime.date(2026, 4, 1)
        )

        assert result == []

    def test_date_range_aggregates_results(self, mocker):
        client = _make_sec_client(mocker)
        result = DailyDiscovery(client, ["8-K"]).discover(
            datetime.date(2026, 4, 1), datetime.date(2026, 4, 3)
        )

        # Same mock content returned for each of the 3 days → 3 × 1 match
        assert len(result) == 3

    def test_date_range_results_ordered_by_date_ascending(self, mocker):
        apr2_idx = _SAMPLE_IDX.replace("20260401", "20260402")
        client = mocker.MagicMock()
        client.SEC_HEADERS = {}
        client.query_endpoint.side_effect = [
            {"status_code": 200, "data": _SAMPLE_IDX},
            {"status_code": 200, "data": apr2_idx},
        ]
        result = DailyDiscovery(client, ["8-K"]).discover(
            datetime.date(2026, 4, 1), datetime.date(2026, 4, 2)
        )

        assert result[0].filing_date == datetime.date(2026, 4, 1)
        assert result[1].filing_date == datetime.date(2026, 4, 2)

    def test_missing_day_is_skipped_gracefully(self, mocker):
        client = mocker.MagicMock()
        client.SEC_HEADERS = {}
        client.query_endpoint.side_effect = [
            {"status_code": 200, "data": _SAMPLE_IDX},
            {"status_code": 404, "error": "HTTP 404"},
            {"status_code": 200, "data": _SAMPLE_IDX},
        ]
        result = DailyDiscovery(client, ["8-K"]).discover(
            datetime.date(2026, 4, 1), datetime.date(2026, 4, 3)
        )

        assert len(result) == 2

    def test_missing_day_logs_warning(self, mocker):
        client = mocker.MagicMock()
        client.SEC_HEADERS = {}
        client.query_endpoint.return_value = {"status_code": 404, "error": "HTTP 404"}
        mock_logger = mocker.patch("idi_sec_scraper.processor.discovery._logger")
        DailyDiscovery(client, ["8-K"]).discover(
            datetime.date(2026, 4, 1), datetime.date(2026, 4, 1)
        )

        mock_logger.warning.assert_called_once()

    def test_end_date_before_start_returns_empty(self, mocker):
        client = _make_sec_client(mocker)
        result = DailyDiscovery(client, ["8-K"]).discover(
            datetime.date(2026, 4, 3), datetime.date(2026, 4, 1)
        )

        assert result == []


class TestFindFormTypeCol:
    """Tests for _find_form_type_col()."""

    def test_finds_column_from_header(self):
        lines = [
            "Description: Daily Index",
            "Company Name                                                  Form Type   CIK",
            "      Date Filed  URL",
        ]
        assert _find_form_type_col(lines) == 62

    def test_returns_none_when_header_absent(self):
        assert _find_form_type_col(["no header here"]) is None

    def test_returns_none_for_empty_input(self):
        assert _find_form_type_col([]) is None


class TestCrawlerIdxUrl:
    """Tests for _crawler_idx_url()."""

    def test_q1(self):
        assert "QTR1" in _crawler_idx_url(datetime.date(2026, 1, 15))

    def test_q2(self):
        assert "QTR2" in _crawler_idx_url(datetime.date(2026, 4, 1))

    def test_q3(self):
        assert "QTR3" in _crawler_idx_url(datetime.date(2026, 7, 1))

    def test_q4(self):
        assert "QTR4" in _crawler_idx_url(datetime.date(2026, 10, 1))

    def test_date_formatted_as_yyyymmdd(self):
        assert "20260401" in _crawler_idx_url(datetime.date(2026, 4, 1))

    def test_year_in_url(self):
        assert "/2026/" in _crawler_idx_url(datetime.date(2026, 4, 1))


class TestHelpers:
    """Tests for module-level helper functions."""

    def test_parse_yyyymmdd(self):
        assert _parse_yyyymmdd("20260401") == datetime.date(2026, 4, 1)

    def test_accession_from_url(self):
        url = "http://www.sec.gov/Archives/edgar/data/1139685/0001213900-26-037770-index.htm"
        assert _accession_from_url(url) == "0001213900-26-037770"

    def test_matches_form_types_exact(self):
        assert _matches_form_types("8-K", ["8-K"])

    def test_matches_form_types_regex(self):
        assert _matches_form_types("10-K", ["10-?K"])
        assert _matches_form_types("10K", ["10-?K"])

    def test_matches_form_types_prefix_match(self):
        assert _matches_form_types("10-K/A", ["10-?K"])

    def test_matches_form_types_with_space(self):
        assert _matches_form_types("SCHEDULE 13G/A", ["SCHEDULE 13G.*"])

    def test_matches_form_types_no_match(self):
        assert not _matches_form_types("20-F", ["8-K", "10-?K"])


# ---------------------------------------------------------------------------
# Helpers for HistoricalDiscovery tests
# ---------------------------------------------------------------------------


def _make_zip(files: dict[str, dict]) -> bytes:
    """Build an in-memory zip whose entries are JSON-encoded dicts."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, json.dumps(data))
    return buf.getvalue()


def _make_historical_client(mocker, zip_bytes: bytes):
    client = mocker.MagicMock()
    client.SEC_HEADERS = {}

    def fake_open_zip(path, headers=None):
        import contextlib

        @contextlib.contextmanager
        def _cm():
            yield zipfile.ZipFile(io.BytesIO(zip_bytes))

        return _cm()

    mocker.patch(
        "idi_sec_scraper.processor.discovery.open_zip",
        side_effect=lambda path, headers=None: fake_open_zip(path, headers),
    )
    return client


_APPLE_CIK_JSON = {
    "cik": "320193",
    "name": "Apple Inc.",
    "filings": {
        "recent": {
            "form": ["10-K", "8-K", "10-K/A"],
            "accessionNumber": [
                "0000320193-26-000001",
                "0000320193-26-000002",
                "0000320193-26-000003",
            ],
            "filingDate": ["2026-01-15", "2026-02-01", "2026-03-01"],
        },
        "files": [],
    },
}

_OVERFLOW_JSON = {
    "form": ["10-K"],
    "accessionNumber": ["0000320193-25-000099"],
    "filingDate": ["2025-11-01"],
}

_APPLE_WITH_OVERFLOW = {
    "cik": "320193",
    "name": "Apple Inc.",
    "filings": {
        "recent": {
            "form": ["8-K"],
            "accessionNumber": ["0000320193-26-000002"],
            "filingDate": ["2026-02-01"],
        },
        "files": [{"name": "CIK0000320193-submissions-001.json"}],
    },
}


class TestDiscoverHistorical:
    """Tests for HistoricalDiscovery."""

    def test_returns_list_of_discovered_filings(self, mocker):
        zb = _make_zip({"CIK0000320193.json": _APPLE_CIK_JSON})
        client = _make_historical_client(mocker, zb)
        result = HistoricalDiscovery(client, ["10-?K"]).discover("fake://path.zip")

        assert all(isinstance(f, DiscoveredFiling) for f in result)

    def test_filters_by_form_type(self, mocker):
        zb = _make_zip({"CIK0000320193.json": _APPLE_CIK_JSON})
        client = _make_historical_client(mocker, zb)
        result = HistoricalDiscovery(client, ["10-?K"]).discover("fake://path.zip")

        assert len(result) == 2
        assert {f.form_type for f in result} == {"10-K", "10-K/A"}

    def test_no_matches_returns_empty(self, mocker):
        zb = _make_zip({"CIK0000320193.json": _APPLE_CIK_JSON})
        client = _make_historical_client(mocker, zb)
        result = HistoricalDiscovery(client, ["20-F"]).discover("fake://path.zip")

        assert result == []

    def test_filing_fields(self, mocker):
        zb = _make_zip({"CIK0000320193.json": _APPLE_CIK_JSON})
        client = _make_historical_client(mocker, zb)
        result = HistoricalDiscovery(client, ["10-K"]).discover("fake://path.zip")

        f = next(r for r in result if r.accession_number == "0000320193-26-000001")
        assert f.cik == "320193"
        assert f.form_type == "10-K"
        assert f.filing_date == datetime.date(2026, 1, 15)
        assert f.company_name == "Apple Inc."
        assert (
            f.url
            == "https://www.sec.gov/Archives/edgar/data/320193/000032019326000001/0000320193-26-000001-index.htm"
        )

    def test_cik_is_not_padded(self, mocker):
        zb = _make_zip({"CIK0000320193.json": _APPLE_CIK_JSON})
        client = _make_historical_client(mocker, zb)
        result = HistoricalDiscovery(client, ["10-?K"]).discover("fake://path.zip")

        assert all(f.cik == "320193" for f in result)

    def test_filing_date_is_date_object(self, mocker):
        zb = _make_zip({"CIK0000320193.json": _APPLE_CIK_JSON})
        client = _make_historical_client(mocker, zb)
        result = HistoricalDiscovery(client, ["10-K"]).discover("fake://path.zip")

        assert all(isinstance(f.filing_date, datetime.date) for f in result)

    def test_overflow_files_are_skipped_as_top_level(self, mocker):
        zb = _make_zip(
            {
                "CIK0000320193.json": _APPLE_CIK_JSON,
                "CIK0000320193-submissions-001.json": _OVERFLOW_JSON,
            }
        )
        client = _make_historical_client(mocker, zb)
        result = HistoricalDiscovery(client, ["10-?K"]).discover("fake://path.zip")

        # Overflow is NOT processed as a top-level entry — only 2 matches from main file
        assert len(result) == 2

    def test_overflow_filings_are_included(self, mocker):
        zb = _make_zip(
            {
                "CIK0000320193.json": _APPLE_WITH_OVERFLOW,
                "CIK0000320193-submissions-001.json": _OVERFLOW_JSON,
            }
        )
        client = _make_historical_client(mocker, zb)
        result = HistoricalDiscovery(client, ["10-?K"]).discover("fake://path.zip")

        assert len(result) == 1
        assert result[0].accession_number == "0000320193-25-000099"

    def test_non_cik_files_are_skipped(self, mocker):
        zb = _make_zip(
            {
                "CIK0000320193.json": _APPLE_CIK_JSON,
                "README.txt": {},
            }
        )
        client = _make_historical_client(mocker, zb)
        result = HistoricalDiscovery(client, [".*"]).discover("fake://path.zip")

        assert all(f.company_name == "Apple Inc." for f in result)

    def test_multiple_cik_files(self, mocker):
        other = {
            "cik": "789012",
            "name": "Other Corp.",
            "filings": {
                "recent": {
                    "form": ["10-K"],
                    "accessionNumber": ["0000789012-26-000001"],
                    "filingDate": ["2026-01-20"],
                },
                "files": [],
            },
        }
        zb = _make_zip(
            {
                "CIK0000320193.json": _APPLE_CIK_JSON,
                "CIK0000789012.json": other,
            }
        )
        client = _make_historical_client(mocker, zb)
        result = HistoricalDiscovery(client, ["10-K"]).discover("fake://path.zip")

        assert len(result) == 3
        assert {f.cik for f in result} == {"320193", "789012"}

    def test_mismatched_list_lengths_returns_empty(self, mocker):
        bad = {
            "cik": "111111",
            "name": "Bad Corp.",
            "filings": {
                "recent": {
                    "form": ["10-K", "8-K"],
                    "accessionNumber": ["0000111111-26-000001"],  # length mismatch
                    "filingDate": ["2026-01-01"],
                },
                "files": [],
            },
        }
        zb = _make_zip({"CIK0000111111.json": bad})
        client = _make_historical_client(mocker, zb)
        result = HistoricalDiscovery(client, [".*"]).discover("fake://path.zip")

        assert result == []

    def test_mismatched_list_lengths_logs_error(self, mocker):
        bad = {
            "cik": "111111",
            "name": "Bad Corp.",
            "filings": {
                "recent": {
                    "form": ["10-K", "8-K"],
                    "accessionNumber": ["0000111111-26-000001"],
                    "filingDate": ["2026-01-01"],
                },
                "files": [],
            },
        }
        zb = _make_zip({"CIK0000111111.json": bad})
        client = _make_historical_client(mocker, zb)
        mock_logger = mocker.patch("idi_sec_scraper.processor.discovery._logger")
        HistoricalDiscovery(client, [".*"]).discover("fake://path.zip")

        mock_logger.error.assert_called_once()
        assert "mismatched" in mock_logger.error.call_args.args[0]

    def test_mismatched_list_lengths_registers_failure(self, mocker):
        from idi_sec_scraper.common.failures import FailureRegistry
        from idi_sec_scraper.processor.failures import SECScraperFailureClassifier

        bad = {
            "cik": "111111",
            "name": "Bad Corp.",
            "filings": {
                "recent": {
                    "form": ["10-K", "8-K"],
                    "accessionNumber": ["0000111111-26-000001"],
                    "filingDate": ["2026-01-01"],
                },
                "files": [],
            },
        }
        zb = _make_zip({"CIK0000111111.json": bad})
        client = _make_historical_client(mocker, zb)
        registry = FailureRegistry("", SECScraperFailureClassifier())
        HistoricalDiscovery(client, [".*"], registry).discover("fake://path.zip")

        assert ("111111", "CIK0000111111.json") in registry._entries

    def test_known_mismatched_cik_file_is_skipped(self, mocker):
        from idi_sec_scraper.common.failures import FailureRegistry
        from idi_sec_scraper.processor.failures import SECScraperFailureClassifier

        zb = _make_zip({"CIK0000320193.json": _APPLE_CIK_JSON})
        client = _make_historical_client(mocker, zb)
        registry = FailureRegistry("", SECScraperFailureClassifier())
        registry._entries.add(("320193", "CIK0000320193.json"))

        result = HistoricalDiscovery(client, ["10-K"], registry).discover("fake://path.zip")

        assert result == []

    def test_missing_overflow_file_registers_failure(self, mocker):
        from idi_sec_scraper.common.failures import FailureRegistry
        from idi_sec_scraper.processor.failures import SECScraperFailureClassifier

        data = {
            "cik": "320193",
            "name": "Apple Inc.",
            "filings": {
                "recent": {"form": [], "accessionNumber": [], "filingDate": []},
                "files": [{"name": "CIK0000320193-submissions-missing.json"}],
            },
        }
        zb = _make_zip({"CIK0000320193.json": data})
        client = _make_historical_client(mocker, zb)
        registry = FailureRegistry("", SECScraperFailureClassifier())
        HistoricalDiscovery(client, [".*"], registry).discover("fake://path.zip")

        assert ("320193", "CIK0000320193-submissions-missing.json") in registry._entries

    def test_known_missing_overflow_file_is_skipped(self, mocker):
        from idi_sec_scraper.common.failures import FailureRegistry
        from idi_sec_scraper.processor.failures import SECScraperFailureClassifier

        data = {
            "cik": "320193",
            "name": "Apple Inc.",
            "filings": {
                "recent": {"form": [], "accessionNumber": [], "filingDate": []},
                "files": [{"name": "CIK0000320193-submissions-missing.json"}],
            },
        }
        zb = _make_zip({"CIK0000320193.json": data})
        client = _make_historical_client(mocker, zb)
        registry = FailureRegistry("", SECScraperFailureClassifier())
        registry._entries.add(("320193", "CIK0000320193-submissions-missing.json"))
        mock_logger = mocker.patch("idi_sec_scraper.processor.discovery._logger")
        HistoricalDiscovery(client, [".*"], registry).discover("fake://path.zip")

        # warning about skipping, not error about missing
        warning_msgs = [str(c) for c in mock_logger.warning.call_args_list]
        assert any("known" in m for m in warning_msgs)
        mock_logger.error.assert_not_called()

    def test_missing_overflow_file_logs_error(self, mocker):
        data = {
            "cik": "320193",
            "name": "Apple Inc.",
            "filings": {
                "recent": {
                    "form": [],
                    "accessionNumber": [],
                    "filingDate": [],
                },
                "files": [{"name": "CIK0000320193-submissions-missing.json"}],
            },
        }
        zb = _make_zip({"CIK0000320193.json": data})
        client = _make_historical_client(mocker, zb)
        mock_logger = mocker.patch("idi_sec_scraper.processor.discovery._logger")
        HistoricalDiscovery(client, [".*"]).discover("fake://path.zip")

        mock_logger.error.assert_called_once()
        assert "not found" in mock_logger.error.call_args.args[0]


class TestHistoricalDiscoverMaxCiks:
    """Tests for HistoricalDiscovery.discover() max_ciks parameter."""

    def test_max_ciks_limits_cik_files_processed(self, mocker):
        zb = _make_zip(
            {
                "CIK0000000001.json": {**_APPLE_CIK_JSON, "cik": "1", "name": "Co A"},
                "CIK0000000002.json": {**_APPLE_CIK_JSON, "cik": "2", "name": "Co B"},
                "CIK0000000003.json": {**_APPLE_CIK_JSON, "cik": "3", "name": "Co C"},
            }
        )
        client = _make_historical_client(mocker, zb)
        result = HistoricalDiscovery(client, [".*"]).discover("fake://path.zip", max_ciks=2)

        ciks = {f.cik for f in result}
        assert len(ciks) == 2

    def test_max_ciks_none_processes_all(self, mocker):
        zb = _make_zip(
            {
                "CIK0000000001.json": {**_APPLE_CIK_JSON, "cik": "1", "name": "Co A"},
                "CIK0000000002.json": {**_APPLE_CIK_JSON, "cik": "2", "name": "Co B"},
                "CIK0000000003.json": {**_APPLE_CIK_JSON, "cik": "3", "name": "Co C"},
            }
        )
        client = _make_historical_client(mocker, zb)
        result = HistoricalDiscovery(client, [".*"]).discover("fake://path.zip", max_ciks=None)

        ciks = {f.cik for f in result}
        assert len(ciks) == 3

    def test_max_ciks_excludes_non_cik_files_from_cap(self, mocker):
        zb = _make_zip(
            {
                "CIK0000000001.json": {**_APPLE_CIK_JSON, "cik": "1", "name": "Co A"},
                "other-metadata.json": {},
                "CIK0000000002.json": {**_APPLE_CIK_JSON, "cik": "2", "name": "Co B"},
            }
        )
        client = _make_historical_client(mocker, zb)
        result = HistoricalDiscovery(client, [".*"]).discover("fake://path.zip", max_ciks=1)

        ciks = {f.cik for f in result}
        assert len(ciks) == 1


class TestDiscoveryABC:
    """Tests for the Discovery ABC hierarchy."""

    def test_daily_discovery_is_subclass(self):
        assert issubclass(DailyDiscovery, Discovery)

    def test_historical_discovery_is_subclass(self):
        assert issubclass(HistoricalDiscovery, Discovery)

    def test_daily_discovery_is_instance(self, mocker):
        client = mocker.MagicMock()
        assert isinstance(DailyDiscovery(client, []), Discovery)

    def test_historical_discovery_is_instance(self, mocker):
        client = mocker.MagicMock()
        assert isinstance(HistoricalDiscovery(client, []), Discovery)
