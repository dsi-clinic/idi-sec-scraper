"""Data types for the SEC scraper pipeline."""

# Standard library imports
import datetime
from dataclasses import dataclass, field


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
