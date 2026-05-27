"""Pipeline Orchestrator - CLI entry point for the SEC scraper pipeline."""

# Standard library imports
import argparse
import datetime

# Application imports
from idi_ftm2j_shared.api import SecClient

from idi_sec_scraper.processor.pipeline import (
    DailySECScraperPipeline,
    HistoricalSECScraperPipeline,
)
from idi_sec_scraper.processor.types import DailyPipelineConfig, HistoricalPipelineConfig


def main() -> None:
    """Run the SEC scraper pipeline from the command line."""
    parser = argparse.ArgumentParser(description="SEC Scraper Pipeline")
    parser.add_argument(
        "--rate-limit",
        type=float,
        default=0.15,
        help="Minimum seconds between SEC requests (default: 0.15)",
    )
    parser.add_argument(
        "--failure-file",
        default="",
        help="Path or S3 URL for the failure registry JSON (default: disabled)",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=15,
        help="Number of concurrent filing threads (default: 15)",
    )
    parser.add_argument(
        "--manifest-flush-every",
        type=int,
        default=1000,
        help="Number of scraped filings to buffer before writing to the bucket manifest (default: 1000)",
    )

    parser.add_argument("--bucket", required=True, help="S3 bucket name")
    parser.add_argument(
        "--document-filters",
        default="config/document_filters.yaml",
        help="Path to document_filters.yaml (default: config/document_filters.yaml)",
    )

    subparsers = parser.add_subparsers(dest="mode", required=True)

    hist = subparsers.add_parser("historical", help="Run historical pipeline from submissions.zip")
    hist.add_argument(
        "--submissions-url",
        default="https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip",
        help="Path or URL to submissions.zip",
    )
    hist.add_argument(
        "--max-ciks",
        type=int,
        default=None,
        help="Cap the number of CIK files processed (default: no cap)",
    )

    daily = subparsers.add_parser("daily", help="Run daily pipeline")
    daily.add_argument(
        "--start-date",
        default=None,
        type=datetime.date.fromisoformat,
        help=(
            "Start date inclusive (YYYY-MM-DD). "
            "If omitted, both --start-date and --end-date default to yesterday. "
            "If either is supplied, both are required."
        ),
    )
    daily.add_argument(
        "--end-date",
        default=None,
        type=datetime.date.fromisoformat,
        help=(
            "End date inclusive (YYYY-MM-DD). "
            "If omitted, both --start-date and --end-date default to yesterday. "
            "If either is supplied, both are required."
        ),
    )

    args = parser.parse_args()

    if args.mode == "daily":
        # If neither date is given, default both to yesterday.
        # If exactly one is given, require the other — mixing an explicit date
        # with a yesterday default can silently produce start > end.
        if args.start_date is None and args.end_date is None:
            yesterday = datetime.date.today() - datetime.timedelta(days=1)
            args.start_date = yesterday
            args.end_date = yesterday
        elif args.start_date is None:
            parser.error("--start-date is required when --end-date is provided")
        elif args.end_date is None:
            parser.error("--end-date is required when --start-date is provided")

    sec_client = SecClient(rate_limit=args.rate_limit)

    if args.mode == "historical":
        config = HistoricalPipelineConfig(
            bucket=args.bucket,
            document_filters_path=args.document_filters,
            failure_file=args.failure_file,
            max_workers=args.max_workers,
            manifest_flush_every=args.manifest_flush_every,
            submissions_url=args.submissions_url,
            max_ciks=args.max_ciks,
        )
        pipeline = HistoricalSECScraperPipeline(config, sec_client)
    else:
        config = DailyPipelineConfig(
            bucket=args.bucket,
            document_filters_path=args.document_filters,
            failure_file=args.failure_file,
            max_workers=args.max_workers,
            manifest_flush_every=args.manifest_flush_every,
            start_date=args.start_date,
            end_date=args.end_date,
        )
        pipeline = DailySECScraperPipeline(config, sec_client)

    pipeline.run()


if __name__ == "__main__":
    main()
