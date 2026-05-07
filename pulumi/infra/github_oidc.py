"""GitHub Actions OIDC roles for CI/CD.

Two roles:
  1. checks  — read-only, used by pulumi preview on PRs
  2. deploy  — read/write, used by pulumi up and ECR push on merge
"""

import json

import pulumi_aws as aws

import pulumi

from . import config

GITHUB_REPO = "dsi-clinic/idi-sec-scraper"
OIDC_PROVIDER_ARN = pulumi.Output.from_input(config.caller.account_id).apply(
    lambda aid: f"arn:aws:iam::{aid}:oidc-provider/token.actions.githubusercontent.com"
)

# Pulumi state bucket — same bucket used by all apps, scoped to sec-scraper prefix
PULUMI_STATE_BUCKET = "idi-ftm2j-dev-pulumi-state"
PULUMI_STATE_PREFIX = "sec-scraper"


def _oidc_trust_policy(condition_value: str | list[str]) -> pulumi.Output:
    return OIDC_PROVIDER_ARN.apply(
        lambda arn: json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {"Federated": arn},
                        "Action": "sts:AssumeRoleWithWebIdentity",
                        "Condition": {
                            "StringEquals": {
                                "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
                            },
                            "StringLike": {
                                "token.actions.githubusercontent.com:sub": condition_value
                            },
                        },
                    }
                ],
            }
        )
    )


# ---------------------------------------------------------------------------
# Checks role — pulumi preview on PRs (read-only)
# ---------------------------------------------------------------------------
checks_role = aws.iam.Role(
    "idi-role-github-checks",
    name=f"{config.name_prefix}-role-github-checks",
    description="GitHub Actions: pulumi preview (read-only)",
    assume_role_policy=_oidc_trust_policy(f"repo:{GITHUB_REPO}:pull_request"),
    tags=config.tags({"purpose": "GitHub Actions CI checks"}),
)

_checks_policy = pulumi.Output.from_input(config.caller.account_id).apply(
    lambda aid: json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                # Pulumi state — read
                {
                    "Effect": "Allow",
                    "Action": ["s3:GetObject", "s3:ListBucket"],
                    "Resource": [
                        f"arn:aws:s3:::{PULUMI_STATE_BUCKET}",
                        f"arn:aws:s3:::{PULUMI_STATE_BUCKET}/{PULUMI_STATE_PREFIX}/*",
                    ],
                },
                # Pulumi locks — write (preview acquires/releases locks even for read-only operations)
                # Covers login with or without a key prefix (s3://bucket vs s3://bucket/prefix)
                {
                    "Effect": "Allow",
                    "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
                    "Resource": [
                        f"arn:aws:s3:::{PULUMI_STATE_BUCKET}/.pulumi/locks/*",
                        f"arn:aws:s3:::{PULUMI_STATE_BUCKET}/{PULUMI_STATE_PREFIX}/.pulumi/locks/*",
                    ],
                },
                # ECR — read
                {
                    "Effect": "Allow",
                    "Action": ["ecr:GetAuthorizationToken"],
                    "Resource": "*",
                },
                {
                    "Effect": "Allow",
                    "Action": [
                        "ecr:DescribeRepositories",
                        "ecr:DescribeImages",
                        "ecr:ListImages",
                        "ecr:GetLifecyclePolicy",
                        "ecr:GetRepositoryPolicy",
                    ],
                    "Resource": f"arn:aws:ecr:{config.aws_region}:{aid}:repository/*",
                },
                # ECS — read
                {
                    "Effect": "Allow",
                    "Action": [
                        "ecs:DescribeClusters",
                        "ecs:DescribeTaskDefinition",
                        "ecs:DescribeTasks",
                        "ecs:ListClusters",
                        "ecs:ListTaskDefinitions",
                        "ecs:ListTasks",
                    ],
                    "Resource": "*",
                },
                # IAM — read (Pulumi inspects role state during preview)
                {
                    "Effect": "Allow",
                    "Action": [
                        "iam:GetRole",
                        "iam:GetRolePolicy",
                        "iam:ListRolePolicies",
                        "iam:ListAttachedRolePolicies",
                    ],
                    "Resource": f"arn:aws:iam::{aid}:role/{config.name_prefix}-*",
                },
                # CloudWatch Logs — read
                {
                    "Effect": "Allow",
                    "Action": ["logs:DescribeLogGroups", "logs:ListTagsLogGroup"],
                    "Resource": f"arn:aws:logs:{config.aws_region}:{aid}:log-group:*",
                },
                # Secrets Manager — describe only (Pulumi reads metadata during preview, not values)
                {
                    "Effect": "Allow",
                    "Action": [
                        "secretsmanager:DescribeSecret",
                    ],
                    "Resource": f"arn:aws:secretsmanager:{config.aws_region}:{aid}:secret:{config.name_prefix}-*",
                },
                # SQS — read
                {
                    "Effect": "Allow",
                    "Action": [
                        "sqs:GetQueueAttributes",
                        "sqs:GetQueueUrl",
                        "sqs:ListQueueTags",
                    ],
                    "Resource": f"arn:aws:sqs:{config.aws_region}:{aid}:{config.name_prefix}-*",
                },
                # EventBridge Scheduler — read
                {
                    "Effect": "Allow",
                    "Action": [
                        "scheduler:GetSchedule",
                        "scheduler:ListSchedules",
                    ],
                    "Resource": "*",
                },
                # EC2 — describe VPC/subnet/SG for networking preview
                {
                    "Effect": "Allow",
                    "Action": [
                        "ec2:DescribeVpcs",
                        "ec2:DescribeVpcAttribute",
                        "ec2:DescribeSubnets",
                        "ec2:DescribeSubnetAttribute",
                        "ec2:DescribeSecurityGroups",
                        "ec2:DescribeSecurityGroupRules",
                        "ec2:DescribeNetworkAcls",
                        "ec2:DescribeRouteTables",
                        "ec2:DescribeInternetGateways",
                    ],
                    "Resource": "*",
                },
                # STS — identify caller
                {
                    "Effect": "Allow",
                    "Action": "sts:GetCallerIdentity",
                    "Resource": "*",
                },
            ],
        }
    )
)

checks_role_policy = aws.iam.RolePolicy(
    "idi-policy-github-checks",
    role=checks_role.id,
    policy=_checks_policy,
)

# ---------------------------------------------------------------------------
# Deploy role — pulumi up + ECR push on merge (read/write)
# ---------------------------------------------------------------------------
deploy_role = aws.iam.Role(
    "idi-role-github-deploy",
    name=f"{config.name_prefix}-role-github-deploy",
    description="GitHub Actions: pulumi up and ECR push",
    assume_role_policy=_oidc_trust_policy(
        [
            f"repo:{GITHUB_REPO}:ref:refs/heads/main",
            f"repo:{GITHUB_REPO}:ref:refs/heads/dev",
            f"repo:{GITHUB_REPO}:ref:refs/heads/release/*",
        ]
    ),
    tags=config.tags({"purpose": "GitHub Actions CI deploy"}),
)

_deploy_policy = pulumi.Output.from_input(config.caller.account_id).apply(
    lambda aid: json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                # Pulumi state — read/write
                {
                    "Effect": "Allow",
                    "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"],
                    "Resource": [
                        f"arn:aws:s3:::{PULUMI_STATE_BUCKET}",
                        f"arn:aws:s3:::{PULUMI_STATE_BUCKET}/{PULUMI_STATE_PREFIX}/*",
                    ],
                },
                # ECR — full (create repo, push images, lifecycle)
                {
                    "Effect": "Allow",
                    "Action": ["ecr:GetAuthorizationToken"],
                    "Resource": "*",
                },
                {
                    "Effect": "Allow",
                    "Action": ["ecr:*"],
                    "Resource": f"arn:aws:ecr:{config.aws_region}:{aid}:repository/*",
                },
                # ECS — full (clusters, task definitions)
                {
                    "Effect": "Allow",
                    "Action": ["ecs:*"],
                    "Resource": "*",
                },
                # IAM — manage roles scoped to this app's prefix
                {
                    "Effect": "Allow",
                    "Action": [
                        "iam:CreateRole",
                        "iam:DeleteRole",
                        "iam:GetRole",
                        "iam:GetRolePolicy",
                        "iam:ListRolePolicies",
                        "iam:ListAttachedRolePolicies",
                        "iam:PutRolePolicy",
                        "iam:DeleteRolePolicy",
                        "iam:AttachRolePolicy",
                        "iam:DetachRolePolicy",
                        "iam:TagRole",
                        "iam:UntagRole",
                        "iam:UpdateAssumeRolePolicy",
                    ],
                    "Resource": f"arn:aws:iam::{aid}:role/{config.name_prefix}-*",
                },
                # IAM PassRole — allow ECS/Scheduler to use the app's roles
                {
                    "Effect": "Allow",
                    "Action": "iam:PassRole",
                    "Resource": f"arn:aws:iam::{aid}:role/{config.name_prefix}-*",
                    "Condition": {
                        "StringEquals": {
                            "iam:PassedToService": [
                                "ecs-tasks.amazonaws.com",
                                "scheduler.amazonaws.com",
                            ]
                        }
                    },
                },
                # CloudWatch Logs — full for log group management
                {
                    "Effect": "Allow",
                    "Action": [
                        "logs:CreateLogGroup",
                        "logs:DeleteLogGroup",
                        "logs:PutRetentionPolicy",
                        "logs:DescribeLogGroups",
                        "logs:ListTagsLogGroup",
                        "logs:TagLogGroup",
                        "logs:TagResource",
                    ],
                    "Resource": f"arn:aws:logs:{config.aws_region}:{aid}:log-group:*",
                },
                # Secrets Manager — full for this app's secrets
                {
                    "Effect": "Allow",
                    "Action": [
                        "secretsmanager:CreateSecret",
                        "secretsmanager:DeleteSecret",
                        "secretsmanager:DescribeSecret",
                        "secretsmanager:GetSecretValue",
                        "secretsmanager:PutSecretValue",
                        "secretsmanager:RestoreSecret",
                        "secretsmanager:TagResource",
                        "secretsmanager:UpdateSecret",
                    ],
                    "Resource": f"arn:aws:secretsmanager:{config.aws_region}:{aid}:secret:{config.name_prefix}-*",
                },
                # SQS — full for DLQ management
                {
                    "Effect": "Allow",
                    "Action": [
                        "sqs:CreateQueue",
                        "sqs:DeleteQueue",
                        "sqs:GetQueueAttributes",
                        "sqs:GetQueueUrl",
                        "sqs:ListQueueTags",
                        "sqs:SetQueueAttributes",
                        "sqs:TagQueue",
                    ],
                    "Resource": f"arn:aws:sqs:{config.aws_region}:{aid}:{config.name_prefix}-*",
                },
                # EventBridge Scheduler — full
                {
                    "Effect": "Allow",
                    "Action": [
                        "scheduler:CreateSchedule",
                        "scheduler:DeleteSchedule",
                        "scheduler:GetSchedule",
                        "scheduler:ListSchedules",
                        "scheduler:UpdateSchedule",
                        "scheduler:TagResource",
                    ],
                    "Resource": "*",
                },
                # EC2 — manage security groups, read VPC/subnet
                {
                    "Effect": "Allow",
                    "Action": [
                        "ec2:DescribeVpcs",
                        "ec2:DescribeSubnets",
                        "ec2:DescribeSecurityGroups",
                        "ec2:CreateSecurityGroup",
                        "ec2:DeleteSecurityGroup",
                        "ec2:AuthorizeSecurityGroupEgress",
                        "ec2:RevokeSecurityGroupEgress",
                        "ec2:CreateTags",
                    ],
                    "Resource": "*",
                },
                # STS — identify caller
                {
                    "Effect": "Allow",
                    "Action": "sts:GetCallerIdentity",
                    "Resource": "*",
                },
            ],
        }
    )
)

deploy_role_policy = aws.iam.RolePolicy(
    "idi-policy-github-deploy",
    role=deploy_role.id,
    policy=_deploy_policy,
)
