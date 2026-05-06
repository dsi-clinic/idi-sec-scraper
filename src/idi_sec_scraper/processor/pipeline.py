"""SEC scraper pipeline."""

# Standard library imports
import dataclasses
import datetime
import re
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

# Third party imports
from tqdm import tqdm

# Application imports
from idi_sec_scraper.common.api import SecClient
from idi_sec_scraper.common.failures import FailureRegistry
from idi_sec_scraper.common.logs import get_logger
from idi_sec_scraper.common.storage import (
    key_exists,
    load_content,
    load_json,
    save_content,
    save_json,
)
from idi_sec_scraper.processor.discovery import DailyDiscovery, Discovery, HistoricalDiscovery
from idi_sec_scraper.processor.document_filters import (
    find_form_type_entry,
    load_document_filters,
    select_and_filter_documents,
)
from idi_sec_scraper.processor.failures import FailureType, SECScraperFailureClassifier
from idi_sec_scraper.processor.manifest import update_bucket_manifest
from idi_sec_scraper.processor.parser import parse_index_htm
from idi_sec_scraper.processor.types import (
    DiscoveredFiling,
    DocumentFilterConfig,
    PipelineConfig,
    PipelineStats,
    ScrapedDocument,
    ScrapedFiling,
)

# Matches any character not in the S3-safe set; used to sanitise form types for S3 paths.
_SAFE_RE = re.compile(r"[^0-9a-zA-Z!._*'()-]")


def _s3_prefix(bucket: str, filing: DiscoveredFiling) -> str:
    """Return the S3 prefix (no trailing slash) for all files belonging to a filing."""
    form_type_safe = _SAFE_RE.sub("_", filing.form_type)
    accession_nodash = filing.accession_number.replace("-", "")
    return f"s3://{bucket}/{filing.filing_date}/{form_type_safe}/{filing.cik}/{accession_nodash}"


def _new_scraped_filing(filing: DiscoveredFiling) -> ScrapedFiling:
    """Create a new empty ScrapedFiling from a DiscoveredFiling."""
    return ScrapedFiling(
        cik=filing.cik,
        accession_number=filing.accession_number,
        form_type=filing.form_type,
        filing_date=filing.filing_date.isoformat(),
        last_scraped_at=datetime.datetime.now(datetime.UTC).isoformat(),
        index_url=filing.url,
        company_name=filing.company_name,
        documents=[],
    )


def _scraped_filing_from_dict(data: dict[str, Any]) -> ScrapedFiling:
    """Deserialize a ScrapedFiling from a manifest dict read from S3."""
    documents = [ScrapedDocument(**d) for d in data.get("documents", [])]
    return ScrapedFiling(
        cik=data["cik"],
        accession_number=data["accession_number"],
        form_type=data["form_type"],
        filing_date=data["filing_date"],
        report_date=data.get("report_date", ""),
        last_scraped_at=data["last_scraped_at"],
        index_url=data.get("index_url", ""),
        company_name=data.get("company_name", ""),
        failure_reason=data.get("failure_reason", ""),
        documents=documents,
    )


class Pipeline(ABC):
    """Abstract base class for SEC scraper pipelines."""

    def __init__(self, config: PipelineConfig, sec_client: SecClient) -> None:
        """Initialize the pipeline.

        Args:
            config: Pipeline configuration.
            sec_client: Configured SEC EDGAR API client.
        """
        self.config = config
        self.sec_client = sec_client
        self.stats = PipelineStats()
        self.logger = get_logger(self.__class__.__name__)
        self.document_filter_config: DocumentFilterConfig = load_document_filters(
            config.document_filters_path
        )
        form_type_keys = list(self.document_filter_config.form_types.keys())
        self.stats.form_type_filings_total = dict.fromkeys(form_type_keys, 0)
        self.stats.form_type_documents_total = dict.fromkeys(form_type_keys, 0)
        self._failure_classifier = SECScraperFailureClassifier()
        self.failure_registry = FailureRegistry(config.failure_file, self._failure_classifier)

    @abstractmethod
    def load_input(self) -> list[DiscoveredFiling]:
        """Discover filings to scrape.

        Returns:
            List of :class:`DiscoveredFiling` objects.
        """
        ...

    @abstractmethod
    def process(self, filings: list[DiscoveredFiling]) -> list[ScrapedFiling]:
        """Scrape each filing and return manifests.

        Args:
            filings: Filings returned by :meth:`load_input`.

        Returns:
            List of :class:`ScrapedFiling` manifests.
        """
        ...

    @abstractmethod
    def display_stats(self) -> None:
        """Log a summary of pipeline statistics."""
        ...

    def run(self) -> None:
        """Execute the full pipeline: load → process → display stats."""
        start_time = datetime.datetime.now()
        filings = self.load_input()
        self.process(filings)
        self.display_stats()
        self.logger.info("Elapsed time: %s", datetime.datetime.now() - start_time)


class SECScraperPipeline(Pipeline, ABC):
    """Pipeline that fetches SEC filings and stores content and manifests in S3."""

    def __init__(self, config: PipelineConfig, sec_client: SecClient) -> None:
        """Initialize the pipeline and create the discovery instance.

        Args:
            config: Pipeline configuration.
            sec_client: Configured SEC EDGAR API client.
        """
        super().__init__(config, sec_client)
        self.discovery = self._make_discovery()

    def run(self) -> None:
        """Execute the full pipeline: load → process → update manifest → display stats."""
        start_time = datetime.datetime.now()
        filings = self.load_input()
        scraped = self.process(filings)
        update_bucket_manifest(self.config.bucket, scraped)
        self.display_stats()
        self.logger.info("Elapsed time: %s", datetime.datetime.now() - start_time)

    @abstractmethod
    def _make_discovery(self) -> Discovery:
        """Construct and return the discovery instance for this pipeline variant."""
        ...

    def process(self, filings: list[DiscoveredFiling]) -> list[ScrapedFiling]:
        """Scrape each filing, writing index HTML, documents, and manifests to S3.

        Known failures and fully-cached filings are counted as skipped; new
        failures increment the failed counter.

        Args:
            filings: Filings to scrape.

        Returns:
            List of :class:`ScrapedFiling` objects for filings where new work
            was done this run (excludes known failures and fully-cached filings).
        """
        results = []
        with ThreadPoolExecutor(max_workers=self.config.max_workers) as executor:
            future_to_filing = {}
            for filing in filings:
                self.stats.increment("total_filings")
                form_type_entry = find_form_type_entry(
                    filing.form_type, self.document_filter_config
                )
                if form_type_entry is not None:
                    self.stats.increment_form_type("form_type_filings_total", form_type_entry[0])
                failure_key = (filing.cik, filing.accession_number)
                if failure_key in self.failure_registry:
                    self.logger.warning(
                        "Skipping known failure: %s / %s", filing.cik, filing.accession_number
                    )
                    self.stats.increment("skipped_filings")
                    continue
                future_to_filing[executor.submit(self._scrape_filing, filing)] = filing

            for future in tqdm(
                as_completed(future_to_filing), total=len(future_to_filing), desc="Scraping filings"
            ):
                result = future.result()
                if result is None:
                    self.stats.increment("failed_filings")
                elif result[1]:
                    self.stats.increment("skipped_filings")
                else:
                    results.append(result[0])
                    self.stats.increment("scraped_filings")
        self.failure_registry.flush()
        return results

    def display_stats(self) -> None:
        """Log a formatted summary of pipeline statistics."""
        self.logger.info("=" * 40)
        self.logger.info("Pipeline Stats")
        self.logger.info("=" * 40)
        self.logger.info("  Filings")
        self.logger.info("    Total:    %d", self.stats.total_filings)
        self.logger.info("    Scraped:  %d", self.stats.scraped_filings)
        self.logger.info("    Skipped:  %d", self.stats.skipped_filings)
        self.logger.info("    Failed:   %d", self.stats.failed_filings)
        self.logger.info("  Documents")
        self.logger.info("    Total:    %d", self.stats.total_documents)
        self.logger.info("    Scraped:  %d", self.stats.scraped_documents)
        self.logger.info("    Skipped:  %d", self.stats.skipped_documents)
        self.logger.info("    Failed:   %d", self.stats.failed_documents)
        self.logger.info("  Per configured form type")
        for form_type_key in self.document_filter_config.form_types:
            self.logger.info(
                "    %s: filings=%d documents=%d",
                form_type_key,
                self.stats.form_type_filings_total.get(form_type_key, 0),
                self.stats.form_type_documents_total.get(form_type_key, 0),
            )
        self.logger.info("=" * 40)

    def _fetch_or_load_filing_index(
        self, filing: DiscoveredFiling, prefix: str
    ) -> tuple[str, ScrapedFiling, bool] | None:
        """Retrieve index HTML either from S3 cache or from the SEC API.

        For historical runs where the index is already cached, reads from S3
        and loads any existing manifest. Otherwise fetches from the SEC API,
        saves the HTML to S3, and returns a fresh manifest scaffold.

        Args:
            filing: The filing to fetch.
            prefix: S3 prefix for this filing's files (no trailing slash).

        Returns:
            ``(index_html, scraped_filing, index_was_cached)`` on success, or
            ``None`` on API error. A rate-limit response returns ``None``
            without registering a failure.
        """
        filing_manifest_s3_key = f"{prefix}/manifest.json"
        filing_index_s3_key = f"{prefix}/index.htm"

        if key_exists(filing_manifest_s3_key):
            index_html = load_content(filing_index_s3_key)
            manifest_data = load_json(filing_manifest_s3_key)
            return index_html, _scraped_filing_from_dict(manifest_data), True

        response = self.sec_client.query_endpoint(sec_url=filing.url, return_json=False)
        if "error" in response:
            failure_type = self._failure_classifier.classify_from_response(response)
            log = (
                self.logger.warning if failure_type == FailureType.RATE_LIMIT else self.logger.error
            )
            log(
                "Failed to fetch index for %s / %s (%s)",
                filing.cik,
                filing.accession_number,
                failure_type,
            )
            if failure_type != FailureType.RATE_LIMIT:
                self.failure_registry.add((filing.cik, filing.accession_number), failure_type)
            return None

        save_content(filing_index_s3_key, response["data"])
        return response["data"], _new_scraped_filing(filing), False

    def _scrape_filing(self, filing: DiscoveredFiling) -> tuple[ScrapedFiling, bool] | None:
        """Scrape a single filing.

        Returns:
            ``(scraped_filing, was_fully_cached)`` on success, or ``None`` on
            failure. ``was_fully_cached`` is ``True`` when both the index HTML
            and every filtered document were already present in S3 — no API
            calls were made for this filing.
        """
        try:
            return self._scrape_filing_inner(filing)
        except Exception:
            self.logger.exception(
                "Unexpected error scraping %s / %s (form=%s date=%s url=%s)",
                filing.cik,
                filing.accession_number,
                filing.form_type,
                filing.filing_date,
                filing.url,
            )
            return None

    def _scrape_filing_inner(self, filing: DiscoveredFiling) -> tuple[ScrapedFiling, bool] | None:
        failure_key = (filing.cik, filing.accession_number)

        prefix = _s3_prefix(self.config.bucket, filing)

        fetch_result = self._fetch_or_load_filing_index(filing, prefix)
        if fetch_result is None:
            return None
        index_html, scraped_filing, index_was_cached = fetch_result

        parsed_filing, failure_type = parse_index_htm(index_html, filing)
        scraped_filing.report_date = (
            parsed_filing.report_date.isoformat() if parsed_filing.report_date else ""
        )

        if failure_type is not None:
            self.logger.error(
                "Parse validation failed (%s) for %s / %s (%s)",
                failure_type,
                filing.cik,
                filing.accession_number,
                filing.form_type,
            )
            scraped_filing.failure_reason = str(failure_type)
            scraped_filing.last_scraped_at = datetime.datetime.now(datetime.UTC).isoformat()
            save_json(f"{prefix}/manifest.json", dataclasses.asdict(scraped_filing))
            self.failure_registry.add(failure_key, failure_type)
            return None

        form_type_key, filtered_docs = select_and_filter_documents(
            filing.form_type, parsed_filing.available_documents, self.document_filter_config
        )
        if form_type_key is None:
            self.logger.error(
                "Form type %r not configured for %s / %s",
                filing.form_type,
                filing.cik,
                filing.accession_number,
            )
            self.failure_registry.add(failure_key, FailureType.FORM_NOT_CONFIGURED)
            return None

        if not filtered_docs:
            self.logger.error(
                "No matching documents for %s / %s (%s)",
                filing.cik,
                filing.accession_number,
                filing.form_type,
            )
            scraped_filing.failure_reason = str(FailureType.DOCUMENTS_MISSING)
            scraped_filing.last_scraped_at = datetime.datetime.now(datetime.UTC).isoformat()
            save_json(f"{prefix}/manifest.json", dataclasses.asdict(scraped_filing))
            self.failure_registry.add(failure_key, FailureType.DOCUMENTS_MISSING)
            return None

        existing = {doc.filename for doc in scraped_filing.documents}
        new_doc_count = 0
        failed_doc_count = 0

        for doc in filtered_docs:
            self.stats.increment("total_documents")
            self.stats.increment_form_type("form_type_documents_total", form_type_key)
            if doc.filename in existing:
                self.stats.increment("skipped_documents")
                continue

            doc_response = self.sec_client.query_endpoint(sec_url=doc.url, return_json=False)

            if "error" in doc_response:
                failure_type = self._failure_classifier.classify_from_response(doc_response)
                if failure_type == FailureType.RATE_LIMIT:
                    self.logger.warning("Rate limited fetching document %s", doc.url)
                else:
                    self.logger.error("Failed to fetch document %s", doc.url)
                    self.failure_registry.add(failure_key, FailureType.API_ERROR)
                self.stats.increment("failed_documents")
                failed_doc_count += 1
                continue

            doc_s3_url = f"{prefix}/{doc.filename}"
            save_content(doc_s3_url, doc_response["data"])

            scraped_filing.documents.append(
                ScrapedDocument(
                    seq=doc.seq,
                    description=doc.description,
                    filename=doc.filename,
                    type=doc.type,
                    s3_key=doc_s3_url,
                    url=doc.url,
                )
            )
            self.stats.increment("scraped_documents")
            new_doc_count += 1

        was_fully_cached = index_was_cached and new_doc_count == 0 and failed_doc_count == 0
        scraped_filing.last_scraped_at = datetime.datetime.now(datetime.UTC).isoformat()
        save_json(f"{prefix}/manifest.json", dataclasses.asdict(scraped_filing))
        return scraped_filing, was_fully_cached


class HistoricalSECScraperPipeline(SECScraperPipeline):
    """Pipeline for historical runs, discovering filings from a submissions.zip archive."""

    def _make_discovery(self) -> HistoricalDiscovery:
        """Create a HistoricalDiscovery instance from config and filter patterns."""
        ft_configs = list(self.document_filter_config.form_types.values())
        patterns = [ft.match for ft in ft_configs]
        cutoffs = {ft.match: ft.cutoff_date for ft in ft_configs}
        return HistoricalDiscovery(self.sec_client, patterns, self.failure_registry, cutoffs=cutoffs)

    def load_input(self) -> list[DiscoveredFiling]:
        """Discover filings from the submissions.zip archive.

        Returns:
            List of :class:`DiscoveredFiling` objects.
        """
        return self.discovery.discover(self.config.submissions_url, self.config.max_ciks)


class DailySECScraperPipeline(SECScraperPipeline):
    """Pipeline for daily runs, discovering filings from daily crawler indexes."""

    def _make_discovery(self) -> DailyDiscovery:
        """Create a DailyDiscovery instance from config and filter patterns."""
        ft_configs = list(self.document_filter_config.form_types.values())
        patterns = [ft.match for ft in ft_configs]
        cutoffs = {ft.match: ft.cutoff_date for ft in ft_configs}
        return DailyDiscovery(self.sec_client, patterns, cutoffs=cutoffs)

    def load_input(self) -> list[DiscoveredFiling]:
        """Discover filings from daily crawler indexes.

        Returns:
            List of :class:`DiscoveredFiling` objects.
        """
        return self.discovery.discover(self.config.start_date, self.config.end_date)
