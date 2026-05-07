"""ECS cluster and Fargate task definition for the SEC scraper."""

import json

import pulumi_aws as aws

import pulumi

from . import config, ecr, iam, secrets

# -----------------------------------------------------------------------------
# ECS Cluster (Fargate only)
# -----------------------------------------------------------------------------
cluster = aws.ecs.Cluster(
    "idi-ecs-cluster",
    name=f"{config.name_prefix}-cluster",
    settings=[
        aws.ecs.ClusterSettingArgs(
            name="containerInsights",
            value="enabled",
        )
    ],
    tags=config.tags(),
)

# -----------------------------------------------------------------------------
# CloudWatch Log Group for awslogs driver
# -----------------------------------------------------------------------------
log_group = aws.cloudwatch.LogGroup(
    "idi-ecs-log-group",
    name=f"/ecs/{config.name_prefix}",
    retention_in_days=config.log_retention_days,
    tags=config.tags(),
)

# -----------------------------------------------------------------------------
# Task Definition
# -----------------------------------------------------------------------------
cpu = config.config.get("cpu") or "1024"
memory = config.config.get("memory") or "4096"
rate_limit = config.config.get("rate_limit") or "0.15"
max_workers = config.config.get("max_workers") or "15"

failure_file = f"s3://{config.bucket_name}/sec/failures.json"

scraper_cmd = (
    f"sec-scraper"
    f" --bucket {config.bucket_name}"
    f" --failure-file {failure_file}"
    f" --rate-limit {rate_limit}"
    f" --max-workers {max_workers}"
    f" daily"
)

container_definitions = pulumi.Output.all(
    image=ecr.scraper_image,
    log_group_name=log_group.name,
    region=config.aws_region,
    sec_user_agent_secret_arn=secrets.sec_user_agent_secret.arn,
).apply(
    lambda args: json.dumps(
        [
            {
                "name": "sec-scraper",
                "image": args["image"],
                "essential": True,
                "entryPoint": ["sh", "-c"],
                "command": [scraper_cmd],
                "environment": [
                    {"name": "AWS_REGION", "value": args["region"]},
                    {"name": "CLOUDWATCH_LOGS_ENABLED", "value": "false"},
                    {"name": "PYTHONUNBUFFERED", "value": "1"},
                ],
                "secrets": [
                    {
                        "name": "SEC_USER_AGENT",
                        "valueFrom": args["sec_user_agent_secret_arn"],
                    },
                ],
                "logConfiguration": {
                    "logDriver": "awslogs",
                    "options": {
                        "awslogs-group": args["log_group_name"],
                        "awslogs-region": args["region"],
                        "awslogs-stream-prefix": "scraper",
                    },
                },
                "stopTimeout": 30,
            }
        ]
    )
)

task_definition = aws.ecs.TaskDefinition(
    "idi-ecs-task-definition",
    family=f"{config.name_prefix}",
    requires_compatibilities=["FARGATE"],
    network_mode="awsvpc",
    cpu=cpu,
    memory=memory,
    execution_role_arn=iam.task_execution_role.arn,
    task_role_arn=iam.task_role.arn,
    container_definitions=container_definitions,
    tags=config.tags(),
)
