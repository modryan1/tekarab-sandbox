# /home/alaa/sandbox-demo/test_repo_matrix.py
from __future__ import annotations

import json
import sys
from typing import Dict, Any, List
import requests


BASE_URL = "http://127.0.0.1:5000"

ALLOWED_REPO_TYPES = {
    "cli_app",
    "web_api",
    "web_app",
    "library",
    "framework_source",
    "script_collection",
    "ml_experiment",
    "template_repo",
    "unclear",
}

ALLOWED_EXECUTION_READINESS = {
    "ready",
    "needs_env",
    "no_run",
    "unclear",
    "unsupported",
}

MATRIX = [
    {
        "name": "AutoResearchClaw",
        "repo_url": "https://github.com/aiming-lab/AutoResearchClaw",
        "expected_repo_type": "cli_app",
        "expected_readiness": "needs_env",
        "provided_env_vars": {"OPENAI_API_KEY": "set"},
        "expected_run_repo_allowed": False,
        "expected_run_repo_attempted": False,
    },
    {
        "name": "CPython",
        "repo_url": "https://github.com/python/cpython",
        "expected_repo_type": "framework_source",
        "expected_readiness": "no_run",
        "provided_env_vars": {},
        "expected_run_repo_allowed": False,
        "expected_run_repo_attempted": False,
    },
    {
        "name": "FastAPI",
        "repo_url": "https://github.com/fastapi/fastapi",
        "expected_repo_type": "web_api",
        "expected_readiness": "ready",
        "provided_env_vars": {},
        "expected_run_repo_allowed": False,
        "expected_run_repo_attempted": False,
    },
    {
        "name": "Streamlit",
        "repo_url": "https://github.com/streamlit/streamlit",
        "expected_repo_type": "web_app",
        "expected_readiness": "ready",
        "provided_env_vars": {},
        "expected_run_repo_allowed": True,
        "expected_run_repo_attempted": True,
        "expected_execution_success": False,
    },
    {
        "name": "Flask",
        "repo_url": "https://github.com/pallets/flask",
        "expected_repo_type": "cli_app",
        "expected_readiness": "ready",
        "provided_env_vars": {},
        "expected_run_repo_allowed": True,
        "expected_run_repo_attempted": True,
        "expected_execution_success": True,
    },
    {
        "name": "Nx",
        "repo_url": "https://github.com/nrwl/nx",
        "expected_repo_type": "web_app",
        "expected_readiness": "ready",
        "provided_env_vars": {},
        "expected_run_repo_allowed": True,
        "expected_run_repo_attempted": True,
        "expected_execution_success": False,
    },
    {
        "name": "Flipoff",
        "repo_url": "https://github.com/magnum6actual/flipoff.git",
        "expected_repo_type": "web_app",
        "expected_readiness": "ready",
        "provided_env_vars": {},
        "expected_run_repo_allowed": True,
        "expected_run_repo_attempted": True,
        "expected_execution_success": True,
    },
    {
        "name": "Gradio",
        "repo_url": "https://github.com/gradio-app/gradio",
        "expected_repo_type": "web_app",
        "expected_readiness": "ready",
        "provided_env_vars": {},
        "expected_run_repo_allowed": True,
        "expected_run_repo_attempted": True,
        "expected_execution_success": False,
        "expected_smart_error_hint_category": "node_version_mismatch",
    },
    {
        "name": "FullStackFastAPI",
        "repo_url": "https://github.com/fastapi/full-stack-fastapi-template",
        "expected_repo_type": "template_repo",
        "expected_readiness": "unclear",
        "provided_env_vars": {},
        "expected_run_repo_allowed": False,
        "expected_run_repo_attempted": False,
    },
    {
        "name": "Nextjs",
        "repo_url": "https://github.com/vercel/next.js",
        "expected_repo_type": "web_app",
        "expected_readiness": "ready",
        "provided_env_vars": {},
        "expected_run_repo_allowed": True,
        "expected_run_repo_attempted": True,
        "expected_execution_success": True,
    },
]


def assert_true(condition: bool, message: str):
    if not condition:
        raise AssertionError(message)


def validate_decision_shape(decision: Dict[str, Any]):
    required_top_fields = [
        "repo_url",
        "detected_language",
        "repo_type_guess",
        "execution_readiness",
        "support_tier",
        "risk_level",
        "confidence_overall",
        "required_env_vars",
        "external_services_detected",
        "entry_candidates",
        "recommended_plan",
    ]

    for field in required_top_fields:
        assert_true(field in decision, f"Missing field in decision: {field}")

    assert_true(
        decision["repo_type_guess"] in ALLOWED_REPO_TYPES,
        f"Invalid repo_type_guess: {decision['repo_type_guess']}"
    )
    assert_true(
        decision["execution_readiness"] in ALLOWED_EXECUTION_READINESS,
        f"Invalid execution_readiness: {decision['execution_readiness']}"
    )

    assert_true(isinstance(decision["required_env_vars"], list), "required_env_vars must be a list")
    assert_true(isinstance(decision["external_services_detected"], list), "external_services_detected must be a list")
    assert_true(isinstance(decision["entry_candidates"], list), "entry_candidates must be a list")

    plan = decision["recommended_plan"]
    assert_true(isinstance(plan, dict), "recommended_plan must be an object")

    for field in ["setup_steps", "run_steps", "notes", "blockers"]:
        assert_true(field in plan, f"recommended_plan missing field: {field}")
        assert_true(isinstance(plan[field], list), f"recommended_plan['{field}'] must be a list")

    if decision["execution_readiness"] == "no_run":
        assert_true(len(plan["run_steps"]) == 0, "run_steps must be empty when execution_readiness=no_run")


def validate_preview_shape(preview: Dict[str, Any]):
    required_fields = [
        "summary",
        "setup_steps",
        "run_steps",
        "required_env_vars",
        "warnings",
        "safe_to_attempt",
    ]

    for field in required_fields:
        assert_true(field in preview, f"Missing field in preview: {field}")

    assert_true(isinstance(preview["summary"], str), "preview['summary'] must be a string")
    assert_true(isinstance(preview["setup_steps"], list), "preview['setup_steps'] must be a list")
    assert_true(isinstance(preview["run_steps"], list), "preview['run_steps'] must be a list")
    assert_true(isinstance(preview["required_env_vars"], list), "preview['required_env_vars'] must be a list")
    assert_true(isinstance(preview["warnings"], list), "preview['warnings'] must be a list")
    assert_true(isinstance(preview["safe_to_attempt"], bool), "preview['safe_to_attempt'] must be a bool")


def validate_validation_shape(validation: Dict[str, Any]):
    required_fields = [
        "is_valid",
        "can_proceed_to_execution",
        "missing_env_vars",
        "provided_env_vars",
        "required_env_vars",
        "setup_steps",
        "run_steps",
        "blockers",
        "notes",
    ]

    for field in required_fields:
        assert_true(field in validation, f"Missing field in validation: {field}")

    assert_true(isinstance(validation["is_valid"], bool), "validation['is_valid'] must be a bool")
    assert_true(isinstance(validation["can_proceed_to_execution"], bool), "validation['can_proceed_to_execution'] must be a bool")
    assert_true(isinstance(validation["missing_env_vars"], list), "validation['missing_env_vars'] must be a list")
    assert_true(isinstance(validation["provided_env_vars"], list), "validation['provided_env_vars'] must be a list")
    assert_true(isinstance(validation["required_env_vars"], list), "validation['required_env_vars'] must be a list")
    assert_true(isinstance(validation["setup_steps"], list), "validation['setup_steps'] must be a list")
    assert_true(isinstance(validation["run_steps"], list), "validation['run_steps'] must be a list")
    assert_true(isinstance(validation["blockers"], list), "validation['blockers'] must be a list")
    assert_true(isinstance(validation["notes"], list), "validation['notes'] must be a list")


def validate_run_policy_shape(run_policy: Dict[str, Any]):
    required_fields = [
        "is_allowed",
        "policy_blockers",
        "blocked_commands",
        "max_setup_steps",
        "max_run_steps",
        "allowed_repo_types",
        "allowed_execution_readiness",
        "allowed_support_tiers",
    ]

    for field in required_fields:
        assert_true(field in run_policy, f"Missing field in run_policy: {field}")

    assert_true(isinstance(run_policy["is_allowed"], bool), "run_policy['is_allowed'] must be a bool")
    assert_true(isinstance(run_policy["policy_blockers"], list), "run_policy['policy_blockers'] must be a list")
    assert_true(isinstance(run_policy["blocked_commands"], list), "run_policy['blocked_commands'] must be a list")
    assert_true(isinstance(run_policy["max_setup_steps"], int), "run_policy['max_setup_steps'] must be an int")
    assert_true(isinstance(run_policy["max_run_steps"], int), "run_policy['max_run_steps'] must be an int")
    assert_true(isinstance(run_policy["allowed_repo_types"], list), "run_policy['allowed_repo_types'] must be a list")
    assert_true(isinstance(run_policy["allowed_execution_readiness"], list), "run_policy['allowed_execution_readiness'] must be a list")
    assert_true(isinstance(run_policy["allowed_support_tiers"], list), "run_policy['allowed_support_tiers'] must be a list")


def validate_execution_shape(execution: Dict[str, Any]):
    required_fields = [
        "attempted",
        "success",
        "reason",
        "policy_blockers",
        "blocked_commands",
        "setup_steps_executed",
        "run_steps_executed",
        "logs",
    ]

    for field in required_fields:
        assert_true(field in execution, f"Missing field in execution: {field}")

    assert_true(isinstance(execution["attempted"], bool), "execution['attempted'] must be a bool")
    assert_true(isinstance(execution["success"], bool), "execution['success'] must be a bool")
    assert_true(isinstance(execution["reason"], str), "execution['reason'] must be a string")
    assert_true(isinstance(execution["policy_blockers"], list), "execution['policy_blockers'] must be a list")
    assert_true(isinstance(execution["blocked_commands"], list), "execution['blocked_commands'] must be a list")
    assert_true(isinstance(execution["setup_steps_executed"], list), "execution['setup_steps_executed'] must be a list")
    assert_true(isinstance(execution["run_steps_executed"], list), "execution['run_steps_executed'] must be a list")
    assert_true(isinstance(execution["logs"], list), "execution['logs'] must be a list")


def run_prepare_case(case: Dict[str, Any]) -> Dict[str, Any]:
    print("=" * 80)
    print(f"Running prepare case: {case['name']}")
    print(f"Repo URL: {case['repo_url']}")

    response = requests.post(
        f"{BASE_URL}/prepare-repo-run",
        json={"repo_url": case["repo_url"]},
        timeout=120,
    )

    assert_true(response.status_code == 200, f"Unexpected HTTP status: {response.status_code}")
    payload = response.json()

    assert_true(payload.get("ok") is True, f"Endpoint returned ok=False: {payload}")
    assert_true("analysis" in payload, "Missing analysis in response")

    decision = payload["analysis"]
    validate_decision_shape(decision)

    assert_true(
        decision["repo_type_guess"] == case["expected_repo_type"],
        f"Expected repo_type_guess={case['expected_repo_type']} but got {decision['repo_type_guess']}"
    )

    assert_true(
        decision["execution_readiness"] == case["expected_readiness"],
        f"Expected execution_readiness={case['expected_readiness']} but got {decision['execution_readiness']}"
    )

    print(json.dumps({
        "stage": "prepare",
        "name": case["name"],
        "repo_url": case["repo_url"],
        "detected_language": decision["detected_language"],
        "repo_type_guess": decision["repo_type_guess"],
        "execution_readiness": decision["execution_readiness"],
        "support_tier": decision["support_tier"],
        "risk_level": decision["risk_level"],
        "confidence_overall": decision["confidence_overall"],
        "required_env_vars_count": len(decision["required_env_vars"]),
    }, indent=2, ensure_ascii=False))

    return {
        "name": case["name"],
        "stage": "prepare",
        "passed": True,
    }


def run_preview_case(case: Dict[str, Any]) -> Dict[str, Any]:
    print("=" * 80)
    print(f"Running preview case: {case['name']}")
    print(f"Repo URL: {case['repo_url']}")

    response = requests.post(
        f"{BASE_URL}/preview-repo-run",
        json={"repo_url": case["repo_url"]},
        timeout=120,
    )

    assert_true(response.status_code == 200, f"Unexpected HTTP status: {response.status_code}")
    payload = response.json()

    assert_true(payload.get("ok") is True, f"Endpoint returned ok=False: {payload}")
    assert_true("analysis" in payload, "Missing raw analysis in preview response")
    assert_true("decision" in payload, "Missing decision in preview response")
    assert_true("preview" in payload, "Missing preview section in preview response")

    decision = payload["decision"]
    preview = payload["preview"]

    validate_decision_shape(decision)
    validate_preview_shape(preview)

    assert_true(
        decision["repo_type_guess"] == case["expected_repo_type"],
        f"Expected repo_type_guess={case['expected_repo_type']} but got {decision['repo_type_guess']}"
    )

    assert_true(
        decision["execution_readiness"] == case["expected_readiness"],
        f"Expected execution_readiness={case['expected_readiness']} but got {decision['execution_readiness']}"
    )

    if case["expected_readiness"] == "needs_env":
        assert_true(
            len(preview["required_env_vars"]) > 0,
            "preview required_env_vars should not be empty when execution_readiness=needs_env"
        )

    if case["expected_readiness"] == "no_run":
        assert_true(
            len(preview["run_steps"]) == 0,
            "preview run_steps must be empty when execution_readiness=no_run"
        )

    print(json.dumps({
        "stage": "preview",
        "name": case["name"],
        "repo_url": case["repo_url"],
        "repo_type_guess": decision["repo_type_guess"],
        "execution_readiness": decision["execution_readiness"],
        "preview_safe_to_attempt": preview["safe_to_attempt"],
        "preview_warning_count": len(preview["warnings"]),
        "preview_run_steps_count": len(preview["run_steps"]),
    }, indent=2, ensure_ascii=False))

    return {
        "name": case["name"],
        "stage": "preview",
        "passed": True,
    }


def run_validation_case(case: Dict[str, Any], scenario: str, provided_env_vars: Dict[str, Any]) -> Dict[str, Any]:
    print("=" * 80)
    print(f"Running validation case: {case['name']} [{scenario}]")
    print(f"Repo URL: {case['repo_url']}")

    response = requests.post(
        f"{BASE_URL}/validate-repo-run-request",
        json={
            "repo_url": case["repo_url"],
            "provided_env_vars": provided_env_vars,
        },
        timeout=120,
    )

    assert_true(response.status_code == 200, f"Unexpected HTTP status: {response.status_code}")
    payload = response.json()

    assert_true(payload.get("ok") is True, f"Endpoint returned ok=False: {payload}")
    assert_true("decision" in payload, "Missing decision in validation response")
    assert_true("validation" in payload, "Missing validation in validation response")

    decision = payload["decision"]
    validation = payload["validation"]

    validate_decision_shape(decision)
    validate_validation_shape(validation)

    assert_true(
        decision["repo_type_guess"] == case["expected_repo_type"],
        f"Expected repo_type_guess={case['expected_repo_type']} but got {decision['repo_type_guess']}"
    )

    assert_true(
        decision["execution_readiness"] == case["expected_readiness"],
        f"Expected execution_readiness={case['expected_readiness']} but got {decision['execution_readiness']}"
    )

    expected_readiness = case["expected_readiness"]

    if expected_readiness == "needs_env" and scenario == "provided":
        assert_true(validation["is_valid"] is True, "validation is_valid should be true when required env vars are provided")
        assert_true(validation["can_proceed_to_execution"] is True, "can_proceed_to_execution should be true when required env vars are provided")
        assert_true(len(validation["missing_env_vars"]) == 0, "missing_env_vars should be empty when required env vars are provided")
        assert_true(len(validation["blockers"]) == 0, "blockers should be empty when validation passes")
        assert_true(len(validation["provided_env_vars"]) > 0, "provided_env_vars should not be empty in provided scenario")

    elif expected_readiness == "needs_env" and scenario == "missing":
        assert_true(validation["is_valid"] is False, "validation is_valid should be false when required env vars are missing")
        assert_true(validation["can_proceed_to_execution"] is False, "can_proceed_to_execution should be false when required env vars are missing")
        assert_true(len(validation["missing_env_vars"]) > 0, "missing_env_vars should not be empty when required env vars are missing")
        assert_true(len(validation["blockers"]) > 0, "blockers should not be empty when validation fails due to missing env vars")

    elif expected_readiness == "no_run":
        assert_true(validation["is_valid"] is False, "validation is_valid should be false for no_run repositories")
        assert_true(validation["can_proceed_to_execution"] is False, "can_proceed_to_execution should be false for no_run repositories")
        assert_true(len(validation["run_steps"]) == 0, "validation run_steps must be empty for no_run repositories")

    elif expected_readiness == "ready":
        assert_true(validation["is_valid"] is True, "validation is_valid should be true for ready repositories")
        assert_true(validation["can_proceed_to_execution"] is True, "can_proceed_to_execution should be true for ready repositories")

    else:
        assert_true(validation["can_proceed_to_execution"] is False, "Unexpected validation state for unsupported readiness type")

    print(json.dumps({
        "stage": "validation",
        "scenario": scenario,
        "name": case["name"],
        "repo_url": case["repo_url"],
        "repo_type_guess": decision["repo_type_guess"],
        "execution_readiness": decision["execution_readiness"],
        "is_valid": validation["is_valid"],
        "can_proceed_to_execution": validation["can_proceed_to_execution"],
        "missing_env_vars_count": len(validation["missing_env_vars"]),
        "blockers_count": len(validation["blockers"]),
    }, indent=2, ensure_ascii=False))

    return {
        "name": case["name"],
        "stage": f"validation:{scenario}",
        "passed": True,
    }


def run_run_repo_case(case: Dict[str, Any]) -> Dict[str, Any]:
    print("=" * 80)
    print(f"Running run-repo case: {case['name']}")
    print(f"Repo URL: {case['repo_url']}")

    response = requests.post(
        f"{BASE_URL}/run-repo",
        json={
            "repo_url": case["repo_url"],
            "provided_env_vars": case.get("provided_env_vars", {}),
        },
        timeout=120,
    )

    assert_true(response.status_code == 200, f"Unexpected HTTP status: {response.status_code}")
    payload = response.json()

    assert_true(payload.get("ok") is True, f"Endpoint returned ok=False: {payload}")
    assert_true("decision" in payload, "Missing decision in run-repo response")
    assert_true("validation" in payload, "Missing validation in run-repo response")
    assert_true("run_policy" in payload, "Missing run_policy in run-repo response")
    assert_true("execution" in payload, "Missing execution in run-repo response")

    decision = payload["decision"]
    validation = payload["validation"]
    run_policy = payload["run_policy"]
    execution = payload["execution"]

    validate_decision_shape(decision)
    validate_validation_shape(validation)
    validate_run_policy_shape(run_policy)
    validate_execution_shape(execution)

    assert_true(
        decision["repo_type_guess"] == case["expected_repo_type"],
        f"Expected repo_type_guess={case['expected_repo_type']} but got {decision['repo_type_guess']}"
    )

    assert_true(
        decision["execution_readiness"] == case["expected_readiness"],
        f"Expected execution_readiness={case['expected_readiness']} but got {decision['execution_readiness']}"
    )

    assert_true(
        run_policy["is_allowed"] == case["expected_run_repo_allowed"],
        f"Expected run_policy.is_allowed={case['expected_run_repo_allowed']} but got {run_policy['is_allowed']}"
    )

    assert_true(
        execution["attempted"] == case["expected_run_repo_attempted"],
        f"Expected execution.attempted={case['expected_run_repo_attempted']} but got {execution['attempted']}"
    )

    if "expected_execution_success" in case:
        assert_true(
            execution["success"] == case["expected_execution_success"],
            f"Expected execution.success={case['expected_execution_success']} but got {execution['success']}"
        )

    if "expected_smart_error_hint_category" in case:
        smart_error_hint = execution.get("smart_error_hint")
        assert_true(
            isinstance(smart_error_hint, dict),
            "Expected execution.smart_error_hint to be present as a dict"
        )
        assert_true(
            smart_error_hint.get("category") == case["expected_smart_error_hint_category"],
            f"Expected execution.smart_error_hint.category={case['expected_smart_error_hint_category']} but got {smart_error_hint.get('category')}"
        )

    if not case["expected_run_repo_allowed"]:
        assert_true(execution["success"] is False, "execution.success should be false when run policy blocks execution")
        assert_true(len(run_policy["policy_blockers"]) > 0, "run_policy.policy_blockers should not be empty when execution is blocked")
        assert_true(len(execution["setup_steps_executed"]) == 0, "No setup steps should execute when execution is blocked")
        assert_true(len(execution["run_steps_executed"]) == 0, "No run steps should execute when execution is blocked")
        assert_true(len(execution["logs"]) == 0, "No logs should exist when execution is blocked")

    print(json.dumps({
        "stage": "run-repo",
        "name": case["name"],
        "repo_url": case["repo_url"],
        "repo_type_guess": decision["repo_type_guess"],
        "execution_readiness": decision["execution_readiness"],
        "run_policy_allowed": run_policy["is_allowed"],
        "policy_blockers_count": len(run_policy["policy_blockers"]),
        "blocked_commands_count": len(run_policy["blocked_commands"]),
        "execution_attempted": execution["attempted"],
        "execution_success": execution["success"],
    }, indent=2, ensure_ascii=False))

    return {
        "name": case["name"],
        "stage": "run-repo",
        "passed": True,
    }


def main():
    print("\n" + "=" * 80)
    print("Tekarab Sandbox Repo Validation Matrix")
    print("=" * 80 + "\n")

    results: List[Dict[str, Any]] = []

    for case in MATRIX:
        if case.get("skip_case"):
            reason = case.get("skip_reason", "Skipped")
            print("=" * 80)
            print(f"[SKIP][case] {case['name']} -> {reason}\n")
            continue

        try:
            result = run_prepare_case(case)
            results.append(result)
            print(f"[PASS][prepare] {case['name']}\n")
        except Exception as e:
            results.append({"name": case["name"], "stage": "prepare", "passed": False, "error": str(e)})
            print(f"[FAIL][prepare] {case['name']} -> {e}\n")

        try:
            result = run_preview_case(case)
            results.append(result)
            print(f"[PASS][preview] {case['name']}\n")
        except Exception as e:
            results.append({"name": case["name"], "stage": "preview", "passed": False, "error": str(e)})
            print(f"[FAIL][preview] {case['name']} -> {e}\n")

        if case["expected_readiness"] == "needs_env":
            try:
                result = run_validation_case(case, "provided", case.get("provided_env_vars", {}))
                results.append(result)
                print(f"[PASS][validation:provided] {case['name']}\n")
            except Exception as e:
                results.append({"name": case["name"], "stage": "validation:provided", "passed": False, "error": str(e)})
                print(f"[FAIL][validation:provided] {case['name']} -> {e}\n")

            try:
                result = run_validation_case(case, "missing", {})
                results.append(result)
                print(f"[PASS][validation:missing] {case['name']}\n")
            except Exception as e:
                results.append({"name": case["name"], "stage": "validation:missing", "passed": False, "error": str(e)})
                print(f"[FAIL][validation:missing] {case['name']} -> {e}\n")
        else:
            try:
                result = run_validation_case(case, "default", case.get("provided_env_vars", {}))
                results.append(result)
                print(f"[PASS][validation:default] {case['name']}\n")
            except Exception as e:
                results.append({"name": case["name"], "stage": "validation:default", "passed": False, "error": str(e)})
                print(f"[FAIL][validation:default] {case['name']} -> {e}\n")

        if case.get("skip_run_repo"):
            print(f"[SKIP][run-repo] {case['name']} -> run-repo expectation not yet confirmed\n")
            continue

        try:
            result = run_run_repo_case(case)
            results.append(result)
            print(f"[PASS][run-repo] {case['name']}\n")
        except Exception as e:
            results.append({"name": case["name"], "stage": "run-repo", "passed": False, "error": str(e)})
            print(f"[FAIL][run-repo] {case['name']} -> {e}\n")

    passed = sum(1 for r in results if r["passed"])
    total = len(results)

    print("=" * 80)
    print(f"FINAL RESULT: PASS {passed}/{total}")
    print("=" * 80)

    if passed != total:
        sys.exit(1)


if __name__ == "__main__":
    main()
