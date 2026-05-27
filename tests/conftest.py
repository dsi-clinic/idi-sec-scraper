"""Shared fixtures for the test suite."""

# Standard library imports
import os

# Third party imports
import boto3
import pytest
from botocore.exceptions import ClientError

MINIO_ENDPOINT = os.getenv("AWS_ENDPOINT_URL", "http://localhost:9000")
MINIO_ACCESS_KEY = os.getenv("AWS_ACCESS_KEY_ID", "minioadmin")
MINIO_SECRET_KEY = os.getenv("AWS_SECRET_ACCESS_KEY", "minioadmin")
MINIO_REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
TEST_BUCKET = os.getenv("S3_BUCKET", "idi-sec-scraper")


@pytest.fixture(scope="session", autouse=True)
def reset_storage_client():
    """Reset the shared S3 client so it's created fresh with the current env vars."""
    import idi_ftm2j_shared.storage as storage

    storage._s3_client = None
    yield


@pytest.fixture(scope="session")
def s3_client():
    """Boto3 S3 client pointed at the local MinIO instance."""
    return boto3.client(
        "s3",
        endpoint_url=MINIO_ENDPOINT,
        aws_access_key_id=MINIO_ACCESS_KEY,
        aws_secret_access_key=MINIO_SECRET_KEY,
        region_name=MINIO_REGION,
    )


@pytest.fixture(scope="session")
def s3_bucket(s3_client):
    """Ensure the test bucket exists and return its name."""
    try:
        s3_client.head_bucket(Bucket=TEST_BUCKET)
    except ClientError:
        s3_client.create_bucket(Bucket=TEST_BUCKET)
    return TEST_BUCKET
