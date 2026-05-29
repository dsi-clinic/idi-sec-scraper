"""Centralised S3 path construction for the SEC scraper.

All outputs share a common ``sec/`` root prefix so this scraper can coexist
with other tools in the same bucket.
"""

from idi_ftm2j_shared.sec import _S3_ROOT, s3_prefix
from idi_ftm2j_shared.types import DiscoveredFiling


def filing_s3_prefix(bucket: str, filing: DiscoveredFiling) -> str:
    """Return the S3 prefix (no trailing slash) for all files in a filing."""
    return f"s3://{bucket}/{s3_prefix(filing.form_type, filing.filing_date, filing.cik, filing.accession_number)}"


def manifest_s3_path(bucket: str) -> str:
    """Return the S3 path for the bucket-level manifest parquet file."""
    return f"s3://{bucket}/{_S3_ROOT}/manifest.parquet"
