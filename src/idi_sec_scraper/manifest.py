"""Bucket-level manifest utilities."""

# Standard library imports
import threading

import pandas as pd

# Third party imports
from idi_ftm2j_shared.logs import get_logger

# Application imports
from idi_sec_scraper.paths import manifest_s3_path
from idi_sec_scraper.types import ScrapedFiling

_logger = get_logger(__name__)

_MANIFEST_COLUMNS = [
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


def _filings_to_df(filings: list[ScrapedFiling]) -> pd.DataFrame:
    """Flatten a list of ScrapedFilings into a per-document DataFrame."""
    rows = [
        {
            "cik": filing.cik,
            "accession_number": filing.accession_number,
            "filing_date": filing.filing_date,
            "form_type": filing.form_type,
            "seq": doc.seq,
            "description": doc.description,
            "filename": doc.filename,
            "type": doc.type,
            "s3_key": doc.s3_key,
            "url": doc.url,
        }
        for filing in filings
        for doc in filing.documents
    ]
    if not rows:
        return pd.DataFrame(columns=_MANIFEST_COLUMNS)
    return pd.DataFrame(rows)


def update_bucket_manifest(bucket: str, filings: list[ScrapedFiling]) -> None:
    """Append new filing documents to the bucket-level manifest parquet file.

    Reads the existing manifest (if any), merges with new rows, deduplicates
    on ``s3_key``, and writes the result back to ``s3://{bucket}/sec/manifest.parquet``.

    Args:
        bucket: S3 bucket name (without protocol prefix).
        filings: Scraped filings whose documents should be added to the manifest.
    """
    new_df = _filings_to_df(filings)
    if new_df.empty:
        return

    manifest_path = manifest_s3_path(bucket)

    try:
        existing_df = pd.read_parquet(manifest_path)
    except FileNotFoundError:
        existing_df = pd.DataFrame(columns=_MANIFEST_COLUMNS)

    combined = pd.concat([existing_df, new_df], ignore_index=True)
    combined = combined.drop_duplicates(subset=["s3_key"], keep="last")
    combined.to_parquet(manifest_path, index=False)
    _logger.info("Wrote %d rows to bucket manifest (%s)", len(combined), manifest_path)


class ManifestWriter:
    """Buffers scraped filings and periodically flushes them to the bucket manifest.

    Thread-safe. Flushes automatically every ``flush_every`` filings added, and
    on an explicit :meth:`flush` call (e.g. at end of pipeline run).

    Args:
        bucket: S3 bucket name (without protocol prefix).
        flush_every: Number of filings to buffer before an automatic flush.
    """

    def __init__(self, bucket: str, flush_every: int = 1000) -> None:
        """Initialize the ManifestWriter."""
        self.bucket = bucket
        self._flush_every = flush_every
        self._buffer: list[ScrapedFiling] = []
        self._lock = threading.RLock()

    def add(self, filing: ScrapedFiling) -> None:
        """Add a scraped filing to the buffer, flushing if the threshold is reached.

        Args:
            filing: A successfully scraped filling to include in the manifest.
        """
        with self._lock:
            self._buffer.append(filing)
            if len(self._buffer) >= self._flush_every:
                self._flush_locked()

    def flush(self) -> None:
        """Write all buffered filings to the manifest and clear the buffer."""
        with self._lock:
            self._flush_locked()

    def _flush_locked(self) -> None:
        """Flush under the lock — caller must already hold ``self._lock``."""
        if not self._buffer:
            return
        update_bucket_manifest(self.bucket, self._buffer)
        self._buffer.clear()
