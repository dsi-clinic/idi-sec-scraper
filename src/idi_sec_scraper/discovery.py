"""Discovery logic for SEC scraper runs."""

# Standard library imports
import datetime
import json
import re
import zipfile
from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator

# Application imports
from idi_ftm2j_shared.api import SecClient

# Third party imports
from idi_ftm2j_shared.failures import FailureRegistry
from idi_ftm2j_shared.logs import get_logger
from idi_ftm2j_shared.sec import get_daily_index
from idi_ftm2j_shared.storage import open_zip
from idi_ftm2j_shared.types import DiscoveredFiling

from idi_sec_scraper.failures import FailureType

_logger = get_logger(__name__)


class Discovery(ABC):
    """Abstract base class for SEC filing discovery strategies."""

    def __init__(
        self,
        sec_client: SecClient,
        form_types: list[str],
        cutoffs: dict[str, datetime.date | None] | None = None,
    ) -> None:
        """Initialize with a SEC client, form type regex patterns, and optional cutoff dates."""
        self.sec_client = sec_client
        self.form_types = form_types
        self.cutoffs: dict[str, datetime.date | None] = cutoffs or {}
        self.ciks_scanned: int = 0
        self.total_ciks: int = 0

    @abstractmethod
    def discover(self, *args, **kwargs) -> Iterable[DiscoveredFiling]:
        """Return matching filings from the discovery source."""
        ...


_IS_OVERFLOW = re.compile(r"-submissions-\d+\.json$")

_INDEX_HTM_URL = (
    "https://www.sec.gov/Archives/edgar/data/{cik}/{accession_nodash}/{accession_number}-index.htm"
)


class DailyDiscovery(Discovery):
    """Discovers filings from the SEC daily crawler index over a date range."""

    def discover(
        self, start_date: datetime.date, end_date: datetime.date
    ) -> list[DiscoveredFiling]:
        """Fetch all filings for ``[start_date, end_date]`` and filter by form type and cutoffs.

        Args:
            start_date: First date to include, inclusive.
            end_date: Last date to include, inclusive.

        Returns:
            List of :class:`DiscoveredFiling` objects ordered by date ascending.
        """
        return [
            filing
            for filing in get_daily_index(start_date, end_date, client=self.sec_client)
            if _matches_form_types(filing.form_type, self.form_types)
            and not _before_cutoff(
                filing.form_type, filing.filing_date, self.form_types, self.cutoffs
            )
        ]


class HistoricalDiscovery(Discovery):
    """Discovers filings from a SEC submissions.zip bulk archive."""

    def __init__(
        self,
        sec_client: SecClient,
        form_types: list[str],
        failure_registry: FailureRegistry | None = None,
        cutoffs: dict[str, datetime.date | None] | None = None,
    ) -> None:
        """Initialize with a SEC client, form type patterns, optional failure registry, and optional cutoff dates."""
        super().__init__(sec_client, form_types, cutoffs)
        self.failure_registry = failure_registry

    def discover(
        self, submissions_url: str, max_ciks: int | None = None
    ) -> Iterator[DiscoveredFiling]:
        """Read each ``CIK*.json`` in the archive and yield matching filings.

        Combines recent filings with any overflow files referenced in
        ``filings.files``, filters by ``form_types``, and yields a
        :class:`DiscoveredFiling` for each match. Filings are yielded lazily
        as each CIK file is parsed, keeping memory use O(1) with respect to
        the total number of matching filings.

        Args:
            submissions_url: Local path, ``s3://`` URL, or ``https://`` URL to
                the submissions.zip archive.
            max_ciks: If set, process at most this many CIK files. Useful for
                testing without running the full archive.

        Yields:
            :class:`DiscoveredFiling` objects for all matching rows.
        """
        with open_zip(submissions_url, headers=self.sec_client.SEC_HEADERS) as zf:
            names = zf.namelist()
            cik_names = [n for n in names if n.startswith("CIK") and n.endswith(".json")]
            if max_ciks is not None:
                cik_names = cik_names[:max_ciks]
            self.total_ciks = len(cik_names)
            _logger.info("Starting discovery: %d CIK files to process", self.total_ciks)
            for i, filename in enumerate(cik_names, 1):
                yield from self._parse_cik_file(zf, filename)
                self.ciks_scanned = i

    def _parse_cik_file(self, zf: zipfile.ZipFile, filename: str) -> list[DiscoveredFiling]:
        if _IS_OVERFLOW.search(filename):
            return []
        if not (filename.startswith("CIK") and filename.endswith(".json")):
            return []

        try:
            with zf.open(filename) as f:
                data = json.load(f)
        except (EOFError, json.JSONDecodeError) as e:
            _logger.error("Failed to read %s: %s", filename, e)
            return []

        cik = str(int(data.get("cik", "") or 0)) if data.get("cik") else ""
        company_name = data.get("name", "")

        if self.failure_registry and (cik, filename) in self.failure_registry:
            _logger.warning("Skipping known failure: %s", filename)
            return []

        recent = data.get("filings", {}).get("recent", {})
        forms = list(recent.get("form", []))
        accession_numbers = list(recent.get("accessionNumber", []))
        filing_dates = list(recent.get("filingDate", []))

        for entry in data.get("filings", {}).get("files", []):
            overflow_filename = entry.get("name", "")
            if not overflow_filename:
                continue
            if self.failure_registry and (cik, overflow_filename) in self.failure_registry:
                _logger.warning("Skipping known missing overflow file: %s", overflow_filename)
                continue
            try:
                with zf.open(overflow_filename) as of:
                    overflow = json.load(of)
                forms += overflow.get("form", [])
                accession_numbers += overflow.get("accessionNumber", [])
                filing_dates += overflow.get("filingDate", [])
            except KeyError:
                _logger.error("Overflow file not found: %s", overflow_filename)
                if self.failure_registry:
                    self.failure_registry.add(
                        (cik, overflow_filename), FailureType.NO_OVERFLOW_FILINGS
                    )
                continue

        if len({len(forms), len(accession_numbers), len(filing_dates)}) != 1:
            _logger.error("Filename: %s has forms with mismatched data lengths.", filename)
            if self.failure_registry:
                self.failure_registry.add((cik, filename), FailureType.MISMATCHED_LENGTHS)
            return []

        if not any([forms, accession_numbers, filing_dates]):
            _logger.debug("Filename: %s has forms without data.", filename)
            return []

        filings = []
        for form, accession_number, filing_date in zip(forms, accession_numbers, filing_dates):
            if not _matches_form_types(form, self.form_types):
                continue
            parsed_date = datetime.date.fromisoformat(filing_date)
            if _before_cutoff(form, parsed_date, self.form_types, self.cutoffs):
                continue
            accession_nodash = accession_number.replace("-", "")
            url = _INDEX_HTM_URL.format(
                cik=cik,
                accession_nodash=accession_nodash,
                accession_number=accession_number,
            )
            filings.append(
                DiscoveredFiling(
                    cik=cik,
                    accession_number=accession_number,
                    form_type=form,
                    filing_date=parsed_date,
                    url=url,
                    company_name=company_name,
                )
            )
        return filings


def _matches_form_types(form_type: str, patterns: list[str]) -> bool:
    return any(re.match(pattern, form_type) for pattern in patterns)


def _before_cutoff(
    form_type: str,
    filing_date: datetime.date,
    patterns: list[str],
    cutoffs: dict[str, datetime.date | None],
) -> bool:
    """Return True if filing_date is before the cutoff for the first matching pattern."""
    for pattern in patterns:
        if re.match(pattern, form_type):
            cutoff = cutoffs.get(pattern)
            return cutoff is not None and filing_date < cutoff
    return False
