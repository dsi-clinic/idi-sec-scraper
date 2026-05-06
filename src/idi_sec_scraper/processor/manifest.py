"""Bucket-level manifest utilities."""

# Third party imports
import pandas as pd

# Application imports
from idi_sec_scraper.common.logs import get_logger
from idi_sec_scraper.processor.types import ScrapedFiling

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
    on ``s3_key``, and writes the result back to ``s3://{bucket}/manifest.parquet``.

    Args:
        bucket: S3 bucket name (without protocol prefix).
        filings: Scraped filings whose documents should be added to the manifest.
    """
    new_df = _filings_to_df(filings)
    if new_df.empty:
        return

    manifest_path = f"s3://{bucket}/manifest.parquet"

    try:
        existing_df = pd.read_parquet(manifest_path)
    except FileNotFoundError:
        existing_df = pd.DataFrame(columns=_MANIFEST_COLUMNS)

    combined = pd.concat([existing_df, new_df], ignore_index=True)
    combined = combined.drop_duplicates(subset=["s3_key"], keep="last")
    combined.to_parquet(manifest_path, index=False)
    _logger.info("Wrote %d rows to bucket manifest (%s)", len(combined), manifest_path)
