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
CANARY = ROOT / ".github" / "workflows" / "canary.yml"


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


def test_the_deploy_workflow_runs_only_from_main_by_hand_or_after_a_successful_publish():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    triggers = workflow.get("on", workflow.get(True))
    assert list(triggers) == ["workflow_dispatch", "workflow_run"]  # no push, no schedule
    assert triggers["workflow_run"] == {"workflows": ["Monthly Pipeline"], "types": ["completed"]}
    assert workflow["permissions"] == {"contents": "read", "id-token": "write"}
    condition = " ".join(workflow["jobs"]["deploy"]["if"].split())
    assert condition.startswith("github.ref == 'refs/heads/main' &&")
    # after a publish only when it succeeded and ran on main
    assert "github.event.workflow_run.conclusion == 'success'" in condition
    assert "github.event.workflow_run.head_branch == 'main'" in condition
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "secrets." not in text and "AWS_ACCESS_KEY_ID" not in text
    assert "vars.AWS_DEPLOY_ROLE_ARN" in text and "vars.AWS_REGION" in text
    assert "--provenance" not in text  # a plain image manifest: Lambda rejects an OCI index


def test_the_deploy_workflow_skips_when_current_checks_freshness_and_can_roll_back():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "The live API already serves this data" in text  # no rebuild when nothing was published
    assert "does not serve the data version in the repository" in text  # the freshness gate
    assert 'MODE" = "deploy"' in text  # a rollback is not held to the repository's data version
    workflow = yaml.safe_load(text)
    inputs = (workflow.get("on", workflow.get(True)))["workflow_dispatch"]["inputs"]
    assert inputs["rollback_to"]["required"] is False
    assert "[0-9a-f]{40}" in text  # the rollback input is checked before it is used
    assert "ecr describe-images" in text  # only an image that exists can be rolled back to


def test_the_function_address_is_masked_in_every_workflow_log():
    for path in (WORKFLOW, CANARY):
        text = path.read_text(encoding="utf-8")
        assert "::add-mask::" in text, path
        assert "echo $URL" not in text and 'echo "$URL"' not in text, path  # never printed


# ------------------------------------------------------------------ the canary


def test_the_canary_runs_daily_checks_health_auth_and_data_and_holds_no_key():
    workflow = yaml.safe_load(CANARY.read_text(encoding="utf-8"))
    triggers = workflow.get("on", workflow.get(True))
    assert set(triggers) == {"schedule", "workflow_dispatch"}
    assert len(triggers["schedule"]) == 1 and triggers["schedule"][0]["cron"].endswith("* * *")
    assert workflow["permissions"] == {"contents": "read"}  # it cannot change anything
    text = CANARY.read_text(encoding="utf-8")
    assert "secrets.API_URL" in text and "X-API-Key" not in text  # the address only, no key
    assert '"auth":"api-key"' in text  # fails if the API stops asking for a key
    assert '"$CODE" = "401"' in text  # and if an unkeyed call is no longer refused
    assert "a different data version" in text  # and if the live data are not the repository's
    assert "AWS_" not in text and "id-token" not in text  # it needs no AWS access at all


# ------------------------------------------------------------------ monitoring


@pytest.fixture(scope="module")
def monitoring():
    return template("monitoring.yml")


def test_every_alarm_notifies_the_topic_and_a_quiet_function_is_not_an_alarm(monitoring):
    resources = monitoring["Resources"]
    alarms = {
        k: v["Properties"] for k, v in resources.items() if v["Type"] == "AWS::CloudWatch::Alarm"
    }
    assert set(alarms) == {
        "FunctionErrorsAlarm", "ThrottlesAlarm", "ServerErrorsAlarm", "SlowAlarm",
        "RejectedKeysAlarm",
    }  # fmt: skip
    for name, alarm in alarms.items():
        assert alarm["AlarmActions"] == [{"Ref": "AlertTopic"}], name
        assert alarm["OKActions"] == [{"Ref": "AlertTopic"}], name  # and say when it clears
        assert alarm["TreatMissingData"] == "notBreaching", name  # no traffic is not a failure
    assert (
        alarms["SlowAlarm"]["ExtendedStatistic"] == "p95"
        and alarms["SlowAlarm"]["EvaluationPeriods"] == 3
    )


def test_the_alert_email_is_a_parameter_with_no_default_and_the_topic_has_one_subscriber(
    monitoring,
):
    email = monitoring["Parameters"]["AlertEmail"]
    assert "Default" not in email and "AllowedPattern" in email  # typed by the owner, never stored
    subscription = monitoring["Resources"]["AlertSubscription"]["Properties"]
    assert subscription["Protocol"] == "email" and subscription["Endpoint"] == {"Ref": "AlertEmail"}
    text = (INFRA / "monitoring.yml").read_text(encoding="utf-8")
    assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text)  # no address is written in the file


def test_the_log_metrics_read_the_apis_json_access_line_and_name_each_outcome(monitoring):
    filters = {
        k: v["Properties"] for k, v in monitoring["Resources"].items()
        if v["Type"] == "AWS::Logs::MetricFilter"
    }  # fmt: skip
    patterns = {
        f["MetricTransformations"][0]["MetricName"]: f["FilterPattern"] for f in filters.values()
    }
    assert patterns == {
        "Requests": "{ $.status > 0 }",
        "ServerErrors": "{ $.status >= 500 }",
        "RejectedKeys": "{ $.status = 401 }",
        "RateLimited": "{ $.status = 429 }",
    }
    for f in filters.values():
        assert f["LogGroupName"] == {
            "Sub": "/aws/lambda/${FunctionName}"
        }  # the function's own log group
        assert f["MetricTransformations"][0]["MetricNamespace"] == "OAMarketApi"


def test_the_dashboard_body_is_valid_json_with_four_widgets_on_the_functions_metrics(monitoring):
    import json

    body = monitoring["Resources"]["Dashboard"]["Properties"]["DashboardBody"]["Sub"]
    body = body.replace("${AWS::Region}", "us-east-1").replace("${FunctionName}", "oa-market-api")
    widgets = json.loads(body)["widgets"]
    assert len(widgets) == 4
    titles = {w["properties"]["title"] for w in widgets}
    assert titles == {
        "Requests",
        "Run time (ms)",
        "Crashes and throttles",
        "Copies running at once",
    }
    assert all(w["properties"]["region"] == "us-east-1" for w in widgets)


def test_the_monitoring_stack_creates_no_function_and_changes_nothing_in_it(monitoring):
    types = {r["Type"] for r in monitoring["Resources"].values()}
    assert types == {
        "AWS::SNS::Topic", "AWS::SNS::Subscription", "AWS::Logs::MetricFilter",
        "AWS::CloudWatch::Alarm", "AWS::CloudWatch::Dashboard",
    }  # fmt: skip


def test_the_lambda_image_runs_the_handler_with_no_key_in_it():
    text = (ROOT / "Dockerfile.lambda").read_text(encoding="utf-8")
    assert "FROM public.ecr.aws/lambda/python:3.12" in text
    assert 'CMD ["oa_market_intelligence.api.lambda_handler.handler"]' in text
    assert "COPY data/published" in text and "API_KEYS" not in text.replace("API_KEYS=choose", "")
    assert "mangum" in (ROOT / "api" / "requirements-lambda.txt").read_text(encoding="utf-8")
