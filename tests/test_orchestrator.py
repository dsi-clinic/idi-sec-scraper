"""Tests for processor.orchestrator — CLI argument parsing."""

# Standard library imports
import datetime

# Third party imports
import pytest

# Application imports
from idi_sec_scraper.orchestrator import main


def _run(mocker, argv: list[str]):
    """Invoke main() with the given argv, mocking pipeline construction and run."""
    mocker.patch("sys.argv", ["sec-scraper"] + argv)
    mocker.patch("idi_sec_scraper.orchestrator.SecClient")
    mock_hist = mocker.patch("idi_sec_scraper.orchestrator.HistoricalSECScraperPipeline")
    mock_hist.return_value.run.return_value = None
    mock_daily = mocker.patch("idi_sec_scraper.orchestrator.DailySECScraperPipeline")
    mock_daily.return_value.run.return_value = None
    main()
    return mock_hist, mock_daily


class TestSharedArgs:
    """--bucket and --document-filters belong to the parent parser."""

    def test_bucket_before_subcommand_historical(self, mocker):
        mock_hist, _ = _run(
            mocker,
            ["--bucket", "my-bucket", "historical"],
        )
        config = mock_hist.call_args.args[0]
        assert config.bucket == "my-bucket"

    def test_bucket_before_subcommand_daily(self, mocker):
        _, mock_daily = _run(
            mocker,
            [
                "--bucket",
                "my-bucket",
                "daily",
                "--start-date",
                "2026-04-01",
                "--end-date",
                "2026-04-01",
            ],
        )
        config = mock_daily.call_args.args[0]
        assert config.bucket == "my-bucket"

    def test_document_filters_default(self, mocker):
        mock_hist, _ = _run(mocker, ["--bucket", "b", "historical"])
        config = mock_hist.call_args.args[0]
        assert config.document_filters_path == "config/document_filters.yaml"

    def test_document_filters_override(self, mocker):
        mock_hist, _ = _run(
            mocker,
            ["--bucket", "b", "--document-filters", "custom/filters.yaml", "historical"],
        )
        config = mock_hist.call_args.args[0]
        assert config.document_filters_path == "custom/filters.yaml"

    def test_bucket_is_required(self, mocker):
        mocker.patch("sys.argv", ["sec-scraper", "historical"])
        with pytest.raises(SystemExit):
            main()

    def test_max_workers_default(self, mocker):
        mock_hist, _ = _run(mocker, ["--bucket", "b", "historical"])
        config = mock_hist.call_args.args[0]
        assert config.max_workers == 15

    def test_max_workers_override(self, mocker):
        mock_hist, _ = _run(mocker, ["--bucket", "b", "--max-workers", "16", "historical"])
        config = mock_hist.call_args.args[0]
        assert config.max_workers == 16


class TestHistoricalMode:
    """Tests for the historical subcommand."""

    def test_max_ciks_default_is_none(self, mocker):
        mock_hist, _ = _run(mocker, ["--bucket", "b", "historical"])
        config = mock_hist.call_args.args[0]
        assert config.max_ciks is None

    def test_max_ciks_override(self, mocker):
        mock_hist, _ = _run(mocker, ["--bucket", "b", "historical", "--max-ciks", "100"])
        config = mock_hist.call_args.args[0]
        assert config.max_ciks == 100

    def test_launches_historical_pipeline(self, mocker):
        mock_hist, mock_daily = _run(mocker, ["--bucket", "b", "historical"])
        assert mock_hist.return_value.run.called
        assert not mock_daily.return_value.run.called

    def test_submissions_url_default(self, mocker):
        mock_hist, _ = _run(mocker, ["--bucket", "b", "historical"])
        config = mock_hist.call_args.args[0]
        assert "submissions.zip" in config.submissions_url

    def test_submissions_url_override(self, mocker):
        mock_hist, _ = _run(
            mocker,
            ["--bucket", "b", "historical", "--submissions-url", "s3://bucket/submissions.zip"],
        )
        config = mock_hist.call_args.args[0]
        assert config.submissions_url == "s3://bucket/submissions.zip"


class TestDailyMode:
    """Tests for the daily subcommand."""

    def test_launches_daily_pipeline(self, mocker):
        mock_hist, mock_daily = _run(mocker, ["--bucket", "b", "daily"])
        assert mock_daily.return_value.run.called
        assert not mock_hist.return_value.run.called

    def test_dates_default_to_yesterday(self, mocker):
        fake_today = datetime.date(2026, 4, 2)
        mock_dt = mocker.patch("idi_sec_scraper.orchestrator.datetime")
        mock_dt.date.today.return_value = fake_today
        mock_dt.date.fromisoformat = datetime.date.fromisoformat
        mock_dt.timedelta = datetime.timedelta
        _, mock_daily = _run(mocker, ["--bucket", "b", "daily"])
        config = mock_daily.call_args.args[0]
        assert config.start_date == datetime.date(2026, 4, 1)
        assert config.end_date == datetime.date(2026, 4, 1)

    def test_start_and_end_date_parsed(self, mocker):
        _, mock_daily = _run(
            mocker,
            ["--bucket", "b", "daily", "--start-date", "2026-04-01", "--end-date", "2026-04-03"],
        )
        config = mock_daily.call_args.args[0]
        assert config.start_date == datetime.date(2026, 4, 1)
        assert config.end_date == datetime.date(2026, 4, 3)

    def test_start_date_without_end_date_errors(self, mocker):
        mocker.patch(
            "sys.argv", ["sec-scraper", "--bucket", "b", "daily", "--end-date", "2026-04-01"]
        )
        with pytest.raises(SystemExit):
            main()

    def test_end_date_without_start_date_errors(self, mocker):
        mocker.patch(
            "sys.argv", ["sec-scraper", "--bucket", "b", "daily", "--start-date", "2026-04-01"]
        )
        with pytest.raises(SystemExit):
            main()
