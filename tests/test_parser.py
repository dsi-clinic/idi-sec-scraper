"""Tests for processor.parser — parse_index_htm and validate_parsed_filing."""

# Standard library imports
import dataclasses
import datetime

from idi_ftm2j_shared.types import DiscoveredFiling

# Application imports
from idi_sec_scraper.failures import FailureType
from idi_sec_scraper.parser import parse_index_htm, validate_parsed_filing
from idi_sec_scraper.types import ParsedFiling

_SAMPLE_URL = "https://www.sec.gov/Archives/edgar/data/320193/000114036126006577/0001140361-26-006577-index.htm"

_SAMPLE_FILING = DiscoveredFiling(
    cik="320193",
    accession_number="0001140361-26-006577",
    form_type="8-K",
    filing_date=datetime.date(2026, 2, 24),
    url=_SAMPLE_URL,
    company_name="Apple Inc.",
)

_SAMPLE_HTML = """
<!DOCTYPE HTML>
<html>
<head><title>EDGAR Filing Documents for 0001140361-26-006577</title></head>
<body>
<div id="formHeader">
  <div id="formName">
    <strong>Form 8-K</strong> - Current report:
  </div>
  <div id="secNum">
    <strong>SEC Accession No.</strong> 0001140361-26-006577
  </div>
</div>

<div class="formGrouping">
  <div class="infoHead">Filing Date</div>
  <div class="info">2026-02-24</div>
  <div class="infoHead">Period of Report</div>
  <div class="info">2026-02-24</div>
  <div class="infoHead">Accepted</div>
  <div class="info">2026-02-24 16:55:58</div>
</div>

<div class="formDiv">
  <p>Document Format Files</p>
  <table class="tableFile" summary="Document Format Files">
    <tr>
      <th>Seq</th><th>Description</th><th>Document</th><th>Type</th><th>Size</th>
    </tr>
    <tr>
      <td>1</td>
      <td>8-K</td>
      <td><a href="/ix?doc=/Archives/edgar/data/320193/000114036126006577/ef20060722_8k.htm">ef20060722_8k.htm</a></td>
      <td>8-K</td>
      <td>73012</td>
    </tr>
    <tr>
      <td>2</td>
      <td>EXHIBIT 10.1</td>
      <td><a href="/Archives/edgar/data/320193/000114036126006577/ef20060722_ex10-1.htm">ef20060722_ex10-1.htm</a></td>
      <td>EX-10.1</td>
      <td>74614</td>
    </tr>
    <tr>
      <td>&nbsp;</td>
      <td>Complete submission text file</td>
      <td><a href="/Archives/edgar/data/320193/000114036126006577/0001140361-26-006577.txt">0001140361-26-006577.txt</a></td>
      <td>&nbsp;</td>
      <td>415164</td>
    </tr>
  </table>
</div>

<div class="formDiv">
  <p>Data Files</p>
  <table class="tableFile" summary="Data Files">
    <tr>
      <th>Seq</th><th>Description</th><th>Document</th><th>Type</th><th>Size</th>
    </tr>
    <tr>
      <td>4</td>
      <td>XBRL TAXONOMY EXTENSION SCHEMA</td>
      <td><a href="/Archives/edgar/data/320193/000114036126006577/aapl-20260224.xsd">aapl-20260224.xsd</a></td>
      <td>EX-101.SCH</td>
      <td>5321</td>
    </tr>
  </table>
</div>

<div class="companyInfo">
  <span class="companyName">Apple Inc. (Filer)
    <acronym title="Central Index Key">CIK</acronym>:
    <a href="/cgi-bin/browse-edgar?CIK=0000320193&amp;action=getcompany">0000320193 (see all company filings)</a>
  </span>
</div>

</body>
</html>
"""


class TestParseIndexHtm:
    """Tests for parse_index_htm()."""

    def test_returns_parsed_filing_and_no_failure(self):
        parsed, failure = parse_index_htm(_SAMPLE_HTML, _SAMPLE_FILING)

        assert isinstance(parsed, ParsedFiling)
        assert failure is None

    def test_returns_url(self):
        parsed, _ = parse_index_htm(_SAMPLE_HTML, _SAMPLE_FILING)

        assert parsed.url == _SAMPLE_URL

    def test_extracts_form_type(self):
        parsed, _ = parse_index_htm(_SAMPLE_HTML, _SAMPLE_FILING)

        assert parsed.form_type == "8-K"

    def test_extracts_accession_number(self):
        parsed, _ = parse_index_htm(_SAMPLE_HTML, _SAMPLE_FILING)

        assert parsed.accession_number == "0001140361-26-006577"

    def test_extracts_filing_date(self):
        parsed, _ = parse_index_htm(_SAMPLE_HTML, _SAMPLE_FILING)

        assert parsed.filing_date == datetime.date(2026, 2, 24)

    def test_extracts_report_date(self):
        parsed, _ = parse_index_htm(_SAMPLE_HTML, _SAMPLE_FILING)

        assert parsed.report_date == datetime.date(2026, 2, 24)

    def test_extracts_cik(self):
        parsed, _ = parse_index_htm(_SAMPLE_HTML, _SAMPLE_FILING)

        assert parsed.cik == "320193"

    def test_last_scraped_at_is_utc_datetime(self):
        parsed, _ = parse_index_htm(_SAMPLE_HTML, _SAMPLE_FILING)

        assert isinstance(parsed.last_scraped_at, datetime.datetime)
        assert parsed.last_scraped_at.tzinfo == datetime.UTC

    def test_documents_from_both_tables(self):
        parsed, _ = parse_index_htm(_SAMPLE_HTML, _SAMPLE_FILING)

        # 3 from Document Format Files + 1 from Data Files
        assert len(parsed.available_documents) == 4

    def test_ixbrl_url_stripped_and_absolute(self):
        parsed, _ = parse_index_htm(_SAMPLE_HTML, _SAMPLE_FILING)

        ixbrl_doc = parsed.available_documents[0]
        assert ixbrl_doc.filename == "ef20060722_8k.htm"
        assert (
            ixbrl_doc.url
            == "https://www.sec.gov/Archives/edgar/data/320193/000114036126006577/ef20060722_8k.htm"
        )

    def test_standard_url_is_absolute(self):
        parsed, _ = parse_index_htm(_SAMPLE_HTML, _SAMPLE_FILING)

        exhibit_doc = parsed.available_documents[1]
        assert (
            exhibit_doc.url
            == "https://www.sec.gov/Archives/edgar/data/320193/000114036126006577/ef20060722_ex10-1.htm"
        )

    def test_document_fields(self):
        parsed, _ = parse_index_htm(_SAMPLE_HTML, _SAMPLE_FILING)

        doc = parsed.available_documents[1]
        assert doc.seq == "2"
        assert doc.description == "EXHIBIT 10.1"
        assert doc.filename == "ef20060722_ex10-1.htm"
        assert doc.type == "EX-10.1"

    def test_empty_seq_and_type_for_nbsp_rows(self):
        parsed, _ = parse_index_htm(_SAMPLE_HTML, _SAMPLE_FILING)

        complete_submission = parsed.available_documents[2]
        assert complete_submission.seq == ""
        assert complete_submission.type == ""
        assert complete_submission.description == "Complete submission text file"

    def test_data_files_table_included(self):
        parsed, _ = parse_index_htm(_SAMPLE_HTML, _SAMPLE_FILING)

        xbrl_doc = parsed.available_documents[3]
        assert xbrl_doc.seq == "4"
        assert xbrl_doc.type == "EX-101.SCH"
        assert xbrl_doc.filename == "aapl-20260224.xsd"


_MULTI_FILER_HTML = _SAMPLE_HTML.replace(
    # Replace the single companyInfo block with two: primary filer (899689) + subject (320193)
    '<span class="companyName">Apple Inc. (Filer)\n'
    '    <acronym title="Central Index Key">CIK</acronym>:\n'
    '    <a href="/cgi-bin/browse-edgar?CIK=0000320193&amp;action=getcompany">0000320193 (see all company filings)</a>\n'
    "  </span>",
    '<span class="companyName">Some Fund (Filer)\n'
    '    <acronym title="Central Index Key">CIK</acronym>:\n'
    '    <a href="/cgi-bin/browse-edgar?CIK=0000899689&amp;action=getcompany">0000899689 (see all company filings)</a>\n'
    "  </span>\n"
    '  <span class="companyName">Apple Inc. (Subject)\n'
    '    <acronym title="Central Index Key">CIK</acronym>:\n'
    '    <a href="/cgi-bin/browse-edgar?CIK=0000320193&amp;action=getcompany">0000320193 (see all company filings)</a>\n'
    "  </span>",
)


class TestParseIndexHtmMultiFiler:
    """Tests for parse_index_htm() with filings that list multiple filers."""

    def test_discovered_cik_as_co_filer_passes_validation(self):
        # Discovered CIK is 320193 (subject company); primary filer CIK is 899689.
        parsed, failure = parse_index_htm(_MULTI_FILER_HTML, _SAMPLE_FILING)

        assert failure is None
        assert parsed.cik == "320193"

    def test_primary_filer_cik_returned_when_discovered_cik_absent(self):
        # Use a discovered CIK that is not listed anywhere on the page.
        filing = dataclasses.replace(_SAMPLE_FILING, cik="999999")
        parsed, failure = parse_index_htm(_MULTI_FILER_HTML, filing)

        assert parsed.cik == "899689"
        assert failure == FailureType.CIK_MISMATCH

    def test_single_filer_still_works(self):
        parsed, failure = parse_index_htm(_SAMPLE_HTML, _SAMPLE_FILING)

        assert failure is None
        assert parsed.cik == "320193"


class TestParseIndexHtmEdgeCases:
    """Tests for parse_index_htm() with missing or malformed HTML."""

    def test_missing_form_name_returns_cik_mismatch(self):
        # form_type will be empty → FORM_TYPE_MISSING since it won't match filing.form_type
        html = _SAMPLE_HTML.replace('id="formName"', 'id="other"')
        parsed, failure = parse_index_htm(html, _SAMPLE_FILING)

        assert parsed.form_type == ""
        assert failure == FailureType.FORM_TYPE_MISSING

    def test_missing_sec_num_returns_accession_number_missing(self):
        html = _SAMPLE_HTML.replace('id="secNum"', 'id="other"')
        parsed, failure = parse_index_htm(html, _SAMPLE_FILING)

        assert parsed.accession_number == ""
        assert failure == FailureType.ACCESSION_NUMBER_MISSING

    def test_missing_filing_date_returns_filing_date_missing(self):
        html = _SAMPLE_HTML.replace("Filing Date", "Other Label")
        parsed, failure = parse_index_htm(html, _SAMPLE_FILING)

        assert parsed.filing_date is None
        assert failure == FailureType.FILING_DATE_MISSING

    def test_missing_report_date_returns_none(self):
        html = _SAMPLE_HTML.replace("Period of Report", "Other Label")
        parsed, _ = parse_index_htm(html, _SAMPLE_FILING)

        assert parsed.report_date is None

    def test_missing_company_info_returns_cik_missing(self):
        html = _SAMPLE_HTML.replace('class="companyName"', 'class="other"')
        parsed, failure = parse_index_htm(html, _SAMPLE_FILING)

        assert parsed.cik == ""
        assert failure == FailureType.CIK_MISSING

    def test_rows_without_links_are_skipped(self):
        html = _SAMPLE_HTML.replace(
            '<td><a href="/Archives/edgar/data/320193/000114036126006577/aapl-20260224.xsd">aapl-20260224.xsd</a></td>',
            "<td>aapl-20260224.xsd</td>",
        )
        parsed, _ = parse_index_htm(html, _SAMPLE_FILING)

        assert len(parsed.available_documents) == 3

    def test_rows_with_empty_href_are_skipped(self):
        html = _SAMPLE_HTML.replace(
            'href="/Archives/edgar/data/320193/000114036126006577/aapl-20260224.xsd"',
            'href=""',
        )
        parsed, _ = parse_index_htm(html, _SAMPLE_FILING)

        assert len(parsed.available_documents) == 3

    def test_rows_with_empty_filename_are_skipped(self):
        html = _SAMPLE_HTML.replace("aapl-20260224.xsd</a>", "</a>")
        parsed, _ = parse_index_htm(html, _SAMPLE_FILING)

        assert len(parsed.available_documents) == 3


class TestValidateParsedFiling:
    """Tests for validate_parsed_filing()."""

    def _make_parsed(self, **overrides) -> ParsedFiling:
        defaults = {
            "url": _SAMPLE_FILING.url,
            "cik": _SAMPLE_FILING.cik,
            "accession_number": _SAMPLE_FILING.accession_number,
            "form_type": _SAMPLE_FILING.form_type,
            "filing_date": _SAMPLE_FILING.filing_date,
            "report_date": None,
            "last_scraped_at": datetime.datetime.now(datetime.UTC),
        }
        defaults.update(overrides)
        return ParsedFiling(**defaults)

    def test_returns_none_on_all_fields_matching(self):
        assert validate_parsed_filing(self._make_parsed(), _SAMPLE_FILING) is None

    def test_missing_cik_returns_cik_missing(self):
        result = validate_parsed_filing(self._make_parsed(cik=""), _SAMPLE_FILING)
        assert result == FailureType.CIK_MISSING

    def test_mismatched_cik_returns_cik_mismatch(self):
        result = validate_parsed_filing(self._make_parsed(cik="999999"), _SAMPLE_FILING)
        assert result == FailureType.CIK_MISMATCH

    def test_missing_accession_number_returns_accession_number_missing(self):
        result = validate_parsed_filing(self._make_parsed(accession_number=""), _SAMPLE_FILING)
        assert result == FailureType.ACCESSION_NUMBER_MISSING

    def test_mismatched_accession_number_returns_accession_number_mismatch(self):
        result = validate_parsed_filing(
            self._make_parsed(accession_number="0000000000-00-000000"), _SAMPLE_FILING
        )
        assert result == FailureType.ACCESSION_NUMBER_MISMATCH

    def test_missing_form_type_returns_form_type_missing(self):
        result = validate_parsed_filing(self._make_parsed(form_type=""), _SAMPLE_FILING)
        assert result == FailureType.FORM_TYPE_MISSING

    def test_mismatched_form_type_returns_form_type_mismatch(self):
        result = validate_parsed_filing(self._make_parsed(form_type="10-K"), _SAMPLE_FILING)
        assert result == FailureType.FORM_TYPE_MISMATCH

    def test_missing_filing_date_returns_filing_date_missing(self):
        result = validate_parsed_filing(self._make_parsed(filing_date=None), _SAMPLE_FILING)
        assert result == FailureType.FILING_DATE_MISSING

    def test_mismatched_filing_date_returns_filing_date_mismatch(self):
        result = validate_parsed_filing(
            self._make_parsed(filing_date=datetime.date(2020, 1, 1)), _SAMPLE_FILING
        )
        assert result == FailureType.FILING_DATE_MISMATCH

    def test_string_fields_normalized_before_comparison(self):
        result = validate_parsed_filing(
            self._make_parsed(cik="  320193  ", form_type="8-k "),
            _SAMPLE_FILING,
        )
        assert result is None
