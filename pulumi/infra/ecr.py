"""ECR repository for the SEC scraper image."""

import json

import pulumi_aws as aws

import pulumi

from . import config

# -----------------------------------------------------------------------------
# ECR Registry & Image URIs
# -----------------------------------------------------------------------------
ecr_registry = pulumi.Output.from_input(config.caller.account_id).apply(
    lambda aid: f"{aid}.dkr.ecr.{config.aws_region}.amazonaws.com"
)

ecr_repo = aws.ecr.Repository(
    "idi-ecr-scraper",
    name=config.name_prefix,
    force_delete=True,
    tags=config.tags(),
)

scraper_image = ecr_registry.apply(lambda r: f"{r}/{config.name_prefix}:latest")

# Lifecycle policy — expire images beyond the last N to avoid unbounded storage growth
ecr_lifecycle_policy = aws.ecr.LifecyclePolicy(
    "idi-ecr-lifecycle",
    repository=ecr_repo.name,
    policy=json.dumps(
        {
            "rules": [
                {
                    "rulePriority": 1,
                    "description": f"Keep last {config.ecr_image_count} images",
                    "selection": {
                        "tagStatus": "any",
                        "countType": "imageCountMoreThan",
                        "countNumber": config.ecr_image_count,
                    },
                    "action": {"type": "expire"},
                }
            ]
        }
    ),
)
