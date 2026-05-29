"""Tests for processor.failures."""

# Application imports
from idi_sec_scraper.failures import FailureType, SECScraperFailureClassifier


class TestFailureType:
    """Tests for FailureType enum values."""

    def test_cik_missing_value(self):
        assert FailureType.CIK_MISSING == "cik_missing"

    def test_accession_number_missing_value(self):
        assert FailureType.ACCESSION_NUMBER_MISSING == "accession_number_missing"

    def test_form_type_missing_value(self):
        assert FailureType.FORM_TYPE_MISSING == "form_type_missing"

    def test_filing_date_missing_value(self):
        assert FailureType.FILING_DATE_MISSING == "filing_date_missing"

    def test_cik_mismatch_value(self):
        assert FailureType.CIK_MISMATCH == "cik_mismatch"

    def test_accession_number_mismatch_value(self):
        assert FailureType.ACCESSION_NUMBER_MISMATCH == "accession_number_mismatch"

    def test_form_type_mismatch_value(self):
        assert FailureType.FORM_TYPE_MISMATCH == "form_type_mismatch"

    def test_filing_date_mismatch_value(self):
        assert FailureType.FILING_DATE_MISMATCH == "filing_date_mismatch"

    def test_mismatched_lengths_value(self):
        assert FailureType.MISMATCHED_LENGTHS == "mismatched_lengths"

    def test_no_overflow_filings_value(self):
        assert FailureType.NO_OVERFLOW_FILINGS == "no_overflow_filings"

    def test_form_not_configured_value(self):
        assert FailureType.FORM_NOT_CONFIGURED == "form_not_configured"

    def test_documents_missing_value(self):
        assert FailureType.DOCUMENTS_MISSING == "documents_missing"

    def test_api_error_value(self):
        assert FailureType.API_ERROR == "api_error"

    def test_rate_limit_value(self):
        assert FailureType.RATE_LIMIT == "rate_limit"


class TestSECScraperFailureClassifier:
    """Tests for SECScraperFailureClassifier."""

    def setup_method(self):
        """Create a fresh classifier for each test."""
        self.classifier = SECScraperFailureClassifier()

    def test_all_missing_types_are_non_retryable(self):
        for ft in (
            FailureType.CIK_MISSING,
            FailureType.ACCESSION_NUMBER_MISSING,
            FailureType.FORM_TYPE_MISSING,
            FailureType.FILING_DATE_MISSING,
        ):
            assert not self.classifier.is_retryable(ft), f"{ft} should not be retryable"

    def test_all_mismatch_types_are_non_retryable(self):
        for ft in (
            FailureType.CIK_MISMATCH,
            FailureType.ACCESSION_NUMBER_MISMATCH,
            FailureType.FORM_TYPE_MISMATCH,
            FailureType.FILING_DATE_MISMATCH,
        ):
            assert not self.classifier.is_retryable(ft), f"{ft} should not be retryable"

    def test_discovery_failures_are_non_retryable(self):
        for ft in (FailureType.MISMATCHED_LENGTHS, FailureType.NO_OVERFLOW_FILINGS):
            assert not self.classifier.is_retryable(ft), f"{ft} should not be retryable"

    def test_form_not_configured_is_non_retryable(self):
        assert not self.classifier.is_retryable(FailureType.FORM_NOT_CONFIGURED)

    def test_documents_missing_is_retryable(self):
        assert self.classifier.is_retryable(FailureType.DOCUMENTS_MISSING)

    def test_rate_limit_is_retryable(self):
        assert self.classifier.is_retryable(FailureType.RATE_LIMIT)

    def test_classify_from_response_returns_rate_limit_for_429(self):
        result = self.classifier.classify_from_response(
            {"status_code": 429, "error": "Too Many Requests"}
        )
        assert result == FailureType.RATE_LIMIT

    def test_classify_from_response_returns_api_error_for_404(self):
        result = self.classifier.classify_from_response({"status_code": 404, "error": "Not Found"})
        assert result == FailureType.API_ERROR

    def test_classify_from_response_returns_api_error_for_500(self):
        result = self.classifier.classify_from_response(
            {"status_code": 500, "error": "Server Error"}
        )
        assert result == FailureType.API_ERROR

    def test_do_not_retry_contains_all_non_retryable_types(self):
        expected = {
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
        assert expected <= self.classifier.do_not_retry

    def test_do_not_retry_excludes_retryable_types(self):
        assert FailureType.RATE_LIMIT not in self.classifier.do_not_retry
        assert FailureType.DOCUMENTS_MISSING not in self.classifier.do_not_retry
