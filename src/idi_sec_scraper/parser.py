"""Parsers for SEC EDGAR filing index pages."""

# Standard library imports
import datetime
import re

# Third party imports
from bs4 import BeautifulSoup
from idi_ftm2j_shared.types import DiscoveredFiling

# Application imports
from idi_sec_scraper.failures import FailureType
from idi_sec_scraper.types import ParsedDocument, ParsedFiling

_SEC_BASE_URL = "https://www.sec.gov"
_IXBRL_PREFIX = "/ix?doc="
_ACCESSION_RE = re.compile(r"\d{10}-\d{2}-\d{6}")


def _normalize_str(s: str) -> str:
    return s.strip().lower()


def parse_index_htm(html: str, filing: DiscoveredFiling) -> tuple[ParsedFiling, FailureType | None]:
    """Parse a SEC EDGAR filing index page and validate it against discovery metadata.

    Args:
        html: Raw HTML content of an ``index.htm`` filing page.
        filing: The :class:`DiscoveredFiling` whose metadata is used both to
            supply the source URL and to cross-validate the parsed fields.

    Returns:
        A tuple ``(parsed_filing, failure_type)``.  ``failure_type`` is
        ``None`` when all critical fields are present and consistent with the
        discovery metadata; otherwise it is the first :class:`FailureType`
        that failed validation.
    """
    soup = BeautifulSoup(html, "html.parser")
    parsed = ParsedFiling(
        url=filing.url,
        cik=_extract_cik(soup, filing.cik),
        accession_number=_extract_accession_number(soup),
        form_type=_extract_form_type(soup),
        filing_date=_parse_date(_extract_info_field(soup, "Filing Date")),
        report_date=_parse_date(_extract_info_field(soup, "Period of Report")),
        last_scraped_at=datetime.datetime.now(datetime.UTC),
        available_documents=_extract_documents(soup),
    )
    return parsed, validate_parsed_filing(parsed, filing)


def validate_parsed_filing(parsed: ParsedFiling, filing: DiscoveredFiling) -> FailureType | None:
    """Cross-validate parsed index fields against discovery metadata.

    String fields are compared after stripping whitespace and lowercasing.
    The filing date is compared directly as a :class:`datetime.date`.

    Args:
        parsed: Result of parsing the index HTML.
        filing: Discovery metadata to validate against.

    Returns:
        The appropriate :class:`FailureType` on the first failed check,
        or ``None`` if all fields are present and consistent.
    """
    _STR_FIELDS: list[tuple[str | None, str, FailureType, FailureType]] = [
        (parsed.cik, filing.cik, FailureType.CIK_MISSING, FailureType.CIK_MISMATCH),
        (
            parsed.accession_number,
            filing.accession_number,
            FailureType.ACCESSION_NUMBER_MISSING,
            FailureType.ACCESSION_NUMBER_MISMATCH,
        ),
        (
            parsed.form_type,
            filing.form_type,
            FailureType.FORM_TYPE_MISSING,
            FailureType.FORM_TYPE_MISMATCH,
        ),
    ]
    for parsed_val, discovered_val, missing_type, mismatch_type in _STR_FIELDS:
        if not parsed_val:
            return missing_type
        if _normalize_str(parsed_val) != _normalize_str(discovered_val):
            return mismatch_type

    if not parsed.filing_date:
        return FailureType.FILING_DATE_MISSING
    if parsed.filing_date != filing.filing_date:
        return FailureType.FILING_DATE_MISMATCH

    return None


def _parse_date(s: str) -> datetime.date | None:
    try:
        return datetime.date.fromisoformat(s)
    except ValueError:
        return None


def _extract_cik(soup: BeautifulSoup, discovered_cik: str) -> str:
    """Extract a CIK from companyName spans, preferring the discovered CIK.

    Multi-filer filings list all co-filers on the index page. We return the
    discovered CIK if it appears in any span so that validation passes for
    filings where the subject company is not the primary (first-listed) filer.
    """
    ciks = []
    for span in soup.find_all("span", class_="companyName"):
        link = span.find("a")
        if not link:
            continue
        raw = link.get_text(strip=True).split()[0]
        cik = str(int(raw)) if raw.isdigit() else raw
        if cik:
            ciks.append(cik)
    if not ciks:
        return ""
    if discovered_cik in ciks:
        return discovered_cik
    return ciks[0]


def _extract_accession_number(soup: BeautifulSoup) -> str:
    div = soup.find("div", id="secNum")
    if not div:
        return ""
    match = _ACCESSION_RE.search(div.get_text())
    return match.group() if match else ""


def _extract_form_type(soup: BeautifulSoup) -> str:
    div = soup.find("div", id="formName")
    if not div:
        return ""
    strong = div.find("strong")
    if not strong:
        return ""
    return re.sub(r"^Form\s+", "", strong.get_text(strip=True))


def _extract_info_field(soup: BeautifulSoup, label: str) -> str:
    for info_head in soup.find_all("div", class_="infoHead"):
        if info_head.get_text(strip=True) == label:
            info_div = info_head.find_next_sibling("div", class_="info")
            if info_div:
                return info_div.get_text(strip=True)
    return ""


def _extract_documents(soup: BeautifulSoup) -> list[ParsedDocument]:
    documents = []
    for table in soup.find_all("table", class_="tableFile"):
        for row in table.find_all("tr")[1:]:
            cols = row.find_all("td")
            if len(cols) < 4:  # noqa: PLR2004
                continue

            link = cols[2].find("a")
            if not link:
                continue

            seq = cols[0].get_text(strip=True).replace("\xa0", "")
            description = cols[1].get_text(strip=True)
            filename = link.get_text(strip=True)
            doc_type = cols[3].get_text(strip=True).replace("\xa0", "")
            href = link.get("href", "")

            if href.startswith(_IXBRL_PREFIX):
                href = href[len(_IXBRL_PREFIX) :]

            full_url = _SEC_BASE_URL + href if href.startswith("/") else href

            # For old filings, the document may be on the index page but not have a filename/url.
            # Consider these documents unavailable.
            if not full_url or not filename:
                continue

            documents.append(
                ParsedDocument(
                    seq=seq,
                    description=description,
                    filename=filename,
                    type=doc_type,
                    url=full_url,
                )
            )
    return documents
