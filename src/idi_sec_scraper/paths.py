"""Centralised S3 path construction for the SEC scraper.

All outputs share a common ``sec/`` root prefix so this scraper can coexist
with other tools in the same bucket.  Change ``_ROOT`` here to relocate
everything at once.
"""

import re

from idi_sec_scraper.types import DiscoveredFiling

_ROOT = "sec"

# Matches any character not in the S3-safe set; used to sanitise form types.
_SAFE_RE = re.compile(r"[^0-9a-zA-Z!._*'()-]")


def filing_s3_prefix(bucket: str, filing: DiscoveredFiling) -> str:
    """Return the S3 prefix (no trailing slash) for all files in a filing."""
    form_type_safe = _SAFE_RE.sub("_", filing.form_type)
    accession_nodash = filing.accession_number.replace("-", "")
    return (
        f"s3://{bucket}/{_ROOT}"
        f"/{filing.filing_date}/{form_type_safe}/{filing.cik}/{accession_nodash}"
    )


def manifest_s3_path(bucket: str) -> str:
    """Return the S3 path for the bucket-level manifest parquet file."""
    return f"s3://{bucket}/{_ROOT}/manifest.parquet"
