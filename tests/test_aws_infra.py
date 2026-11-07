"""Tests for the AWS files (infra/aws, Dockerfile.lambda, the deploy workflow; DL-118).

They cannot create anything in AWS, so they pin down the properties that keep the deployment safe:
the deploy role can be assumed only from the main branch of this repository and can touch only the
one repository and the one function, no key or secret is written in any file, the API key is a
hidden parameter, the public URL has both permissions Lambda needs, and the deploy workflow runs
only by hand, only on main, and keeps no stored credentials."""

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
INFRA = ROOT / "infra" / "aws"
WORKFLOW = ROOT / ".github" / "workflows" / "deploy-aws.yml"


class _Loader(yaml.SafeLoader):
    """CloudFormation's short forms (!Ref, !Sub, ...) read as plain data."""


def _tag(loader, suffix, node):
    if isinstance(node, yaml.ScalarNode):
        return {suffix: loader.construct_scalar(node)}
    if isinstance(node, yaml.SequenceNode):
        return {suffix: loader.construct_sequence(node, deep=True)}
    return {suffix: loader.construct_mapping(node, deep=True)}


_Loader.add_multi_constructor("!", _tag)


def template(name: str) -> dict:
    return yaml.load((INFRA / name).read_text(encoding="utf-8"), Loader=_Loader)  # noqa: S506


@pytest.fixture(scope="module")
def bootstrap():
    return template("bootstrap.yml")


@pytest.fixture(scope="module")
def api():
    return template("api.yml")


def _statements(role: dict) -> list[dict]:
    return role["Properties"]["Policies"][0]["PolicyDocument"]["Statement"]


# ------------------------------------------------------------------ the deploy role


def test_the_deploy_role_can_only_be_assumed_from_the_main_branch_of_this_repository(bootstrap):
    role = bootstrap["Resources"]["DeployRole"]["Properties"]
    (statement,) = role["AssumeRolePolicyDocument"]["Statement"]
    assert statement["Action"] == "sts:AssumeRoleWithWebIdentity"
    condition = statement["Condition"]["StringEquals"]
    assert condition["token.actions.githubusercontent.com:aud"] == "sts.amazonaws.com"
    subject = condition["token.actions.githubusercontent.com:sub"]["Sub"]
    assert subject == "${GitHubSubjectPrefix}:ref:refs/heads/main"  # an exact match, no wildcard
    # GitHub names the repository by immutable IDs (owner@id/name@id), so a renamed or re-created
    # repository cannot match; the plain name form is refused by GitHub's current tokens
    prefix = bootstrap["Parameters"]["GitHubSubjectPrefix"]["Default"]
    assert re.fullmatch(r"repo:tus2014ar@\d+/oa-market-intelligence-system@\d+", prefix)


def test_the_deploy_role_can_reach_only_one_repository_and_one_function(bootstrap):
    statements = _statements(bootstrap["Resources"]["DeployRole"])
    for s in statements:
        actions = s["Action"] if isinstance(s["Action"], list) else [s["Action"]]
        assert not any(a.endswith("*") for a in actions), actions  # no wildcard action
        assert s["Effect"] == "Allow"
    by_id = {s["Sid"]: s for s in statements}
    assert by_id["LoginToTheRegistry"]["Resource"] == "*"  # the one call that cannot be narrowed
    assert by_id["LoginToTheRegistry"]["Action"] == "ecr:GetAuthorizationToken"
    assert by_id["PushToThisRepositoryOnly"]["Resource"] == {"GetAtt": "ImageRepository.Arn"}
    function = by_id["UpdateThisFunctionOnly"]
    assert function["Resource"]["Sub"].endswith(":function:${FunctionName}")
    assert "lambda:UpdateFunctionCode" in function["Action"]
    assert not any(
        a.startswith(("iam:", "cloudformation:", "s3:"))
        for s in statements
        for a in (s["Action"] if isinstance(s["Action"], list) else [s["Action"]])
    )


def test_the_image_repository_keeps_tags_fixed_scans_on_push_and_expires_old_images(bootstrap):
    repo = bootstrap["Resources"]["ImageRepository"]["Properties"]
    assert repo["ImageTagMutability"] == "IMMUTABLE"
    assert repo["ImageScanningConfiguration"]["ScanOnPush"] is True
    assert '"countNumber":3' in repo["LifecyclePolicy"]["LifecyclePolicyText"].replace("\n", "")


def test_the_bootstrap_creates_no_function_and_holds_no_secret(bootstrap):
    types = {r["Type"] for r in bootstrap["Resources"].values()}
    assert types == {"AWS::ECR::Repository", "AWS::IAM::OIDCProvider", "AWS::IAM::Role"}


# ------------------------------------------------------------------ the function


def test_the_api_key_is_a_hidden_parameter_and_is_nowhere_in_the_files(api):
    key = api["Parameters"]["ApiKeys"]
    assert key["NoEcho"] is True and key["MinLength"] >= 16 and "Default" not in key
    for path in [*INFRA.glob("*"), WORKFLOW, ROOT / "Dockerfile.lambda"]:
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"AKIA[0-9A-Z]{16}", text), path  # an AWS access key id
        assert "aws_secret_access_key" not in text.lower(), path
        assert not re.search(r"API_KEYS\s*[=:]\s*['\"]?[A-Za-z0-9]{8,}", text), path


def test_the_function_is_a_container_image_with_modest_size_and_a_limited_role(api):
    function = api["Resources"]["Function"]["Properties"]
    assert function["PackageType"] == "Image" and function["Architectures"] == ["x86_64"]
    assert function["Timeout"] <= 30 and function["MemorySize"] <= 1024
    role = api["Resources"]["ExecutionRole"]["Properties"]
    assert len(role["ManagedPolicyArns"]) == 1
    assert role["ManagedPolicyArns"][0]["Sub"].endswith("service-role/AWSLambdaBasicExecutionRole")
    assert "Policies" not in role  # it may write its own logs and nothing else


def test_the_concurrency_limit_is_optional_because_a_new_account_may_not_allow_it(api):
    assert api["Parameters"]["ReservedConcurrency"]["Default"] == 0
    reserved = api["Resources"]["Function"]["Properties"]["ReservedConcurrentExecutions"]
    assert reserved["If"][0] == "LimitConcurrency" and reserved["If"][2] == {"Ref": "AWS::NoValue"}


def test_the_public_url_has_both_permissions_lambda_requires_and_logs_expire(api):
    resources = api["Resources"]
    assert resources["FunctionUrl"]["Properties"]["AuthType"] == "NONE"
    url = resources["AllowUrlCalls"]["Properties"]
    assert url["Action"] == "lambda:InvokeFunctionUrl" and url["FunctionUrlAuthType"] == "NONE"
    invoke = resources["AllowInvokeThroughTheUrl"]["Properties"]
    assert invoke["Action"] == "lambda:InvokeFunction"
    assert invoke["InvokedViaFunctionUrl"] is True  # only through the URL, not by other callers
    assert resources["LogGroup"]["Properties"]["RetentionInDays"] == 14


# ------------------------------------------------------------------ the workflow and the image


def test_the_deploy_workflow_is_manual_main_only_and_uses_no_stored_credentials():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    triggers = workflow.get("on", workflow.get(True))
    assert list(triggers) == ["workflow_dispatch"]  # no push, no schedule
    assert workflow["permissions"] == {"contents": "read", "id-token": "write"}
    job = workflow["jobs"]["deploy"]
    assert job["if"] == "github.ref == 'refs/heads/main'"
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "secrets." not in text and "AWS_ACCESS_KEY_ID" not in text
    assert "vars.AWS_DEPLOY_ROLE_ARN" in text and "vars.AWS_REGION" in text
    assert "--provenance" not in text  # a plain image manifest: Lambda rejects an OCI index


def test_the_lambda_image_runs_the_handler_with_no_key_in_it():
    text = (ROOT / "Dockerfile.lambda").read_text(encoding="utf-8")
    assert "FROM public.ecr.aws/lambda/python:3.12" in text
    assert 'CMD ["oa_market_intelligence.api.lambda_handler.handler"]' in text
    assert "COPY data/published" in text and "API_KEYS" not in text.replace("API_KEYS=choose", "")
    assert "mangum" in (ROOT / "api" / "requirements-lambda.txt").read_text(encoding="utf-8")
