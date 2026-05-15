"""AWS Secrets Manager resources for pipeline secrets."""

import pulumi_aws as aws

import pulumi

from . import config

# -----------------------------------------------------------------------------
# Config (required — Pulumi fails at deploy time if missing)
# -----------------------------------------------------------------------------
sec_user_agent = config.config.require_secret("sec_user_agent")

# -----------------------------------------------------------------------------
# Secrets
# -----------------------------------------------------------------------------
sec_user_agent_secret = aws.secretsmanager.Secret(
    "idi-secret-sec-user-agent",
    name=f"{config.name_prefix}-sec-user-agent",
    description="SEC EDGAR User-Agent header value (Name email@example.com)",
    recovery_window_in_days=0,
    tags=config.tags(),
)

sec_user_agent_secret_version = aws.secretsmanager.SecretVersion(
    "idi-secret-version-sec-user-agent",
    secret_id=sec_user_agent_secret.id,
    secret_string=sec_user_agent,
    opts=pulumi.ResourceOptions(depends_on=[sec_user_agent_secret]),
)
