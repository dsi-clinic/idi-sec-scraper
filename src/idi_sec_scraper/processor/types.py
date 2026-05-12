"""Data types for the SEC scraper pipeline."""

# Standard library imports
import datetime
import re
import threading
from dataclasses import dataclass, field
from typing import Literal


@dataclass
class DiscoveredFiling:
    """A filing discovered during the discovery phase, before its index page is fetched."""

    cik: str
    accession_number: str
    form_type: str
    filing_date: datetime.date
    url: str
    company_name: str


@dataclass
class ParsedDocument:
    """A single document entry from a SEC filing index page."""

    seq: str
    description: str
    filename: str
    type: str
    url: str


@dataclass
class ParsedFiling:
    """Parsed representation of a SEC filing index page."""

    url: str
    cik: str
    accession_number: str
    form_type: str
    filing_date: datetime.date | None
    report_date: datetime.date | None
    last_scraped_at: datetime.datetime
    available_documents: list[ParsedDocument] = field(default_factory=list)


@dataclass
class ScrapedDocument:
    """A document that has been downloaded and stored in S3."""

    seq: str
    description: str
    filename: str
    type: str
    s3_key: str
    url: str


@dataclass
class ScrapedFiling:
    """Manifest of a scraped filing written to S3 as manifest.json."""

    cik: str
    accession_number: str
    form_type: str
    filing_date: str
    last_scraped_at: str
    index_url: str
    company_name: str
    report_date: str = ""
    failure_reason: str = ""
    documents: list[ScrapedDocument] = field(default_factory=list)


@dataclass
class FilterCondition:
    """A single condition applied to one field of a document."""

    field: Literal["description", "filename", "type"]
    operator: Literal["regex", "exact"]
    value: str
    compiled_pattern: re.Pattern[str] | None = field(init=False, default=None, repr=False)

    def __post_init__(self) -> None:
        """Compile regex patterns once for fast matching."""
        if self.operator == "regex":
            self.compiled_pattern = re.compile(self.value)


@dataclass
class FormTypeConfig:
    """Filter configuration for a single form type."""

    match: str
    documents: list[list[FilterCondition]] = field(default_factory=list)
    cutoff_date: datetime.date | None = None
    compiled_match: re.Pattern[str] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        """Compile the form-type matcher once."""
        self.compiled_match = re.compile(self.match)


@dataclass
class DocumentFilterConfig:
    """Top-level configuration mapping form type keys to their filter rules."""

    form_types: dict[str, FormTypeConfig] = field(default_factory=dict)


@dataclass
class PipelineStats:
    """Thread-safe counters tracking pipeline progress and failures."""

    total_filings: int = 0
    scraped_filings: int = 0
    skipped_filings: int = 0
    failed_filings: int = 0
    total_documents: int = 0
    scraped_documents: int = 0
    skipped_documents: int = 0
    failed_documents: int = 0
    form_type_filings_total: dict[str, int] = field(default_factory=dict)
    form_type_documents_total: dict[str, int] = field(default_factory=dict)
    discovery_elapsed: datetime.timedelta = field(default_factory=lambda: datetime.timedelta(0))
    scraping_elapsed: datetime.timedelta = field(default_factory=lambda: datetime.timedelta(0))

    def __post_init__(self) -> None:
        """Initialize the threading lock."""
        self._lock = threading.Lock()

    def increment(self, field: str, n: int = 1) -> None:
        """Increment a stat counter by n.

        Args:
            field: Name of the counter attribute to increment.
            n: Amount to add.
        """
        with self._lock:
            setattr(self, field, getattr(self, field) + n)

    def increment_form_type(self, field: str, form_type_key: str, n: int = 1) -> None:
        """Increment a per-form-type stat counter by n."""
        with self._lock:
            counts = getattr(self, field)
            counts[form_type_key] = counts.get(form_type_key, 0) + n


@dataclass
class PipelineConfig:
    """Base configuration shared by all pipeline variants."""

    bucket: str
    document_filters_path: str
    failure_file: str = ""
    max_workers: int = 15
    manifest_flush_every: int = 1000


@dataclass
class HistoricalPipelineConfig(PipelineConfig):
    """Configuration for the historical scraping pipeline."""

    submissions_url: str = ""
    max_ciks: int | None = None


@dataclass
class DailyPipelineConfig(PipelineConfig):
    """Configuration for the daily scraping pipeline."""

    start_date: datetime.date = field(default_factory=datetime.date.today)
    end_date: datetime.date = field(default_factory=datetime.date.today)
