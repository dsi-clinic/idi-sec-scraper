# idi-sec-scraper

Downloads SEC EDGAR filings and stores them in S3-compatible object storage. Part of the Follow the Money to Justice Terminal.

Two run modes are supported:

- **Historical** — bulk-loads all filings from SEC's `submissions.zip` archive (~12 GB compressed, covers all companies on record).
- **Daily** — fetches filings from the SEC daily crawler index for a date range, used to keep the bucket current.

---

## Table of Contents

- [Requirements](#requirements)
- [Installation](#installation)
- [Local S3 (MinIO)](#local-s3-minio)
- [Running the scraper](#running-the-scraper)
  - [Historical run](#historical-run)
  - [Daily run](#daily-run)
  - [Common options](#common-options)
- [Document filters](#document-filters)
- [S3 bucket layout](#s3-bucket-layout)
  - [File structure](#file-structure)
  - [manifest.json schema](#manifestjson-schema)
  - [How the bucket is populated](#how-the-bucket-is-populated)
- [Failure registry](#failure-registry)
- [Development](#development)

---

## Requirements

- Python 3.13+
- [uv](https://github.com/astral-sh/uv) for dependency management
- AWS credentials (or a MinIO instance) with read/write access to the target bucket

## Installation

```bash
git clone <repo-url>
cd idi-sec-scraper
uv sync
```

---

## Local S3 (MinIO)

For local development, a MinIO instance is provided via Docker Compose. It creates a bucket named `idi-sec-scraper` automatically.

```bash
docker compose up -d
```

The MinIO console is available at http://localhost:9001 (user: `minioadmin`, password: `minioadmin`).

Configure the AWS CLI or `boto3` to point at MinIO by setting these environment variables:

```bash
export AWS_ACCESS_KEY_ID=minioadmin
export AWS_SECRET_ACCESS_KEY=minioadmin
export AWS_ENDPOINT_URL=http://localhost:9000
```

The scraper picks up `AWS_ENDPOINT_URL` automatically via `boto3`/`smart-open`.

---

## Running the scraper

All commands are run with `uv run`. The entry point is `sec-scraper`.

### Historical run

Processes the SEC's bulk submissions archive. On first run this downloads ~12 GB and may take several hours.

```bash
uv run sec-scraper \
  --bucket idi-sec-scraper \
  historical \
  --submissions-url https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip
```

To limit the run to a small number of companies (useful for testing):

```bash
uv run sec-scraper \
  --bucket idi-sec-scraper \
  historical \
  --max-ciks 100
```

To use a locally cached copy of the archive:

```bash
uv run sec-scraper \
  --bucket idi-sec-scraper \
  historical \
  --submissions-url /path/to/submissions.zip
```

### Daily run

Fetches filings indexed by SEC for a specific date range. Dates are inclusive.

```bash
uv run sec-scraper \
  --bucket idi-sec-scraper \
  daily \
  --start-date 2026-04-01 \
  --end-date 2026-04-30
```

Omit both `--start-date` and `--end-date` to scrape yesterday's filings (the default for the scheduled ECS task):

```bash
uv run sec-scraper --bucket idi-sec-scraper daily
```

If either date flag is supplied, both are required — mixing one explicit date with the yesterday default could silently produce an invalid range.

### Common options

These flags apply to both `historical` and `daily` and must be placed **before** the subcommand:

| Flag | Default | Description |
|---|---|---|
| `--bucket` | *(required)* | S3 bucket name |
| `--document-filters` | `config/document_filters.yaml` | Path to document filter config |
| `--failure-file` | *(disabled)* | Path or `s3://` URL for the failure registry JSON |
| `--rate-limit` | `0.2` | Minimum seconds between SEC requests |
| `--max-workers` | `15` | Number of concurrent filing threads |

---

## Document filters

`config/document_filters.yaml` controls which form types are scraped and which documents within each filing are downloaded.

```yaml
form_types:
  10-K:
    match: '10-?K'          # regex matched against the filing's form type
    documents:              # list of OR-ed filter groups
      - all_of:             # conditions within a group are AND-ed
          - field: type
            operator: regex
            value: '^EX-21'
```

**Fields:** `type`, `description`, `filename`

**Operators:** `regex` (anchored at start via `re.match`), `exact` (full string equality)

If `documents` is an empty list, all documents in the filing are downloaded. If a filing's form type matches no entry in the config, it is skipped with a `FORM_NOT_CONFIGURED` failure.

---

## S3 bucket layout

### File structure

```
s3://<bucket>/
└── sec/
    └── <YYYY-MM-DD>/               # filing date
        └── <form-type>/            # e.g. 10-K, 8-K, 13F-HR
            └── <cik>/              # SEC Central Index Key (no leading zeros)
                └── <accession>/    # accession number with dashes removed
                    ├── manifest.json
                    ├── index.htm
                    └── <document-filename>
```

Example:

```
s3://idi-dev-processor/
└── sec/
    └── 2026-02-24/
        └── 8-K/
            └── 320193/
                └── 000114036126006577/
                    ├── manifest.json
                    ├── index.htm
                    └── ef20060722_8k.htm
```

### manifest.json schema

Every filing directory contains a `manifest.json` written after scraping. This is the primary index for consumers.

```json
{
  "cik": "320193",
  "accession_number": "0001140361-26-006577",
  "form_type": "8-K",
  "filing_date": "2026-02-24",
  "last_scraped_at": "2026-04-30T12:00:00+00:00",
  "index_url": "https://www.sec.gov/Archives/edgar/data/320193/000114036126006577/0001140361-26-006577-index.htm",
  "company_name": "Apple Inc.",
  "report_date": "2026-02-24",
  "failure_reason": "",
  "documents": [
    {
      "seq": "1",
      "description": "8-K",
      "filename": "ef20060722_8k.htm",
      "type": "8-K",
      "s3_key": "s3://idi-dev-processor/sec/2026-02-24/8-K/320193/000114036126006577/ef20060722_8k.htm",
      "url": "https://www.sec.gov/Archives/edgar/data/320193/000114036126006577/ef20060722_8k.htm"
    }
  ]
}
```

| Field | Description |
|---|---|
| `cik` | SEC Central Index Key, leading zeros stripped |
| `accession_number` | SEC accession number in `NNNNNNNNNN-YY-NNNNNN` format |
| `form_type` | Raw form type string as filed with SEC |
| `filing_date` | Date the filing was made (`YYYY-MM-DD`) |
| `last_scraped_at` | ISO 8601 UTC timestamp of the last scrape |
| `index_url` | URL of the SEC EDGAR index page for this filing |
| `company_name` | Company name as it appears in the SEC submission |
| `report_date` | Period of report date (`YYYY-MM-DD`), or empty string if not present on the index page |
| `failure_reason` | Non-empty string if the filing failed processing (see below) |
| `documents` | List of documents downloaded for this filing |
| `documents[].seq` | Document sequence number from the EDGAR index |
| `documents[].description` | Human-readable description from the EDGAR index |
| `documents[].filename` | Filename as listed on the EDGAR index page |
| `documents[].type` | Document type (e.g. `EX-21.1`, `8-K`) |
| `documents[].s3_key` | Full `s3://` path to the downloaded file |
| `documents[].url` | Original SEC EDGAR URL |

### How the bucket is populated

1. The scraper discovers filings (from `submissions.zip` or the daily crawler index).
2. For each filing, it fetches the SEC EDGAR index page (`index.htm`) and saves it.
3. It parses the index page and cross-validates key fields (CIK, accession number, form type, filing date) against discovery metadata.
4. Documents are filtered according to `document_filters.yaml` and downloaded individually.
5. A `manifest.json` is written once all documents are fetched.

**Re-running is safe:** For discovered filings whose `manifest.json` already exist in S3, the index file is read from S3; otherwise it will be fetched from SEC. To determine if there are new documents to download for this filing, the current document filter is applied to available documents in the index and compared to the existing documents. Only missing ones are fetched. A filing is counted as fully cached only when the index was loaded from S3 and no new documents were fetched.

**Failure manifests:** if a filing fails validation or has no matching documents, a `manifest.json` is still written with `documents: []` and a non-empty `failure_reason`. This allows consumers to distinguish between "not yet scraped" (no manifest) and "scraped but failed" (manifest with `failure_reason`).

**Failure reasons:**

| Value | Meaning |
|---|---|
| `cik_missing` / `cik_mismatch` | CIK absent or inconsistent between index and discovery |
| `accession_number_missing` / `accession_number_mismatch` | Accession number absent or inconsistent |
| `form_type_missing` / `form_type_mismatch` | Form type absent or inconsistent |
| `filing_date_missing` / `filing_date_mismatch` | Filing date absent or inconsistent |
| `form_not_configured` | Form type not present in `document_filters.yaml` |
| `documents_missing` | Form type is configured but no documents on the filing matched the filter |
| `api_error` | HTTP error fetching the index or a document |

---

## Consuming data from S3

The S3 key structure encodes the filing date and form type, so consumers can scope their reads to exactly the dates and form types they care about without scanning the whole bucket. The key structure is:

```
s3://<bucket>/sec/<YYYY-MM-DD>/<form-type>/<cik>/<accession>/manifest.json
```

For each filing directory, read `manifest.json` first. Skip any filing where `failure_reason` is non-empty — its `documents` list will be empty. The `documents[].s3_key` fields give ready-to-use `s3://` paths to every downloaded file.

**Note on form-type matching:** S3 prefix listing is exact — there is no regex or glob support. Amended forms are stored under a separate prefix (e.g. `10-K/A` is stored as `10-K_A` because slashes are sanitized to underscores in S3 keys). To cover multiple variants, pass a list of prefixes and `iter_manifests` will union the results. If this becomes unergonomic, we can plug in DuckDB which has native S3 glob support.

To get all manifests for a date range and form type:

```python
import boto3, json
from datetime import date, timedelta

s3 = boto3.client("s3")
bucket = "idi-sec-scraper"

def iter_manifests(form_types: str | list[str], start: date, end: date):
    """Yield parsed manifest dicts for each form type prefix over the date range."""
    if isinstance(form_types, str):
        form_types = [form_types]
    day = start
    while day <= end:
        for form_type in form_types:
            prefix = f"sec/{day}/{form_type}/"
            paginator = s3.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    if obj["Key"].endswith("/manifest.json"):
                        body = s3.get_object(Bucket=bucket, Key=obj["Key"])["Body"].read()
                        yield json.loads(body)
        day += timedelta(days=1)
```

### Corporate structure processor

Interested in **EX-21** (subsidiary lists) from `10-K` and `10-K_A` filings and **EX-8** from `20-F` and `20-F_A` filings.

List by date prefix and form type, then read each manifest to get the exact document paths:

```python
for manifest in iter_manifests(["10-K", "10-K_A"], start=date(2024, 1, 1), end=date(2024, 12, 31)):
    if manifest["failure_reason"]:
        continue
    for doc in manifest["documents"]:
        # All documents on a 10-K are EX-21.x by filter config
        print(manifest["cik"], manifest["filing_date"], doc["s3_key"])

# Same pattern for 20-F / EX-8 (include amended 20-F_A)
for manifest in iter_manifests(["20-F", "20-F_A"], start=date(2024, 1, 1), end=date(2024, 12, 31)):
    if manifest["failure_reason"]:
        continue
    for doc in manifest["documents"]:
        print(manifest["cik"], manifest["filing_date"], doc["s3_key"])
```

`report_date` on the manifest is the period the annual report covers, which may differ from `filing_date` by several months. Use `report_date` when aligning subsidiary snapshots to a fiscal year.

### Shareholder tracker

Interested in the information-table HTML from **13F-HR** filings (one per institutional manager per quarter).

The form-type prefix is `13F-HR`. Each filing will have one or two documents: the information table HTML and optionally the cover-page HTML. Filter by `documents[].type` to be precise:

```python
for manifest in iter_manifests("13F-HR", start=date(2024, 1, 1), end=date(2024, 12, 31)):
    if manifest["failure_reason"]:
        continue
    for doc in manifest["documents"]:
        if doc["type"] == "INFORMATION TABLE":
            print(manifest["cik"], manifest["report_date"], doc["s3_key"])
```

### Commercial debt tracker

Interested in the **complete submission text file** from **8-K** filings (the single `.txt` that bundles all exhibits). The filter config selects this document by its description `"Complete submission text file"`.

```python
for manifest in iter_manifests("8-K", start=date(2024, 1, 1), end=date(2024, 12, 31)):
    if manifest["failure_reason"]:
        continue
    for doc in manifest["documents"]:
        # Each 8-K has exactly one document: the complete submission text
        print(manifest["cik"], manifest["company_name"], manifest["filing_date"], doc["s3_key"])
```

---

## AWS ECS Architecture

For more information on the development cycle, see the [idi-ftm2j-shared documentation](https://github.com/dsi-clinic/idi-ftm2j-shared#development-cycle) that is used by all processors.

The scraper runs as an **ECS Fargate task** scheduled by **EventBridge Scheduler**. Infrastructure is defined in `pulumi/` using Pulumi (Python).

### Design Decisions

| Decision | Rationale |
|---|---|
| **Fargate** (not EC2) | No instance management — container runs and exits; portable image |
| **EventBridge Scheduler** (not Step Functions) | Single task; no workflow orchestration needed |
| **Public subnet** (no NAT Gateway) | Task needs outbound internet for SEC EDGAR |
| **`awslogs` driver only** | Captures all stdout/stderr; linked directly to the task in the ECS console |
| **Shell wrapper for dates** | Task computes yesterday's date at runtime so it always scrapes the correct day |

### Resources

| Module | Resources |
|---|---|
| `config.py` | Shared name prefix (`{project}-{stack}-{app}`), tags, AWS caller identity |
| `networking.py` | Default VPC, single-AZ public subnet, egress-only security group |
| `iam.py` | Task execution role (ECR pull, CloudWatch Logs, Secrets Manager) + task role (S3, ECS Exec) |
| `ecr.py` | ECR repository + lifecycle policy (retains last 5 images) |
| `ecs.py` | ECS cluster (Fargate, Container Insights), CloudWatch log group (30-day retention), task definition (1 vCPU / 4 GB) |
| `secrets.py` | Secrets Manager secret for SEC User-Agent header; injected as `SEC_USER_AGENT` env var at task startup |
| `scheduling.py` | EventBridge Scheduler (daily cron, starts disabled), SQS dead-letter queue for failed invocations, scheduler IAM role |

### Deployment

```bash
cd pulumi/

# First-time setup
uv run --group pulumi pulumi stack init dev
uv run --group pulumi pulumi config set aws:region us-east-2
uv run --group pulumi pulumi config set idi:bucket_name <bucket>
uv run --group pulumi pulumi config set --secret idi:sec_user_agent "Name email@example.com"

# Deploy
uv run --group pulumi pulumi up
```

#### Configuration Reference

| Config | Default | Description |
|---|---|---|
| `aws:region` | `us-east-2` | AWS region |
| `idi:app_name` | `sec-scraper` | Application name used in resource naming |
| `idi:bucket_name` | — | S3 bucket for scraped filings (created externally) |
| `idi:sec_user_agent` | — | SEC EDGAR User-Agent header (secret; stored in Secrets Manager) |
| `idi:cron_sec_scraper` | `cron(0 3 * * ? *)` | EventBridge schedule expression (3 AM UTC daily) |
| `idi:schedule_enabled` | `false` | Enable the EventBridge schedule |
| `idi:cpu` | `1024` | Fargate task CPU units |
| `idi:memory` | `4096` | Fargate task memory (MiB) |
| `idi:rate_limit` | `0.15` | Seconds between SEC API requests |
| `idi:max_workers` | `15` | Concurrent filing download threads |

### Manual Task Execution

Use `pulumi stack output` to retrieve the cluster name, subnet ID, and security group ID.

```bash
aws ecs run-task \
    --cluster <cluster-name> \
    --task-definition <task-definition> \
    --launch-type FARGATE \
    --propagate-tags TASK_DEFINITION \
    --network-configuration "awsvpcConfiguration={subnets=[<subnet-id>],securityGroups=[<sg-id>],assignPublicIp=ENABLED}" \
    --overrides '{
        "containerOverrides": [{
            "name": "sec-scraper",
            "command": ["sh", "-c", "sec-scraper --bucket <bucket> daily --start-date 2026-04-01 --end-date 2026-04-30"]
        }]
    }'
```

### Monitoring

- **Logs**: CloudWatch → Log groups → `/ecs/{name_prefix}` → stream per task run
- **ECS console**: Tasks tab shows stopped tasks for up to 1 hour after completion
- **Scheduling failures**: Check the SQS dead-letter queue (`pulumi stack output dlq_url`)
- **ECS Exec** (interactive debug into a running task):
  ```bash
  aws ecs execute-command \
    --cluster <cluster> \
    --task <task-id> \
    --container sec-scraper \
    --interactive \
    --command "/bin/sh"
  ```

### Building and Pushing the Container Image

```bash
# Set ECR repo URL
ECR_REPO=$(cd pulumi && uv run --group pulumi pulumi stack output ecr_repo_url)

# Authenticate Docker to ECR
aws ecr get-login-password --region us-east-2 | \
  docker login --username AWS --password-stdin \
  $(aws sts get-caller-identity --query Account --output text).dkr.ecr.us-east-2.amazonaws.com

# Build for linux/amd64 (required on Apple Silicon) and push
docker buildx build --platform linux/amd64 \
  -f dockerfiles/Dockerfile.scraper \
  -t $ECR_REPO \
  --push .
```

---

## Development

Run the test suite:

```bash
uv run pytest tests/
```

Lint and format:

```bash
uv run ruff check --fix
```

Run a small test scrape against local MinIO:

```bash
docker compose up -d

uv run sec-scraper \
  --bucket idi-dev-processor \
  --failure-file s3://idi-dev-processor/sec/failures.json \
  historical \
  --max-ciks 50
```
