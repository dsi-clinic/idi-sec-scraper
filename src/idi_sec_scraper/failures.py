"""Failure classification for the SEC scraper pipeline."""

# Standard library imports
from enum import StrEnum

# Third party imports
from idi_ftm2j_shared.failures import FailureClassifier


class FailureType(StrEnum):
    """Failure types for SEC scraper pipeline operations."""

    # Critical field absent from parsed index page
    CIK_MISSING = "cik_missing"
    ACCESSION_NUMBER_MISSING = "accession_number_missing"
    FORM_TYPE_MISSING = "form_type_missing"
    FILING_DATE_MISSING = "filing_date_missing"

    # Critical field present but conflicts with discovery metadata
    CIK_MISMATCH = "cik_mismatch"
    ACCESSION_NUMBER_MISMATCH = "accession_number_mismatch"
    FORM_TYPE_MISMATCH = "form_type_mismatch"
    FILING_DATE_MISMATCH = "filing_date_mismatch"

    # Discovery-phase failures
    MISMATCHED_LENGTHS = "mismatched_lengths"
    NO_OVERFLOW_FILINGS = "no_overflow_filings"

    # Pipeline-phase failures
    FORM_NOT_CONFIGURED = "form_not_configured"
    DOCUMENTS_MISSING = "documents_missing"  # Retryable because if the document filters change, we would want to check the filing for relevant documents.
    API_ERROR = "api_error"
    RATE_LIMIT = "rate_limit"  # Retryable


class SECScraperFailureClassifier(FailureClassifier):
    """Classifies and categorises SEC scraper failures."""

    _DO_NOT_RETRY: frozenset[FailureType] = frozenset(
        {
            FailureType.CIK_MISSING,
            FailureType.ACCESSION_NUMBER_MISSING,
            FailureType.FORM_TYPE_MISSING,
            FailureType.FILING_DATE_MISSING,
            FailureType.CIK_MISMATCH,
            FailureType.ACCESSION_NUMBER_MISMATCH,
            FailureType.FORM_TYPE_MISMATCH,
            FailureType.FILING_DATE_MISMATCH,
            FailureType.MISMATCHED_LENGTHS,
            FailureType.NO_OVERFLOW_FILINGS,
            FailureType.FORM_NOT_CONFIGURED,
        }
    )

    @property
    def do_not_retry(self) -> frozenset[FailureType]:
        """Return the set of failure types that should not be retried."""
        return self._DO_NOT_RETRY

    def classify_from_response(self, response: dict, **kwargs: object) -> FailureType:
        """Classify an API response as a failure type.

        Args:
            response: API response dict with a ``status_code`` key.
            **kwargs: Unused.

        Returns:
            :attr:`FailureType.RATE_LIMIT` for HTTP 429, otherwise
            :attr:`FailureType.API_ERROR`.
        """
        if response.get("status_code") == 429:  # noqa: PLR2004
            return FailureType.RATE_LIMIT
        return FailureType.API_ERROR
