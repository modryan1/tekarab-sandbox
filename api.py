# /home/alaa/sandbox-demo/api.py
from __future__ import annotations

import os
import re
import sys
import shutil
import socket
import time
import traceback
import threading
import subprocess
import requests
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

from flask import Flask, jsonify, request, make_response

from command_rewriter import apply_rewrite_actions
from repo_decision import build_repo_decision
from repo_analyzer import analyze_repo as RAW_ANALYZE_FUNC
from repo_analyzer import clone_repo_shallow
from presentation_resolver import resolve_presentation
from smart_error_hints import build_smart_error_hint
from smart_timeout import build_timeout_metadata
from session_runtime import (
    cleanup_expired_sessions,
    create_session_from_repo_dir,
    delete_session as delete_runtime_session,
    get_session_status,
    list_session_files,
    read_session_command_log,
    read_session_file_content,
    run_session_command,
    load_session_meta,
    save_session_meta,
    session_command_log_path,
    _append_text,
    _pid_is_running,
)

app = Flask(__name__)

ALLOWED_CORS_ORIGINS = {
    "http://187.124.43.158",
    "http://127.0.0.1",
    "https://sandbox.tekarab.com",
}


RUN_ALLOWED_REPO_TYPES = {"cli_app", "web_app", "ml_experiment"}
RUN_ALLOWED_EXECUTION_READINESS = {"ready", "needs_env"}
RUN_ALLOWED_SUPPORT_TIERS = {"fully_supported", "partially_supported"}

MAX_SETUP_STEPS = 8
MAX_RUN_STEPS = 3
STEP_TIMEOUT_SECONDS = 180
MAX_LOG_CHARS = 12000

DEFAULT_SESSION_COMMAND_TIMEOUT_SECONDS = 120
DEFAULT_SESSION_TTL_SECONDS = 300

INTERACTIVE_PATTERNS = [
    " init",
    " login",
    " auth",
    " configure",
    " wizard",
    " prompt",
    " press enter",
    " choose ",
    " select ",
    " confirm",
    " oauth",
    " device code",
    " streamlit run",
    " flask run",
    " --reload",
]

DANGEROUS_PATTERNS = [
    "sudo ",
    "apt ",
    "apt-get ",
    "yum ",
    "dnf ",
    "systemctl ",
    "service ",
    "docker ",
    "docker-compose ",
    "shutdown ",
    "reboot ",
    "mkfs",
    " mount ",
    " umount ",
    "rm -rf /",
]

EXTERNAL_SERVICE_BLOCKLIST = {
    "aws",
    "azure",
    "gcp",
    "database",
    "stripe",
    "twilio",
    "slack",
}


@app.before_request
def handle_cors_preflight():
    if request.method == "OPTIONS":
        response = make_response("", 204)
        origin = request.headers.get("Origin", "")
        if origin in ALLOWED_CORS_ORIGINS:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        return response
    return None


@app.after_request
def add_cors_headers(response):
    origin = request.headers.get("Origin", "")
    if origin in ALLOWED_CORS_ORIGINS:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


def error_response(message: str, status: int = 400, extra: Dict[str, Any] | None = None):
    payload = {"ok": False, "error": message}
    if extra:
        payload.update(extra)
    return jsonify(payload), status


def truncate_output(text: Any, limit: int = MAX_LOG_CHARS) -> str:
    text = str(text or "")
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _ensure_list_of_str(value: Any) -> List[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _is_placeholder_env_value(value: Any) -> bool:
    if isinstance(value, bool):
        return True

    if value is None:
        return True

    value_str = str(value).strip()
    if not value_str:
        return True

    lowered = value_str.lower()
    return lowered in {"set", "provided", "present", "true", "yes", "1"}


def _has_real_env_value(value: Any) -> bool:
    return not _is_placeholder_env_value(value)


def _is_autoresearchclaw_repo_url(repo_url: str) -> bool:
    repo_url_text = str(repo_url or "").strip().lower()
    return "aiming-lab/autoresearchclaw" in repo_url_text or repo_url_text.endswith("/autoresearchclaw")


def _build_autoresearchclaw_bootstrap_step() -> str:
    return (
        "if [ -f config.arc.yaml ]; then "
        "echo 'config.arc.yaml already exists'; "
        "elif [ -f config.researchclaw.example.yaml ]; then "
        "cp config.researchclaw.example.yaml config.arc.yaml; "
        "else "
        "echo 'Missing config.researchclaw.example.yaml' >&2; "
        "exit 1; "
        "fi"
    )


def _is_understand_anything_repo_url(repo_url: str) -> bool:
    repo_url_text = (repo_url or "").strip().lower()
    return "lum1104/understand-anything" in repo_url_text or repo_url_text.endswith("/understand-anything")


def _is_deepseek_v3_repo_url(repo_url: str) -> bool:
    repo_url_text = (repo_url or "").strip().lower()
    return "deepseek-ai/deepseek-v3" in repo_url_text or repo_url_text.endswith("/deepseek-v3")


def _build_deepseek_v3_requirements_step() -> str:
    return "cd inference && ../.venv/bin/python -m pip install -r requirements.txt"


def inject_repo_specific_plan(
    repo_url: str,
    setup_steps: List[str],
    run_steps: List[str],
) -> Tuple[List[str], List[str], List[str]]:
    final_setup_steps = list(setup_steps)
    final_run_steps = list(run_steps)
    adjustments: List[str] = []

    if _is_autoresearchclaw_repo_url(repo_url):
        bootstrap_step = _build_autoresearchclaw_bootstrap_step()

        if bootstrap_step not in final_setup_steps:
            final_setup_steps.append(bootstrap_step)
            adjustments.append("bootstrap_config_arc_yaml")

        rewritten_run_steps: List[str] = []
        run_injected = False

        for step in final_run_steps:
            step_text = str(step).strip()

            if re.search(r"\bresearchclaw\s+run\b", step_text) and "--config" not in step_text:
                step_text = re.sub(
                    r"\bresearchclaw\s+run\b",
                    "researchclaw run --config config.arc.yaml",
                    step_text,
                    count=1,
                )
                run_injected = True

            rewritten_run_steps.append(step_text)

        final_run_steps = rewritten_run_steps

        if run_injected:
            adjustments.append("inject_researchclaw_config_flag")

    if _is_understand_anything_repo_url(repo_url):
        normalized_existing_setup = [str(step).strip() for step in final_setup_steps]
        if "pnpm install" not in normalized_existing_setup:
            final_setup_steps = ["pnpm install", *final_setup_steps]
            adjustments.append("inject_understand_anything_pnpm_install")

        final_run_steps = [
            "cd understand-anything-plugin/packages/dashboard && npm run build",
            "cd understand-anything-plugin/packages/dashboard && npm run preview -- --host 0.0.0.0 --port 5173",
        ]
        adjustments.append("replace_understand_anything_dev_dashboard_with_build_preview")

    if _is_deepseek_v3_repo_url(repo_url):
        deepseek_requirements_step = _build_deepseek_v3_requirements_step()
        rewritten_setup_steps: List[str] = []
        replaced_editable_install = False

        for step in final_setup_steps:
            step_text = str(step).strip()

            if step_text in {"pip install -e .", "python3 -m pip install -e .", "./.venv/bin/python -m pip install -e ."}:
                if deepseek_requirements_step not in rewritten_setup_steps:
                    rewritten_setup_steps.append(deepseek_requirements_step)
                replaced_editable_install = True
                continue

            rewritten_setup_steps.append(step_text)

        if not replaced_editable_install and deepseek_requirements_step not in rewritten_setup_steps:
            rewritten_setup_steps.append(deepseek_requirements_step)

        final_setup_steps = rewritten_setup_steps

        rewritten_run_steps = []
        for step in final_run_steps:
            step_text = str(step).strip()

            if step_text.startswith("torchrun ") and " generate.py " in f" {step_text} ":
                step_text = f"cd inference && {step_text}"

            rewritten_run_steps.append(step_text)

        final_run_steps = rewritten_run_steps
        adjustments.append("replace_deepseek_v3_editable_install_with_inference_requirements")
        adjustments.append("prefix_deepseek_v3_run_steps_with_inference_dir")

    normalized_run_steps: List[str] = []
    activation_seen = False
    activation_pattern = r"^source\s+(?:\.?venv)/bin/activate$"

    for step in final_run_steps:
        step_text = str(step).strip()

        if re.match(activation_pattern, step_text):
            activation_seen = True
            continue

        if activation_seen:
            if step_text.startswith("python3 "):
                step_text = step_text.replace("python3 ", "./.venv/bin/python ", 1)
            elif step_text.startswith("python "):
                step_text = step_text.replace("python ", "./.venv/bin/python ", 1)
            elif step_text.startswith("pip3 "):
                step_text = step_text.replace("pip3 ", "./.venv/bin/pip ", 1)
            elif step_text.startswith("pip "):
                step_text = step_text.replace("pip ", "./.venv/bin/pip ", 1)

        normalized_run_steps.append(step_text)

    if activation_seen:
        final_run_steps = normalized_run_steps
        adjustments.append("normalize_venv_activation_run_steps")

    return final_setup_steps, final_run_steps, adjustments


def build_rewritten_plan(decision: Dict[str, Any], repo_url: str = "") -> Dict[str, Any]:
    recommended_plan = decision.get("recommended_plan") or {}

    original_setup_steps = _ensure_list_of_str(recommended_plan.get("setup_steps"))
    original_run_steps = _ensure_list_of_str(recommended_plan.get("run_steps"))

    setup_rewrite = apply_rewrite_actions(original_setup_steps)
    run_rewrite = apply_rewrite_actions(original_run_steps)

    final_setup_steps = _ensure_list_of_str(setup_rewrite.get("final_commands"))
    final_run_steps = _ensure_list_of_str(run_rewrite.get("final_commands"))

    skipped_setup_steps = _ensure_list_of_str(setup_rewrite.get("skipped_commands"))
    skipped_run_steps = _ensure_list_of_str(run_rewrite.get("skipped_commands"))

    final_setup_steps, final_run_steps, repo_specific_adjustments = inject_repo_specific_plan(
        repo_url,
        final_setup_steps,
        final_run_steps,
    )

    rewrite_candidates = _ensure_list_of_str(decision.get("rewrite_candidates"))

    return {
        "original_setup_steps": original_setup_steps,
        "original_run_steps": original_run_steps,
        "final_setup_steps": final_setup_steps,
        "final_run_steps": final_run_steps,
        "skipped_setup_steps": skipped_setup_steps,
        "skipped_run_steps": skipped_run_steps,
        "rewrite_candidates": rewrite_candidates,
        "setup_rewrite_report": setup_rewrite.get("rewrite_report") or {},
        "run_rewrite_report": run_rewrite.get("rewrite_report") or {},
        "repo_specific_adjustments": repo_specific_adjustments,
    }


def build_preview_payload(
    repo_url: str,
    raw_analysis: Dict[str, Any],
    decision: Dict[str, Any],
    rewritten_plan: Dict[str, Any],
) -> Dict[str, Any]:
    final_setup_steps = _ensure_list_of_str(rewritten_plan.get("final_setup_steps"))
    final_run_steps = _ensure_list_of_str(rewritten_plan.get("final_run_steps"))

    required_env_vars = decision.get("required_env_vars") or raw_analysis.get("env_vars") or []
    if not isinstance(required_env_vars, list):
        required_env_vars = [str(required_env_vars)]

    warnings: List[str] = []

    execution_readiness = str(decision.get("execution_readiness", "unclear"))
    repo_type_guess = str(decision.get("repo_type_guess", "unknown"))
    risk_level = str(decision.get("risk_level", "unknown"))
    support_tier = str(decision.get("support_tier", "unknown"))
    detected_language = str(decision.get("detected_language") or raw_analysis.get("detected_language") or "unknown")

    if execution_readiness == "needs_env":
        warnings.append("This repository requires environment variables before execution.")
    elif execution_readiness == "needs_command_rewrite":
        warnings.append("This repository requires command rewrite before execution.")
    elif execution_readiness == "unsupported":
        warnings.append("This repository is currently unsupported for execution.")
    elif execution_readiness == "unclear":
        warnings.append("Execution readiness is unclear and should be reviewed before any run attempt.")
    elif execution_readiness == "no_run":
        warnings.append("This repository does not appear to expose a direct runnable entry point.")

    if risk_level in ("medium", "high"):
        warnings.append(f"Risk level is {risk_level}.")

    if support_tier != "fully_supported":
        warnings.append(f"Support tier is {support_tier}.")

    if not final_run_steps:
        warnings.append("No run steps were identified after command rewrite.")

    skipped_setup_steps = _ensure_list_of_str(rewritten_plan.get("skipped_setup_steps"))
    skipped_run_steps = _ensure_list_of_str(rewritten_plan.get("skipped_run_steps"))
    if skipped_setup_steps or skipped_run_steps:
        warnings.append("Some interactive commands were skipped during rewrite preview.")

    repo_specific_adjustments = _ensure_list_of_str(rewritten_plan.get("repo_specific_adjustments"))
    if repo_specific_adjustments:
        warnings.append("Repo-specific non-interactive bootstrap adjustments were applied.")

    pending_requirements = _ensure_list_of_str(decision.get("pending_requirements"))
    safe_to_attempt = execution_readiness in ("ready", "needs_env") and "command_rewrite" not in pending_requirements

    summary_parts = [
        f"{detected_language.capitalize()} repository",
        f"type: {repo_type_guess}",
        f"readiness: {execution_readiness}",
    ]

    if required_env_vars:
        summary_parts.append("requires environment variables")

    if decision.get("needs_command_rewrite") is True:
        summary_parts.append("includes rewriteable interactive commands")

    if repo_specific_adjustments:
        summary_parts.append("includes repo-specific bootstrap adjustments")

    summary = ". ".join(summary_parts) + "."

    return {
        "summary": summary,
        "setup_steps": final_setup_steps,
        "run_steps": final_run_steps,
        "required_env_vars": required_env_vars,
        "warnings": warnings,
        "safe_to_attempt": safe_to_attempt,
    }

def _finalize_auto_start_state(
    session_id: str,
    state: str,
    reason: str = "",
) -> None:
    meta = load_session_meta(session_id) or {}
    meta["auto_start_state"] = state
    meta["auto_start_updated_at"] = utc_now_iso()

    if state != "running":
        meta["auto_start_current_command"] = None

    if reason:
        meta["auto_start_final_reason"] = reason

    save_session_meta(session_id, meta)


def _build_running_progress_message(kind: str, step: str = "") -> str:
    normalized_kind = str(kind or "").strip().lower()
    normalized_step = str(step or "").strip()

    if normalized_kind == "setup" and normalized_step:
        return f"Automatic setup is running: {normalized_step}. Refresh the session status to follow progress."

    if normalized_kind == "launch" and normalized_step:
        return f"Automatic launch is running: {normalized_step}. Refresh the session status to follow progress."

    if normalized_kind == "command":
        return (
            "Command started successfully. Refresh the session status to follow progress. "
            "If it launches a web app, its preview link will appear on the side when it becomes ready."
        )

    return (
        "Auto setup started successfully. Refresh the session status to follow progress. "
        "If this repository launches a web app, its preview link will appear on the side when it becomes ready."
    )


def auto_start_primary_experience(
    session_id: str,
    presentation: Dict[str, Any],
    validation: Dict[str, Any],
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "attempted": False,
        "success": False,
        "reason": "",
        "executed_setup_steps": [],
        "executed_run_steps": [],
        "stopped_after_detected_server": False,
        "primary_experience": None,
    }

    presentation_type = str(presentation.get("presentation_type") or "").strip().lower()
    if presentation_type != "web_app":
        result["reason"] = "Automatic launch is currently supported only for web applications."
        _finalize_auto_start_state(session_id, "completed", "presentation_type_not_web_app")
        return result

    missing_env_vars = _ensure_list_of_str(validation.get("missing_env_vars"))
    if missing_env_vars:
        result["reason"] = "Missing required environment variables for automatic launch."
        _finalize_auto_start_state(session_id, "completed", "missing_env_vars")
        return result

    setup_steps = _ensure_list_of_str(validation.get("setup_steps"))
    run_steps = _ensure_list_of_str(validation.get("run_steps"))

    effective_run_steps: List[str] = list(run_steps)

    fallback_command = str(presentation.get("fallback_command") or "").strip()
    if not effective_run_steps and fallback_command:
        effective_run_steps.append(fallback_command)

    if not effective_run_steps:
        result["reason"] = "No runnable steps available for automatic launch."
        _finalize_auto_start_state(session_id, "completed", "no_runnable_steps")
        return result

    result["attempted"] = True

    meta = load_session_meta(session_id) or {}
    auto_start_started_at = utc_now_iso()
    meta["auto_start_state"] = "running"
    meta["auto_start_started_at"] = auto_start_started_at
    meta["auto_start_updated_at"] = auto_start_started_at
    meta["auto_start_current_command"] = None
    save_session_meta(session_id, meta)

    def _step_succeeded(step_result: Dict[str, Any]) -> bool:
        status = str(step_result.get("status") or "").strip().lower()
        exit_code = step_result.get("exit_code")
        return status == "success" and exit_code in (0, None)

    def _step_is_running(step_result: Dict[str, Any]) -> bool:
        status = str(step_result.get("status") or "").strip().lower()
        result_state = str(step_result.get("result_state") or "").strip().lower()
        return status == "running" or result_state == "running"

    def _summarize_step(step_result: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "command": step_result.get("command"),
            "status": step_result.get("status"),
            "result_state": step_result.get("result_state"),
            "exit_code": step_result.get("exit_code"),
            "detected_server": step_result.get("detected_server"),
        }

    def _refresh_primary_experience() -> Dict[str, Any] | None:
        session_status = get_session_status(session_id)
        primary_experience = session_status.get("primary_experience")
        result["primary_experience"] = primary_experience
        return primary_experience if isinstance(primary_experience, dict) else None

    for step in setup_steps:
        meta = load_session_meta(session_id) or {}
        meta["auto_start_state"] = "running"
        meta["auto_start_current_command"] = step
        meta["auto_start_updated_at"] = utc_now_iso()
        save_session_meta(session_id, meta)

        step_result = run_session_command(
            session_id=session_id,
            command=step,
            dangerous_patterns=DANGEROUS_PATTERNS,
            interactive_patterns=INTERACTIVE_PATTERNS,
            max_output_chars=MAX_LOG_CHARS,
        )
        result["executed_setup_steps"].append(_summarize_step(step_result))

        if _step_is_running(step_result):
            result["reason"] = _build_running_progress_message("setup", step)
            _finalize_auto_start_state(session_id, "running", "setup_step_running")
            _refresh_primary_experience()
            return result

        if not _step_succeeded(step_result):
            result["reason"] = f"Automatic setup failed on: {step}"
            _finalize_auto_start_state(session_id, "failed", "setup_step_failed")
            _refresh_primary_experience()
            return result

    for step in effective_run_steps:
        meta = load_session_meta(session_id) or {}
        meta["auto_start_state"] = "running"
        meta["auto_start_current_command"] = step
        meta["auto_start_updated_at"] = utc_now_iso()
        save_session_meta(session_id, meta)

        step_result = run_session_command(
            session_id=session_id,
            command=step,
            dangerous_patterns=DANGEROUS_PATTERNS,
            interactive_patterns=INTERACTIVE_PATTERNS,
            max_output_chars=MAX_LOG_CHARS,
        )
        result["executed_run_steps"].append(_summarize_step(step_result))

        if _step_is_running(step_result):
            result["reason"] = _build_running_progress_message("launch", step)
            _finalize_auto_start_state(session_id, "running", "run_step_running")
            _refresh_primary_experience()
            return result

        if not _step_succeeded(step_result):
            result["reason"] = f"Automatic launch failed on: {step}"
            _finalize_auto_start_state(session_id, "failed", "run_step_failed")
            _refresh_primary_experience()
            return result

        primary_experience = _refresh_primary_experience()
        if isinstance(step_result.get("detected_server"), dict):
            result["stopped_after_detected_server"] = True

        if isinstance(primary_experience, dict) and primary_experience.get("state") == "ready":
            result["success"] = True
            result["reason"] = "Primary experience became ready."
            _finalize_auto_start_state(session_id, "completed", "primary_experience_ready")
            return result

    primary_experience = _refresh_primary_experience()

    if isinstance(primary_experience, dict) and primary_experience.get("state") == "ready":
        result["success"] = True
        result["reason"] = "Primary experience became ready."
        _finalize_auto_start_state(session_id, "completed", "primary_experience_ready")
        return result

    background_processes = (load_session_meta(session_id) or {}).get("background_processes")
    if isinstance(background_processes, list) and background_processes:
        result["reason"] = "Automatic launch is still in progress. Refresh the session status to follow progress."
        _finalize_auto_start_state(session_id, "running", "background_processes_running")
        return result

    result["reason"] = "Automatic launch did not produce a ready primary experience."
    _finalize_auto_start_state(session_id, "failed", "primary_experience_not_ready")
    return result


def _run_auto_start_primary_experience_background(
    session_id: str,
    presentation: Dict[str, Any],
    validation: Dict[str, Any],
) -> None:
    try:
        print(
            f"[auto_start_primary_experience_background] started session_id={session_id}",
            flush=True,
        )
        result = auto_start_primary_experience(
            session_id=session_id,
            presentation=presentation,
            validation=validation,
        )
        print(
            "[auto_start_primary_experience_background] finished "
            f"session_id={session_id} result={json.dumps(result, ensure_ascii=False)}",
            flush=True,
        )
    except Exception:
        print(
            "[auto_start_primary_experience_background] unexpected error\n"
            + traceback.format_exc(),
            flush=True,
        )


def build_preview_analysis(raw_analysis: Dict[str, Any]) -> Dict[str, Any]:
    allowed_fields = [
        "repo_url",
        "detected_language",
        "entry_candidates",
        "env_vars",
        "key_files",
        "readme_command_samples",
        "run_commands",
        "setup_commands",
        "test_commands",
        "warnings",
        "interactive_risks",
    ]

    slim: Dict[str, Any] = {}

    for field in allowed_fields:
        if field in raw_analysis:
            slim[field] = raw_analysis[field]

    return slim



def extract_provided_env_var_names(provided_env_vars: Any) -> List[str]:
    names: List[str] = []

    if isinstance(provided_env_vars, dict):
        for key, value in provided_env_vars.items():
            key = str(key).strip()
            if not key:
                continue

            if _has_real_env_value(value):
                names.append(key)

    elif isinstance(provided_env_vars, list):
        for item in provided_env_vars:
            item = str(item).strip()
            if item:
                names.append(item)

    return sorted(set(names))


def build_execution_env(provided_env_vars: Any) -> Dict[str, str]:
    env = dict(os.environ)

    if isinstance(provided_env_vars, dict):
        for key, value in provided_env_vars.items():
            key = str(key).strip()
            if not key:
                continue

            if not _has_real_env_value(value):
                continue

            env[key] = str(value).strip()

    return env

def allocate_free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return int(sock.getsockname()[1])


def build_validation_payload(
    decision: Dict[str, Any],
    provided_env_vars: Any,
    rewritten_plan: Dict[str, Any],
) -> Dict[str, Any]:
    recommended_plan = decision.get("recommended_plan") or {}

    required_env_vars = decision.get("required_env_vars") or []
    if not isinstance(required_env_vars, list):
        required_env_vars = [str(required_env_vars)]

    if isinstance(provided_env_vars, dict):
        provided_env_var_names = list(provided_env_vars.keys())
    else:
        provided_env_var_names = extract_provided_env_var_names(provided_env_vars)

    # normalize both sides for safe comparison
    normalized_provided = {str(x).strip().lower() for x in provided_env_var_names}
    normalized_required = {str(x).strip().lower(): str(x).strip() for x in required_env_vars}

    missing_env_vars = [
        original_name
        for key, original_name in normalized_required.items()
        if key not in normalized_provided
    ]

    setup_steps = _ensure_list_of_str(rewritten_plan.get("final_setup_steps"))
    if not setup_steps:
        setup_steps = _ensure_list_of_str(recommended_plan.get("setup_steps"))

    run_steps = _ensure_list_of_str(rewritten_plan.get("final_run_steps"))
    if not run_steps:
        run_steps = _ensure_list_of_str(recommended_plan.get("run_steps"))
    skipped_setup_steps = _ensure_list_of_str(rewritten_plan.get("skipped_setup_steps"))
    skipped_run_steps = _ensure_list_of_str(rewritten_plan.get("skipped_run_steps"))
    repo_specific_adjustments = _ensure_list_of_str(rewritten_plan.get("repo_specific_adjustments"))

    placeholder_env_vars: List[str] = []
    if isinstance(provided_env_vars, dict):
        for key, value in provided_env_vars.items():
            key = str(key).strip()
            if key and _is_placeholder_env_value(value):
                placeholder_env_vars.append(key)

    raw_blockers = list(recommended_plan.get("blockers") or [])
    blockers: List[str] = []

    for blocker in raw_blockers:
        blocker_text = str(blocker)

        # skip env-related blockers if env is now provided
        if "Missing required environment variables:" in blocker_text and not missing_env_vars:
            continue

        # skip rewrite-related blockers if rewrite already handled
        if "requires command rewrite" in blocker_text:
            continue

        blockers.append(blocker_text)

    if missing_env_vars:
        blockers.insert(0, f"Missing required environment variables: {', '.join(missing_env_vars)}")

    notes = list(recommended_plan.get("notes") or [])

    if skipped_setup_steps or skipped_run_steps:
        notes.append("Validation is based on rewritten commands after skipping known interactive init steps.")

    if repo_specific_adjustments:
        notes.append("Repo-specific bootstrap logic was injected into the final execution plan.")

    if placeholder_env_vars:
        notes.append(
            "Placeholder environment variable markers were ignored until real secret values are provided: "
            + ", ".join(sorted(set(placeholder_env_vars)))
        )

    execution_readiness = str(decision.get("execution_readiness", "unclear"))
    needs_command_rewrite = bool(decision.get("needs_command_rewrite"))

    if execution_readiness == "ready":
        is_valid = len(missing_env_vars) == 0
        can_proceed_to_execution = is_valid
        if is_valid:
            notes.append("Validation passed. Repository can proceed to execution stage.")
        else:
            notes.append("Validation failed because required environment variables are still missing.")
    elif execution_readiness == "needs_env":
        is_valid = len(missing_env_vars) == 0
        can_proceed_to_execution = is_valid
        if is_valid:
            notes.append("All required environment variables appear to be provided.")
            if needs_command_rewrite:
                notes.append("Known rewriteable commands were already normalized into the final execution plan.")
            notes.append("Repository can proceed to execution stage.")
        else:
            notes.append("Validation failed because required environment variables are still missing.")
    elif execution_readiness == "needs_command_rewrite":
        is_valid = len(missing_env_vars) == 0
        can_proceed_to_execution = is_valid
        if is_valid:
            notes.append("Validation passed using rewritten command plan.")
        else:
            notes.append("Validation failed because required environment variables are still missing.")
    elif execution_readiness in {"no_run", "unsupported"}:
        is_valid = False
        can_proceed_to_execution = False
        if execution_readiness == "no_run":
            notes.append("Repository is classified as not directly runnable.")
        else:
            notes.append("Repository is currently unsupported for execution.")

    elif execution_readiness == "unclear":
        is_valid = False
        can_proceed_to_execution = False
        notes.append("Repository execution readiness is unclear. Manual review is required before execution.")
    else:
        is_valid = False
        can_proceed_to_execution = False
        notes.append("Unknown execution readiness state.")

    return {
        "is_valid": is_valid,
        "can_proceed_to_execution": can_proceed_to_execution,
        "missing_env_vars": missing_env_vars,
        "provided_env_vars": provided_env_var_names,
        "required_env_vars": required_env_vars,
        "setup_steps": setup_steps,
        "run_steps": run_steps,
        "skipped_setup_steps": skipped_setup_steps,
        "skipped_run_steps": skipped_run_steps,
        "blockers": blockers,
        "notes": notes,
        "rewritten_plan": rewritten_plan,
    }


def _contains_any_pattern(command: str, patterns: List[str]) -> bool:
    text = f" {str(command).strip().lower()} "
    for pattern in patterns:
        if pattern.lower() in text:
            return True
    return False


def _version_tuple(version_text: str) -> Tuple[int, ...]:
    parts = re.findall(r"\d+", str(version_text))
    return tuple(int(x) for x in parts[:3])


def _extract_min_python_from_spec(spec: str) -> Tuple[int, ...] | None:
    if not spec:
        return None

    match = re.search(r">=\s*([0-9]+(?:\.[0-9]+){0,2})", spec)
    if not match:
        return None

    return _version_tuple(match.group(1))


def _read_repo_python_requirement(repo_dir: str) -> str | None:
    pyproject_path = os.path.join(repo_dir, "pyproject.toml")
    if not os.path.isfile(pyproject_path):
        return None

    try:
        with open(pyproject_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return None

    match = re.search(r'requires-python\s*=\s*["\']([^"\']+)["\']', content, re.IGNORECASE)
    if match:
        return match.group(1).strip()

    return None


def _probe_python_command(command: str, repo_dir: str, env: Dict[str, str]) -> Dict[str, Any]:
    try:
        result = subprocess.run(
            ["bash", "-lc", f"{command} --version"],
            cwd=repo_dir,
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )

        combined = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
        version_match = re.search(r"Python\s+([0-9]+(?:\.[0-9]+){1,2})", combined)

        return {
            "command": command,
            "available": result.returncode == 0 and version_match is not None,
            "version": version_match.group(1) if version_match else None,
            "raw_output": truncate_output(combined, 500),
        }
    except Exception as e:
        return {
            "command": command,
            "available": False,
            "version": None,
            "raw_output": truncate_output(str(e), 500),
        }


def select_python_interpreter_for_repo(repo_dir: str, env: Dict[str, str]) -> Dict[str, Any]:
    required_spec = _read_repo_python_requirement(repo_dir)

    candidates = ["python3", "python3.12", "python3.11", "python3.10"]
    probes = [_probe_python_command(cmd, repo_dir, env) for cmd in candidates]

    available = [item for item in probes if item.get("available") is True]
    default_probe = next((item for item in probes if item.get("command") == "python3"), None)

    selection = {
        "required_python_spec": required_spec,
        "selected_python": "python3",
        "selected_python_version": default_probe.get("version") if default_probe else None,
        "selection_reason": "default_python3",
        "available_candidates": [
            {
                "command": item.get("command"),
                "version": item.get("version"),
            }
            for item in available
        ],
    }

    min_required = _extract_min_python_from_spec(required_spec or "")
    if min_required is None:
        return selection

    if default_probe and default_probe.get("version"):
        default_version = _version_tuple(str(default_probe["version"]))
        if default_version >= min_required:
            selection["selection_reason"] = "default_python3_satisfies_requirement"
            return selection

    eligible: List[Dict[str, Any]] = []
    for item in available:
        version_text = item.get("version")
        if not version_text:
            continue
        if _version_tuple(str(version_text)) >= min_required:
            eligible.append(item)

    if eligible:
        eligible.sort(key=lambda item: _version_tuple(str(item["version"])), reverse=True)
        best = eligible[0]
        selection["selected_python"] = str(best["command"])
        selection["selected_python_version"] = str(best["version"])
        selection["selection_reason"] = "matched_repo_python_requirement"
        return selection

    selection["selection_reason"] = "no_available_interpreter_satisfies_requirement"
    return selection


def rewrite_setup_steps_for_python(setup_steps: List[str], python_command: str) -> List[str]:
    rewritten: List[str] = []

    for step in setup_steps:
        step_text = str(step).strip()

        if step_text.lower().startswith("python3 -m venv "):
            suffix = step_text[len("python3 -m venv "):].strip()
            rewritten.append(f"{python_command} -m venv {suffix}")
            continue

        if step_text.lower().startswith("python -m venv "):
            suffix = step_text[len("python -m venv "):].strip()
            rewritten.append(f"{python_command} -m venv {suffix}")
            continue

        rewritten.append(step_text)

    return rewritten


def build_run_policy_payload(decision: Dict[str, Any], validation: Dict[str, Any]) -> Dict[str, Any]:
    policy_blockers: List[str] = []
    blocked_commands: List[str] = []

    repo_type_guess = str(decision.get("repo_type_guess", "unknown"))
    execution_readiness = str(decision.get("execution_readiness", "unknown"))
    support_tier = str(decision.get("support_tier", "unknown"))
    external_services = list(decision.get("external_services_detected") or [])

    setup_steps = list(validation.get("setup_steps") or [])
    run_steps = list(validation.get("run_steps") or [])

    if validation.get("is_valid") is not True or validation.get("can_proceed_to_execution") is not True:
        policy_blockers.append("Validation failed. Repository cannot proceed to execution.")

    if repo_type_guess not in RUN_ALLOWED_REPO_TYPES:
        policy_blockers.append(f"Repo type '{repo_type_guess}' is not allowed for /run-repo v1.")

    if execution_readiness not in RUN_ALLOWED_EXECUTION_READINESS:
        policy_blockers.append(f"Execution readiness '{execution_readiness}' is not allowed for /run-repo v1.")

    if support_tier not in RUN_ALLOWED_SUPPORT_TIERS:
        policy_blockers.append(f"Support tier '{support_tier}' is not allowed for /run-repo v1.")

    if len(setup_steps) > MAX_SETUP_STEPS:
        policy_blockers.append(f"Too many setup steps: {len(setup_steps)} > {MAX_SETUP_STEPS}.")

    if len(run_steps) > MAX_RUN_STEPS:
        policy_blockers.append(f"Too many run steps: {len(run_steps)} > {MAX_RUN_STEPS}.")

    if not run_steps:
        policy_blockers.append("No runnable steps available after validation.")

    blocked_services = [svc for svc in external_services if svc in EXTERNAL_SERVICE_BLOCKLIST]

    if blocked_services:
        policy_blockers.append(
            f"Unsupported external services detected for /run-repo v1: {', '.join(blocked_services)}."
        )
    else:
        # treat external services (like openai) as requiring user action (not runnable in v1)
        if external_services:
            policy_blockers.append(
                f"External services require user configuration and cannot be auto-executed: {', '.join(external_services)}."
            )

    for command in setup_steps + run_steps:
        cmd = str(command).strip()

        if _contains_any_pattern(cmd, DANGEROUS_PATTERNS):
            blocked_commands.append(cmd)
            policy_blockers.append(f"Dangerous command detected: {cmd}")
        elif _contains_any_pattern(cmd, INTERACTIVE_PATTERNS):
            blocked_commands.append(cmd)
            policy_blockers.append(f"Interactive or long-running command detected: {cmd}")

    deduped_blockers: List[str] = []
    for item in policy_blockers:
        if item not in deduped_blockers:
            deduped_blockers.append(item)

    deduped_commands: List[str] = []
    for item in blocked_commands:
        if item not in deduped_commands:
            deduped_commands.append(item)

    hard_block_reasons: List[str] = []
    for blocker in deduped_blockers:
        blocker_lower = blocker.lower()
        if (
            "dangerous command detected:" in blocker_lower
            or "interactive or long-running command detected:" in blocker_lower
            or "unsupported external services detected" in blocker_lower
        ):
            hard_block_reasons.append(blocker)

    # STRICT EXECUTION BLOCK (do not allow execution for non-runnable repos)
    if validation.get("can_proceed_to_execution") is not True:
        is_allowed = False
    else:
        is_allowed = len(deduped_blockers) == 0

    admission_mode = "full" if is_allowed else "fallback"
    if hard_block_reasons:
        admission_mode = "blocked"

    return {
        "is_allowed": is_allowed,
        "admission_mode": admission_mode,
        "policy_blockers": deduped_blockers,
        "blocked_commands": deduped_commands,
        "max_setup_steps": MAX_SETUP_STEPS,
        "max_run_steps": MAX_RUN_STEPS,
        "allowed_repo_types": sorted(RUN_ALLOWED_REPO_TYPES),
        "allowed_execution_readiness": sorted(RUN_ALLOWED_EXECUTION_READINESS),
        "allowed_support_tiers": sorted(RUN_ALLOWED_SUPPORT_TIERS),
    }

def smart_plan_setup_steps(
    repo_dir: str,
    original_steps: List[str],
    python_command: str
) -> List[str]:
    """
    Smart planner for setup steps.
    Keeps validated setup steps intact as much as possible, while only applying
    safe Python interpreter normalization and light deduplication.
    Removes shell activation steps because execution uses direct interpreter paths.
    """

    planned: List[str] = []

    venv_path = os.path.join(repo_dir, ".venv", "bin", "python")
    has_venv = os.path.exists(venv_path)

    for step in original_steps:
        step_text = str(step).strip()
        step_lower = step_text.lower()

        if not step_text:
            continue

        # Skip shell activation steps; they are unnecessary when using direct paths
        if step_lower in {
            "source .venv/bin/activate",
            ". .venv/bin/activate",
            "source venv/bin/activate",
            ". venv/bin/activate",
        }:
            continue

        # Skip duplicate venv creation if it already exists
        if " -m venv " in step_lower and has_venv:
            continue

        # Normalize plain pip commands while preserving install target
        if step_lower.startswith("pip "):
            pip_suffix = step_text[4:].strip()

            if has_venv:
                planned.append(f"./.venv/bin/pip {pip_suffix}")
            else:
                planned.append(f"{python_command} -m pip {pip_suffix}")
            continue

        # Normalize python -m pip
        if step_lower.startswith("python -m pip ") or step_lower.startswith("python3 -m pip "):
            pip_parts = step_text.split(None, 3)
            if len(pip_parts) >= 4:
                pip_suffix = pip_parts[3].strip()
                planned.append(f"{python_command} -m pip {pip_suffix}")
                continue

        planned.append(step_text)

    # Deduplicate
    seen = set()
    deduped: List[str] = []

    for cmd in planned:
        key = cmd.strip()
        if key and key not in seen:
            deduped.append(cmd)
            seen.add(key)

    return deduped


def run_shell_step(stage: str, step: str, repo_dir: str, env: Dict[str, str]) -> Dict[str, Any]:
    start = time.time()

    shell_script = (
        "set -e\n"
        "if [ -f .venv/bin/activate ]; then source .venv/bin/activate; fi\n"
        f"{step}"
    )

    lowered_step = str(step or "").strip().lower()
    server_markers = [
        "npm run dev",
        "npm run preview",
        "npm start",
        "pnpm dev",
        "pnpm preview",
        "pnpm start",
        "yarn dev",
        "yarn preview",
        "yarn start",
        "vite dev",
        "vite preview",
        "next dev",
        "astro dev",
        "uvicorn ",
        "gunicorn ",
        "flask run",
        "streamlit run",
        "python -m http.server",
        "python3 -m http.server",
    ]
    is_server_command = any(marker in lowered_step for marker in server_markers)

    if is_server_command:
        launch_token = f"{int(time.time() * 1000)}-{os.getpid()}"
        background_log_path = f"/tmp/tekarab-run-{launch_token}.log"

        try:
            with open(background_log_path, "w", encoding="utf-8"):
                pass

            process = subprocess.Popen(
                ["bash", "-lc", f"{shell_script} > {background_log_path} 2>&1"],
                cwd=repo_dir,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
                start_new_session=True,
            )

            startup_log = ""

            for _ in range(6):
                time.sleep(1)

                try:
                    with open(background_log_path, "r", encoding="utf-8", errors="ignore") as fh:
                        startup_log = fh.read()
                except OSError:
                    startup_log = ""

                poll_code = process.poll()
                if poll_code is not None:
                    duration = round(time.time() - start, 3)
                    return {
                        "stage": stage,
                        "step": step,
                        "status": "failed" if poll_code != 0 else "success",
                        "exit_code": poll_code,
                        "duration_seconds": duration,
                        "stdout": truncate_output(startup_log),
                        "stderr": "",
                    }

            duration = round(time.time() - start, 3)
            summary_lines = [
                f"Background process started with PID {process.pid}.",
                f"Log file: {background_log_path}",
            ]
            if startup_log.strip():
                summary_lines.append("----- STARTUP LOG -----")
                summary_lines.append(startup_log.strip())

            return {
                "stage": stage,
                "step": step,
                "status": "success",
                "exit_code": 0,
                "duration_seconds": duration,
                "stdout": truncate_output("\n".join(summary_lines).strip() + "\n"),
                "stderr": "",
            }

        except Exception as e:
            duration = round(time.time() - start, 3)
            return {
                "stage": stage,
                "step": step,
                "status": "failed",
                "exit_code": 1,
                "duration_seconds": duration,
                "stdout": "",
                "stderr": truncate_output(str(e)),
            }

    try:
        result = subprocess.run(
            ["bash", "-lc", shell_script],
            cwd=repo_dir,
            env=env,
            capture_output=True,
            text=True,
            timeout=STEP_TIMEOUT_SECONDS,
        )
        duration = round(time.time() - start, 3)

        status = "success" if result.returncode == 0 else "failed"

        return {
            "stage": stage,
            "step": step,
            "status": status,
            "exit_code": result.returncode,
            "duration_seconds": duration,
            "stdout": truncate_output(result.stdout),
            "stderr": truncate_output(result.stderr),
        }
    except subprocess.TimeoutExpired as e:
        duration = round(time.time() - start, 3)
        stdout = ""
        stderr = "Command timed out."

        if getattr(e, "stdout", None):
            stdout = truncate_output(e.stdout)
        if getattr(e, "stderr", None):
            stderr = truncate_output(e.stderr) or stderr

        return {
            "stage": stage,
            "step": step,
            "status": "timeout",
            "exit_code": None,
            "duration_seconds": duration,
            "stdout": stdout,
            "stderr": stderr,
        }


def detect_system_dependency_issue(step: str, log_item: Dict[str, Any]) -> Dict[str, Any] | None:
    step_text = str(step or "").strip().lower()
    stdout = str(log_item.get("stdout") or "")
    stderr = str(log_item.get("stderr") or "")
    combined = f"{stdout}\n{stderr}".lower()

    if step_text.startswith("python3 -m venv") or step_text.startswith("python -m venv") or " -m venv " in step_text:
        if "ensurepip is not available" in combined or "python3-venv" in combined or "failing command:" in combined:
            return {
                "type": "missing_system_dependency",
                "dependency": "python3-venv",
                "affected_step": step,
                "reason": "The system Python environment does not support venv creation because ensurepip/python3-venv is unavailable.",
                "hint": "Install the matching python-venv package on the host, or make virtualenv available for fallback.",
            }

    return None


def detect_python_version_issue(step: str, log_item: Dict[str, Any]) -> Dict[str, Any] | None:
    stdout = str(log_item.get("stdout") or "")
    stderr = str(log_item.get("stderr") or "")
    combined = f"{stdout}\n{stderr}"

    pattern = r"requires a different Python:\s*([0-9]+(?:\.[0-9]+){1,2})\s+not in\s+'([^']+)'"
    match = re.search(pattern, combined, re.IGNORECASE)

    if not match:
        return None

    current_python = match.group(1).strip()
    required_python = match.group(2).strip()

    return {
        "type": "python_version_mismatch",
        "affected_step": step,
        "current_python": current_python,
        "required_python": required_python,
        "reason": f"Repository dependency installation requires Python {required_python}, but the current runtime is {current_python}.",
        "hint": "Use a host interpreter that matches the repository requirement, such as python3.11 or newer, and create the virtual environment with that interpreter.",
    }


def can_use_virtualenv_fallback(repo_dir: str, env: Dict[str, str], python_command: str) -> Tuple[bool, str]:
    try:
        result = subprocess.run(
            ["bash", "-lc", f"{python_command} -m virtualenv --version"],
            cwd=repo_dir,
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        if result.returncode == 0:
            return True, truncate_output(result.stdout or result.stderr or "virtualenv available", 500)
        return False, truncate_output(result.stderr or result.stdout or "virtualenv not available", 500)
    except Exception as e:
        return False, truncate_output(str(e), 500)


def build_virtualenv_fallback_step(step: str, python_command: str) -> str | None:
    step_text = str(step or "").strip()
    lower = step_text.lower()

    if lower.startswith("python3 -m venv "):
        suffix = step_text[len("python3 -m venv "):].strip()
        if suffix:
            return f"{python_command} -m virtualenv {suffix}"

    if lower.startswith("python -m venv "):
        suffix = step_text[len("python -m venv "):].strip()
        if suffix:
            return f"{python_command} -m virtualenv {suffix}"

    if " -m venv " in lower:
        parts = step_text.split("-m venv", 1)
        if len(parts) == 2:
            suffix = parts[1].strip()
            if suffix:
                return f"{python_command} -m virtualenv {suffix}"

    return None


def execute_repo_plan(validation: Dict[str, Any], provided_env_vars: Any) -> Dict[str, Any]:
    started_at = utc_now_iso()
    execution_env = build_execution_env(provided_env_vars)

    original_setup_steps = list(validation.get("setup_steps") or [])
    run_steps = list(validation.get("run_steps") or [])
    effective_run_steps: List[str] = []

    for raw_step in run_steps:
        step_text = str(raw_step or "").strip()
        if not step_text:
            continue

        rewritten_step = step_text
        lowered_step = step_text.lower()

        if (
            "python -m http.server" in lowered_step
            or "python3 -m http.server" in lowered_step
        ):
            port_value = str(execution_env.get("PORT") or "").strip()
            if not port_value:
                port_value = str(allocate_free_local_port())
                execution_env["PORT"] = port_value

            rewritten_step = re.sub(
                r"((?:python|python3)\s+-m\s+http\.server)(?:\s+\d+)?",
                rf"\1 {port_value}",
                step_text,
                count=1,
                flags=re.IGNORECASE,
            )

        effective_run_steps.append(rewritten_step)

    temp_repo_dir: str | None = None
    temp_root_dir: str | None = None
    cleanup_completed = False

    setup_steps_executed: List[str] = []
    run_steps_executed: List[str] = []
    logs: List[Dict[str, Any]] = []

    failure_stage = None
    failure_step = None
    success = False
    reason = "Execution completed successfully."

    repo_url = str(validation.get("repo_url") or "")

    system_dependency_issue: Dict[str, Any] | None = None
    python_version_issue: Dict[str, Any] | None = None
    fallback_attempted = False
    fallback_succeeded = False
    fallback_details: List[Dict[str, Any]] = []
    interpreter_selection: Dict[str, Any] | None = None
    effective_setup_steps: List[str] = list(original_setup_steps)

    try:
        temp_repo_dir, clone_error = clone_repo_shallow(repo_url)
        if clone_error or not temp_repo_dir:
            finished_at = utc_now_iso()
            return {
                "attempted": True,
                "success": False,
                "reason": f"Repository clone failed: {clone_error or 'unknown error'}",
                "failure_stage": "clone",
                "failure_step": "git clone",
                "working_directory": None,
                "cleanup_completed": False,
                "started_at": started_at,
                "finished_at": finished_at,
                "setup_steps_executed": [],
                "run_steps_executed": [],
                "logs": [],
                "system_dependency_issue": None,
                "python_version_issue": None,
                "interpreter_selection": None,
                "effective_setup_steps": list(original_setup_steps),
                "fallback_attempted": False,
                "fallback_succeeded": False,
                "fallback_details": [],
            }

        temp_root_dir = os.path.dirname(temp_repo_dir)

        interpreter_selection = select_python_interpreter_for_repo(temp_repo_dir, execution_env)
        selected_python = str(interpreter_selection.get("selected_python") or "python3")
        effective_setup_steps = rewrite_setup_steps_for_python(original_setup_steps, selected_python)

        for step in effective_setup_steps:
            step = str(step).strip()
            if not step:
                continue

            setup_steps_executed.append(step)
            log_item = run_shell_step("setup", step, temp_repo_dir, execution_env)
            logs.append(log_item)

            if log_item["status"] != "success":
                detected_issue = detect_system_dependency_issue(step, log_item)

                if detected_issue:
                    system_dependency_issue = detected_issue

                    fallback_step = build_virtualenv_fallback_step(step, selected_python)
                    if fallback_step:
                        fallback_attempted = True
                        fallback_available, fallback_probe_output = can_use_virtualenv_fallback(
                            temp_repo_dir,
                            execution_env,
                            selected_python,
                        )
                        fallback_details.append({
                            "type": "virtualenv_probe",
                            "available": fallback_available,
                            "details": fallback_probe_output,
                            "python_command": selected_python,
                        })

                        if fallback_available:
                            fallback_log = run_shell_step("setup_fallback", fallback_step, temp_repo_dir, execution_env)
                            logs.append(fallback_log)
                            fallback_details.append({
                                "type": "virtualenv_fallback_step",
                                "original_step": step,
                                "fallback_step": fallback_step,
                                "status": fallback_log.get("status"),
                                "exit_code": fallback_log.get("exit_code"),
                                "python_command": selected_python,
                            })

                            if fallback_log.get("status") == "success":
                                fallback_succeeded = True
                                continue

                    failure_stage = "setup"
                    failure_step = step
                    success = False
                    reason = "Execution stopped بسبب missing host dependency required for virtual environment setup."
                    break

                detected_python_issue = detect_python_version_issue(step, log_item)
                if detected_python_issue:
                    python_version_issue = detected_python_issue
                    failure_stage = "setup"
                    failure_step = step
                    success = False
                    reason = "Execution stopped بسبب Python version mismatch between the host runtime and the repository requirement."
                    break

                failure_stage = "setup"
                failure_step = step
                success = False
                if log_item["status"] == "timeout":
                    reason = "Execution stopped بسبب timeout."
                else:
                    reason = "Execution stopped after a failed step."
                break

        if failure_stage is None:
            for step in effective_run_steps:
                step = str(step).strip()
                if not step:
                    continue

                run_steps_executed.append(step)
                log_item = run_shell_step("run", step, temp_repo_dir, execution_env)
                logs.append(log_item)

                if log_item["status"] != "success":
                    detected_python_issue = detect_python_version_issue(step, log_item)
                    if detected_python_issue:
                        python_version_issue = detected_python_issue
                        failure_stage = "run"
                        failure_step = step
                        success = False
                        reason = "Execution stopped بسبب Python version mismatch between the host runtime and the repository requirement."
                        break

                    failure_stage = "run"
                    failure_step = step
                    success = False
                    if log_item["status"] == "timeout":
                        reason = "Execution stopped بسبب timeout."
                    else:
                        reason = "Execution stopped after a failed step."
                    break

        if failure_stage is None:
            success = True
            reason = "Execution completed successfully."

        finished_at = utc_now_iso()

        return {
            "attempted": True,
            "success": success,
            "reason": reason,
            "failure_stage": failure_stage,
            "failure_step": failure_step,
            "working_directory": temp_repo_dir,
            "cleanup_completed": False,
            "started_at": started_at,
            "finished_at": finished_at,
            "setup_steps_executed": setup_steps_executed,
            "run_steps_executed": run_steps_executed,
            "logs": logs,
            "system_dependency_issue": system_dependency_issue,
            "python_version_issue": python_version_issue,
            "interpreter_selection": interpreter_selection,
            "effective_setup_steps": effective_setup_steps,
            "fallback_attempted": fallback_attempted,
            "fallback_succeeded": fallback_succeeded,
            "fallback_details": fallback_details,
        }

    finally:
        if temp_root_dir:
            try:
                shutil.rmtree(temp_root_dir, ignore_errors=False)
                cleanup_completed = True
            except Exception:
                cleanup_completed = False

        execute_repo_plan._last_cleanup_completed = cleanup_completed  # type: ignore[attr-defined]


def _build_session_response_payload(
    repo_url: str,
    decision: Dict[str, Any],
    rewritten_plan: Dict[str, Any],
    validation: Dict[str, Any],
    run_policy: Dict[str, Any],
    session_meta: Dict[str, Any] | None,
    execution_env_names: List[str],
) -> Dict[str, Any]:
    payload = {
        "ok": True,
        "repo_url": repo_url,
        "decision": decision,
        "rewritten_plan": rewritten_plan,
        "validation": validation,
        "run_policy": run_policy,
        "execution_env_var_names": execution_env_names,
    }

    if session_meta is not None:
        payload["session"] = session_meta

    return payload

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True, "service": "tekarab-sandbox-api"}), 200




@app.route("/", methods=["GET"])
def root():
    return jsonify({
        "ok": True,
        "service": "tekarab-sandbox-api",
        "endpoints": [
            "/health",
            "/run",
            "/analyze-repo",
            "/prepare-repo-run",
            "/preview-repo-run",
            "/validate-repo-run-request",
            "/run-repo",
            "/start-repo-session",
            "/run-session-command",
            "/session-status",
            "/delete-session",
            "/session-command-log",
        ],
    }), 200


@app.route("/run", methods=["POST"])
def run_code():
    data = request.get_json(silent=True) or {}
    code = data.get("code", "")
    user_input = data.get("input", "")

    if not isinstance(code, str) or not code.strip():
        return error_response("Missing or invalid 'code' field.", 400)

    try:
        process = subprocess.run(
            [sys.executable, "-c", code],
            input=user_input,
            capture_output=True,
            text=True,
            timeout=10,
        )

        return jsonify({
            "ok": True,
            "success": process.returncode == 0,
            "exit_code": process.returncode,
            "output": process.stdout,
            "error": process.stderr,
        }), 200

    except subprocess.TimeoutExpired:
        return jsonify({
            "ok": True,
            "success": False,
            "exit_code": -1,
            "output": "",
            "error": "Execution timed out.",
        }), 200
    except Exception as e:
        return error_response(f"Run failed: {e}", 500)


@app.route("/analyze-repo", methods=["POST"])
def analyze_repo_endpoint():
    data = request.get_json(silent=True) or {}
    repo_url = str(data.get("repo_url", "")).strip()

    if not repo_url:
        return error_response("Missing 'repo_url'.", 400)

    try:
        raw_analysis = RAW_ANALYZE_FUNC(repo_url)
        if not isinstance(raw_analysis, dict):
            return error_response("Raw analyzer returned invalid response shape.", 500)

        raw_analysis["repo_url"] = repo_url

        return jsonify({
            "ok": True,
            "repo_url": repo_url,
            "analysis": raw_analysis,
        }), 200

    except Exception as e:
        return error_response(
            "Repository analysis failed.",
            500,
            extra={
                "details": str(e),
                "traceback": traceback.format_exc(),
            },
        )


@app.route("/prepare-repo-run", methods=["POST"])
def prepare_repo_run():
    data = request.get_json(silent=True) or {}
    repo_url = str(data.get("repo_url", "")).strip()

    if not repo_url:
        return error_response("Missing 'repo_url'.", 400)

    try:
        raw_analysis = RAW_ANALYZE_FUNC(repo_url)
        if not isinstance(raw_analysis, dict):
            return error_response("Raw analyzer returned invalid response shape.", 500)

        raw_analysis["repo_url"] = repo_url
        decision = build_repo_decision(raw_analysis)
        rewritten_plan = build_rewritten_plan(decision, repo_url)

        return jsonify({
            "ok": True,
            "repo_url": repo_url,
            "analysis": decision,
            "rewritten_plan": rewritten_plan,
        }), 200

    except Exception as e:
        return error_response(
            "Prepare repo run failed.",
            500,
            extra={
                "details": str(e),
                "traceback": traceback.format_exc(),
            },
        )


@app.route("/preview-repo-run", methods=["POST"])
def preview_repo_run():
    data = request.get_json(silent=True) or {}
    repo_url = str(data.get("repo_url", "")).strip()

    if not repo_url:
        return error_response("Missing 'repo_url'.", 400)

    try:
        raw_analysis = RAW_ANALYZE_FUNC(repo_url)
        if not isinstance(raw_analysis, dict):
            return error_response("Raw analyzer returned invalid response shape.", 500)

        raw_analysis["repo_url"] = repo_url
        decision = build_repo_decision(raw_analysis)
        rewritten_plan = build_rewritten_plan(decision, repo_url)
        preview = build_preview_payload(repo_url, raw_analysis, decision, rewritten_plan)
        preview_analysis = build_preview_analysis(raw_analysis)

        return jsonify({
            "ok": True,
            "repo_url": repo_url,
            "analysis": preview_analysis,
            "decision": decision,
            "rewritten_plan": rewritten_plan,
            "preview": preview,
        }), 200

    except Exception as e:
        return error_response(
            "Preview repo run failed.",
            500,
            extra={
                "details": str(e),
                "traceback": traceback.format_exc(),
            },
        )


@app.route("/validate-repo-run-request", methods=["POST"])
def validate_repo_run_request():
    data = request.get_json(silent=True) or {}
    repo_url = str(data.get("repo_url", "")).strip()
    provided_env_vars = data.get("provided_env_vars", {})

    if not repo_url:
        return error_response("Missing 'repo_url'.", 400)

    try:
        raw_analysis = RAW_ANALYZE_FUNC(repo_url)
        if not isinstance(raw_analysis, dict):
            return error_response("Raw analyzer returned invalid response shape.", 500)

        raw_analysis["repo_url"] = repo_url
        decision = build_repo_decision(raw_analysis)
        rewritten_plan = build_rewritten_plan(decision, repo_url)
        validation = build_validation_payload(decision, provided_env_vars, rewritten_plan)

        return jsonify({
            "ok": True,
            "repo_url": repo_url,
            "decision": decision,
            "rewritten_plan": rewritten_plan,
            "validation": validation,
        }), 200

    except Exception as e:
        return error_response(
            "Validate repo run request failed.",
            500,
            extra={
                "details": str(e),
                "traceback": traceback.format_exc(),
            },
        )


@app.route("/run-repo", methods=["POST"])
def run_repo():
    data = request.get_json(silent=True) or {}
    repo_url = str(data.get("repo_url", "")).strip()
    provided_env_vars = data.get("provided_env_vars", {})

    if not repo_url:
        return error_response("Missing 'repo_url'.", 400)

    try:
        raw_analysis = RAW_ANALYZE_FUNC(repo_url)
        if not isinstance(raw_analysis, dict):
            return error_response("Raw analyzer returned invalid response shape.", 500)

        raw_analysis["repo_url"] = repo_url
        decision = build_repo_decision(raw_analysis)
        rewritten_plan = build_rewritten_plan(decision, repo_url)
        validation = build_validation_payload(decision, provided_env_vars, rewritten_plan)
        validation["repo_url"] = repo_url
        run_policy = build_run_policy_payload(decision, validation)

        if not run_policy["is_allowed"]:
            execution = {
                "attempted": False,
                "success": False,
                "reason": "Execution blocked by run policy.",
                "failure_stage": None,
                "failure_step": None,
                "working_directory": None,
                "cleanup_completed": True,
                "started_at": None,
                "finished_at": None,
                "policy_blockers": run_policy["policy_blockers"],
                "blocked_commands": run_policy["blocked_commands"],
                "setup_steps_executed": [],
                "run_steps_executed": [],
                "logs": [],
                "system_dependency_issue": None,
                "python_version_issue": None,
                "interpreter_selection": None,
                "effective_setup_steps": list(validation.get("setup_steps") or []),
                "fallback_attempted": False,
                "fallback_succeeded": False,
                "fallback_details": [],
            }

            # enrich execution (policy blocked case)
            execution["result_state"] = "needs_user_action"
            execution["user_guidance"] = {
                "what_worked": "",
                "what_failed": "Execution was blocked before any repo steps could run.",
                "required_user_actions": execution.get("policy_blockers") or [],
                "next_commands": [],
                "summary": "Execution is blocked by the current run policy.",
            }
            missing_env_vars = [str(x).strip() for x in (validation.get("missing_env_vars") or []) if str(x).strip()]
            structured_actions = [
                {
                    "type": "provide_env_var",
                    "key": env_key,
                    "label": env_key,
                    "required": True,
                    "source": "env_detection",
                }
                for env_key in missing_env_vars
            ]
            for blocker in (execution.get("policy_blockers") or []):
                blocker_text = str(blocker).strip()
                if blocker_text:
                    structured_actions.append({
                        "type": "resolve_policy_blocker",
                        "message": blocker_text,
                        "source": "run_policy",
                    })
            execution["structured_user_actions"] = structured_actions

            return jsonify({
                "ok": True,
                "repo_url": repo_url,
                "decision": decision,
                "rewritten_plan": rewritten_plan,
                "validation": validation,
                "run_policy": run_policy,
                "execution": execution,
            }), 200


        execution = execute_repo_plan(validation, provided_env_vars)
        execution["cleanup_completed"] = bool(getattr(execute_repo_plan, "_last_cleanup_completed", False))
        execution["policy_blockers"] = []
        execution["blocked_commands"] = []

        # enrich execution with result_state + user_guidance
        success = bool(execution.get("success"))
        attempted = bool(execution.get("attempted"))
        setup_steps_executed = [str(x) for x in (execution.get("setup_steps_executed") or []) if str(x).strip()]
        run_steps_executed = [str(x) for x in (execution.get("run_steps_executed") or []) if str(x).strip()]
        completed_steps = setup_steps_executed + run_steps_executed
        failure_stage = str(execution.get("failure_stage") or "").strip()
        failure_step = str(execution.get("failure_step") or "").strip()
        reason = str(execution.get("reason") or "").strip()

        # runtime error detection (invalid API key)
        logs = execution.get("logs") or []
        stderr_text = ""
        if logs:
            last_log = logs[-1]
            stderr_text = str(last_log.get("stderr") or "").lower()

        if "invalid api key" in stderr_text or "unauthorized" in stderr_text:
            execution.setdefault("structured_user_actions", [])
            execution["structured_user_actions"].append({
                "type": "fix_invalid_env_var",
                "key": "OPENAI_API_KEY",
                "message": "Invalid or unauthorized API key",
                "source": "runtime_error",
            })

        smart_error_hint = None
        if logs:
            last_log = logs[-1]
            smart_error_hint = build_smart_error_hint(
                command=str(last_log.get("step") or ""),
                exit_code=last_log.get("exit_code"),
                stdout=str(last_log.get("stdout") or ""),
                stderr=str(last_log.get("stderr") or ""),
            )

        if isinstance(smart_error_hint, dict):
            execution["smart_error_hint"] = smart_error_hint
        elif "smart_error_hint" in execution:
            execution.pop("smart_error_hint", None)

        if success:
            execution["result_state"] = "success"
            execution["user_guidance"] = {
                "what_worked": "Repository setup and execution completed successfully.",
                "what_failed": "",
                "required_user_actions": [],
                "next_commands": [],
                "summary": reason or "Repository execution completed successfully.",
            }
            if not execution.get("structured_user_actions"):
                execution["structured_user_actions"] = []
        elif attempted and completed_steps:
            execution["result_state"] = "partial_success"
            execution["user_guidance"] = {
                "what_worked": f"Completed {len(completed_steps)} setup/run step(s) before execution stopped.",
                "what_failed": f'Execution stopped at step: "{failure_step}".' if failure_step else ("Execution stopped during " + failure_stage + "." if failure_stage else "Execution stopped before all steps could finish."),
                "required_user_actions": [],
                "next_commands": [],
                "summary": reason or "Some repo steps completed, but the full execution did not finish.",
            }
            if not execution.get("structured_user_actions"):
                execution["structured_user_actions"] = []
        else:
            execution["result_state"] = "failed"
            execution["user_guidance"] = {
                "what_worked": "",
                "what_failed": "Repository execution failed before completion.",
                "required_user_actions": [],
                "next_commands": [],
                "summary": reason or "Repository execution failed.",
            }
            if not execution.get("structured_user_actions"):
                execution["structured_user_actions"] = []

        return jsonify({
            "ok": True,
            "repo_url": repo_url,
            "decision": decision,
            "rewritten_plan": rewritten_plan,
            "validation": validation,
            "run_policy": run_policy,
            "execution": execution,
        }), 200

    except Exception as e:
        return error_response(
            "Run repo failed.",
            500,
            extra={
                "details": str(e),
                "traceback": traceback.format_exc(),
            },
        )


@app.route("/start-repo-session", methods=["POST"])
def start_repo_session():
    cleanup_expired_sessions()

    data = request.get_json(silent=True) or {}
    repo_url = str(data.get("repo_url", "")).strip()
    provided_env_vars = data.get("provided_env_vars", {})
    ttl_seconds_raw = data.get("ttl_seconds", DEFAULT_SESSION_TTL_SECONDS)

    if not repo_url:
        return error_response("Missing 'repo_url'.", 400)

    try:
        ttl_seconds = int(ttl_seconds_raw)
        if ttl_seconds <= 0:
            ttl_seconds = DEFAULT_SESSION_TTL_SECONDS
    except Exception:
        ttl_seconds = DEFAULT_SESSION_TTL_SECONDS

    try:
        try:
            raw_analysis = RAW_ANALYZE_FUNC(repo_url)
        except Exception as analysis_error:
            analysis_error_message = str(analysis_error)

            return jsonify({
                "ok": True,
                "repo_url": repo_url,
                "decision": None,
                "rewritten_plan": None,
                "validation": None,
                "run_policy": {
                    "is_allowed": False,
                    "admission_mode": "blocked",
                    "admission_summary": "Repository analysis failed before session creation.",
                    "policy_notes": [analysis_error_message],
                    "policy_blockers": ["Repository analysis failed before session creation."],
                    "blocked_commands": [],
                },
                "presentation": None,
                "structured_user_actions": [],
                "session": None,
                "session_start": {
                    "attempted": True,
                    "success": False,
                    "reason": "Repository analysis failed before session creation.",
                    "error": analysis_error_message,
                    "policy_blockers": ["Repository analysis failed before session creation."],
                    "blocked_commands": [],
                },
            }), 200

        if not isinstance(raw_analysis, dict):
            return error_response("Raw analyzer returned invalid response shape.", 500)

        raw_analysis["repo_url"] = repo_url

        decision = build_repo_decision(raw_analysis)
        rewritten_plan = build_rewritten_plan(decision, repo_url)
        validation = build_validation_payload(decision, provided_env_vars, rewritten_plan)
        validation["repo_url"] = repo_url
        run_policy = build_run_policy_payload(decision, validation)
        presentation = resolve_presentation(raw_analysis, decision)
        admission_mode = run_policy.get("admission_mode")

        allow_fallback_session = True

        if run_policy.get("blocked_commands"):
            allow_fallback_session = False

        if admission_mode == "blocked" and not allow_fallback_session:
            structured_user_actions = []

            for env_key in validation.get("missing_env_vars", []) or []:
                structured_user_actions.append({
                    "type": "provide_env_var",
                    "key": env_key,
                    "label": env_key,
                    "required": True,
                    "source": "env_detection",
                })

            return jsonify({
                "ok": True,
                "repo_url": repo_url,
                "decision": decision,
                "rewritten_plan": rewritten_plan,
                "validation": validation,
                "run_policy": run_policy,
                "presentation": presentation,
                "structured_user_actions": structured_user_actions,
                "session": None,
                "session_start": {
                    "attempted": False,
                    "success": False,
                    "reason": "Session start blocked due to dangerous commands.",
                    "policy_blockers": run_policy["policy_blockers"],
                    "blocked_commands": run_policy["blocked_commands"],
                },
            }), 200

        execution_env = build_execution_env(provided_env_vars)
        presentation_type = str(presentation.get("presentation_type") or "").strip().lower()

        if presentation_type == "web_app" and not str(execution_env.get("PORT") or "").strip():
            execution_env["PORT"] = str(allocate_free_local_port())
        provided_env_names = extract_provided_env_var_names(provided_env_vars)

        temp_repo_dir, clone_error = clone_repo_shallow(repo_url)

        if clone_error or not temp_repo_dir:
            return jsonify({
                "ok": True,
                "repo_url": repo_url,
                "decision": decision,
                "rewritten_plan": rewritten_plan,
                "validation": validation,
                "run_policy": run_policy,
                "presentation": presentation,
                "structured_user_actions": [],
                "session": None,
                "session_start": {
                    "attempted": True,
                    "success": False,
                    "reason": "Failed to clone repository for session start.",
                    "error": clone_error or "Failed to create temporary repository clone.",
                },
            }), 200

        temp_root_dir = os.path.dirname(temp_repo_dir)
        interpreter_selection = select_python_interpreter_for_repo(temp_repo_dir, execution_env)
        selected_python = str(interpreter_selection.get("selected_python") or "python3")
        original_setup_steps = list(validation.get("setup_steps") or [])

        rewritten_setup_steps = rewrite_setup_steps_for_python(
            original_setup_steps,
            selected_python,
        )

        effective_setup_steps = smart_plan_setup_steps(
            temp_repo_dir,
            rewritten_setup_steps,
            selected_python,
        )

        validation["setup_steps"] = list(effective_setup_steps)

        validation_rewritten_plan = validation.get("rewritten_plan")
        if isinstance(validation_rewritten_plan, dict):
            validation_rewritten_plan["final_setup_steps"] = list(effective_setup_steps)

        session_meta = create_session_from_repo_dir(
            repo_source_dir=temp_repo_dir,
            repo_url=repo_url,
            execution_env=execution_env,
            interpreter_selection=interpreter_selection,
            repo_analysis=raw_analysis,
            repo_decision=decision,
            effective_setup_steps=effective_setup_steps,
            setup_steps_executed=[],
            initial_logs=[],
            ttl_seconds=ttl_seconds,
        )

        try:
            if temp_root_dir and os.path.isdir(temp_root_dir):
                shutil.rmtree(temp_root_dir, ignore_errors=False)
        except Exception:
            pass

        admission_mode = str(run_policy.get("admission_mode") or "full")
        policy_blockers = _ensure_list_of_str(run_policy.get("policy_blockers"))
        blocked_commands = _ensure_list_of_str(run_policy.get("blocked_commands"))
        admission_summary = "Session created successfully."

        if admission_mode == "fallback":
            fallback_reason = policy_blockers[0] if policy_blockers else "Repository understanding is incomplete."
            admission_summary = (
                "Session created in fallback mode. "
                f"Primary reason: {fallback_reason} "
                "Tekarab started an exploratory session so you can continue safely in the terminal."
            )

        auto_start_thread = threading.Thread(
            target=_run_auto_start_primary_experience_background,
            kwargs={
                "session_id": session_meta["session_id"],
                "presentation": presentation,
                "validation": validation,
            },
            daemon=True,
            name=f"auto-start-{session_meta['session_id']}",
        )
        auto_start_thread.start()

        session_status = get_session_status(session_meta["session_id"])

        return jsonify({
            "ok": True,
            "repo_url": repo_url,
            "decision": decision,
            "rewritten_plan": rewritten_plan,
            "validation": validation,
            "run_policy": run_policy,
            "presentation": presentation,
            "admission_summary": admission_summary,
            "execution_env_var_names": provided_env_names,
            "session": session_status,
            "primary_experience": session_status.get("primary_experience"),
            "auto_start_primary_experience": {
                "attempted": False,
                "success": False,
                "background_started": True,
                "reason": _build_running_progress_message("auto_start"),
                "executed_setup_steps": [],
                "executed_run_steps": [],
                "stopped_after_detected_server": False,
                "primary_experience": session_status.get("primary_experience"),
            },
            "session_start": {
                "attempted": True,
                "success": True,
                "reason": admission_summary,
                "policy_blockers": policy_blockers,
                "blocked_commands": blocked_commands,
            },
        }), 200


    except Exception as e:
        return error_response(
            "Start repo session failed.",
            500,
            extra={
                "details": str(e),
                "traceback": traceback.format_exc(),
            },
        )

def _run_session_command_background(
    session_id: str,
    command: str,
    timeout_seconds: int | None,
    max_output_chars: int,
) -> None:
    try:
        try:
            _append_text(
                session_command_log_path(session_id),
                f"[{datetime.now(timezone.utc).isoformat()}] status=running command={command} (background thread started)\n",
            )
        except Exception as log_error:
            print(
                "[run_session_command_background] failed to append running log: "
                + repr(log_error),
                file=sys.stderr,
                flush=True,
            )

        run_kwargs = {
            "session_id": session_id,
            "command": command,
            "dangerous_patterns": DANGEROUS_PATTERNS,
            "interactive_patterns": INTERACTIVE_PATTERNS,
            "max_output_chars": max_output_chars,
        }

        if timeout_seconds is not None:
            run_kwargs["timeout_seconds"] = timeout_seconds

        run_session_command(**run_kwargs)

    except Exception:
        try:
            meta = load_session_meta(session_id) or {}
            meta["last_activity_at"] = datetime.now(timezone.utc).isoformat()
            meta["last_command"] = command
            meta["last_command_status"] = "failed"
            save_session_meta(session_id, meta)
        except Exception:
            pass

        try:
            _append_text(
                session_command_log_path(session_id),
                f"[{datetime.now(timezone.utc).isoformat()}] status=failed command={command} (background thread exception)\n",
            )
        except Exception:
            pass

        print(
            "[run_session_command_background] unexpected error\n"
            + traceback.format_exc(),
            flush=True,
        )


@app.route("/run-session-command", methods=["POST"])
def run_session_command_endpoint():
    cleanup_expired_sessions()

    data = request.get_json(silent=True) or {}
    session_id = str(data.get("session_id", "")).strip()
    command = str(data.get("command", "")).strip()
    timeout_seconds_raw = data.get("timeout_seconds")
    max_output_chars_raw = data.get("max_output_chars", MAX_LOG_CHARS)

    if not session_id:
        return error_response("Missing 'session_id'.", 400)

    if not command:
        return error_response("Missing 'command'.", 400)

    timeout_seconds = None
    if timeout_seconds_raw is not None:
        try:
            timeout_seconds = int(timeout_seconds_raw)
        except Exception:
            timeout_seconds = None

    try:
        max_output_chars = int(max_output_chars_raw)
    except Exception:
        max_output_chars = MAX_LOG_CHARS

    try:
        timeout_metadata = build_timeout_metadata(
            command=command,
            repo_context={},
            user_requested_timeout=timeout_seconds,
        )

        timeout_profile = str(timeout_metadata.get("timeout_profile") or "default")
        background_profiles = {"build", "test", "server"}
        should_background = timeout_profile in background_profiles

        if should_background:
              meta = load_session_meta(session_id) or {}

              background_processes = meta.get("background_processes")
              if not isinstance(background_processes, list):
                  background_processes = []

              active_background_processes = []
              for item in background_processes:
                  if not isinstance(item, dict):
                      continue

                  raw_pid = item.get("pid")
                  try:
                      pid = int(raw_pid)
                  except (TypeError, ValueError):
                      pid = 0

                  if pid > 0 and _pid_is_running(pid):
                      active_background_processes.append(item)

              if active_background_processes:
                  meta["background_processes"] = active_background_processes
                  meta["last_activity_at"] = datetime.now(timezone.utc).isoformat()
                  save_session_meta(session_id, meta)

                  running_commands = []
                  for item in active_background_processes[:5]:
                      command_summary = str(item.get("command") or "").strip()
                      if command_summary:
                          running_commands.append(command_summary)

                  return jsonify({
                      "ok": False,
                      "session_id": session_id,
                      "command": command,
                      "status": "failed",
                      "result_state": "failed",
                      "background_started": False,
                      "reason": "Another long-running command is already active in this session. Wait for it to finish, or start a new session if you want to run something else.",
                      "timeout_profile": timeout_profile,
                      "timeout_seconds_applied": timeout_metadata.get("timeout_seconds"),
                      "user_guidance": {
                          "what_worked": "",
                          "what_failed": "This command was blocked because another long-running background command is still active in the same session.",
                          "required_user_actions": [
                              "Wait for the active background command to finish, or start a new session before launching another long-running command."
                          ],
                          "next_commands": [],
                          "summary": "A background command is already running in this session. Review the active command list below before trying again.",
                      },
                      "active_background_commands": running_commands,
                      "session": get_session_status(session_id),
                  }), 409

              meta["last_activity_at"] = datetime.now(timezone.utc).isoformat()
              meta["last_command"] = command
              meta["last_command_status"] = "running"
              meta["last_command_result"] = {
                  "status": "running",
                  "result_state": "running",
                  "exit_code": 0,
                  "command": command,
                  "next_best_commands": [],
                  "next_step_suggestions": [],
                  "user_guidance": {
                      "what_worked": f'The command "{command}" started successfully. Check session status to follow its progress.',
                      "what_failed": "",
                      "required_user_actions": [],
                      "next_commands": [],
                      "summary": (
                          "This command is running. "
                          "Refresh the session status to follow progress. "
                          "If this command launches a web app, its preview link will appear on the side when it becomes ready."
                      ),
                  },
                  "detected_server": None,
                  "preview_diagnostics": None,
                  "execution_diagnostics": meta.get("execution_diagnostics") or {},
              }
              save_session_meta(session_id, meta)

              run_thread = threading.Thread(
                  target=_run_session_command_background,
                  kwargs={
                      "session_id": session_id,
                      "command": command,
                      "timeout_seconds": timeout_seconds,
                      "max_output_chars": max_output_chars,
                  },
                  daemon=True,
                  name=f"run-session-command-{session_id}",
              )
              run_thread.start()

              session_status = get_session_status(session_id)

              return jsonify({
                  "ok": True,
                  "session_id": session_id,
                  "command": command,
                  "status": "running",
                  "result_state": "running",
                  "background_started": True,
                  "reason": _build_running_progress_message("command"),
                  "timeout_profile": timeout_profile,
                  "timeout_seconds_applied": timeout_metadata.get("timeout_seconds"),
                  "session": session_status,
              }), 200

        run_kwargs = {
            "session_id": session_id,
            "command": command,
            "dangerous_patterns": DANGEROUS_PATTERNS,
            "interactive_patterns": INTERACTIVE_PATTERNS,
            "max_output_chars": max_output_chars,
        }

        if timeout_seconds is not None:
            run_kwargs["timeout_seconds"] = timeout_seconds

        result = run_session_command(**run_kwargs)
        return jsonify(result), 200

    except FileNotFoundError as e:
        return error_response(str(e), 404)
    except ValueError as e:
        return error_response(str(e), 400)
    except Exception as e:
        return error_response(
            "Run session command failed.",
            500,
            extra={
                "details": str(e),
                "traceback": traceback.format_exc(),
            },
        )


@app.route("/run-session-auto-fix", methods=["POST"])
def run_session_auto_fix():
    data = request.get_json(force=True) or {}

    session_id = str(data.get("session_id") or "").strip()
    command = str(data.get("command") or "").strip()

    if not session_id:
        return error_response("session_id is required", 400)

    if not command:
        return error_response("command is required", 400)

    timeout_seconds = data.get("timeout_seconds")
    try:
        timeout_seconds = int(timeout_seconds) if timeout_seconds is not None else None
    except Exception:
        return error_response("timeout_seconds must be an integer", 400)

    effective_timeout = timeout_seconds or DEFAULT_SESSION_COMMAND_TIMEOUT_SECONDS

    def _build_exception_result(
        failed_command: str,
        exc: Exception,
        phase: str,
    ) -> dict[str, Any]:
        is_timeout = isinstance(exc, subprocess.TimeoutExpired)
        exception_name = exc.__class__.__name__
        details = str(exc)

        if is_timeout:
            details = (
                f"Command '{failed_command}' timed out after {effective_timeout} seconds."
            )

        result = {
            "command": failed_command,
            "status": "failed",
            "exit_code": None,
            "stdout": "",
            "stderr": details,
            "duration_ms": None,
            "timed_out": is_timeout,
            "timeout_seconds": effective_timeout,
            "smart_error_hint": {
                "category": "command_timeout" if is_timeout else "command_execution_exception",
                "title": "Command timed out" if is_timeout else "Command execution failed",
                "hint": (
                    "The command exceeded the allowed timeout."
                    if is_timeout
                    else "The command raised an exception before a normal result was returned."
                ),
                "details": details,
                "suggested_commands": [],
                "auto_fix": {
                    "strategy": "manual_retry",
                    "summary": (
                        "Review the command, logs, and timeout before retrying."
                        if is_timeout
                        else "Review the command and logs before retrying."
                    ),
                    "commands": [],
                    "confidence": "low",
                },
                "confidence": "high" if is_timeout else "medium",
            },
            "next_best_commands": [],
            "next_step_suggestions": (
                [
                    f"Review whether '{failed_command}' is the correct command for this repository.",
                    f"Retry with a longer timeout if {effective_timeout} seconds is too short for this command.",
                ]
                if is_timeout
                else [
                    f"Review whether '{failed_command}' is the correct command for this repository.",
                ]
            ),
            "exception_type": exception_name,
            "exception_details": str(exc),
            "failure_phase": phase,
        }

        return result

    try:
        result = run_session_command(
            session_id=session_id,
            command=command,
            dangerous_patterns=DANGEROUS_PATTERNS,
            interactive_patterns=INTERACTIVE_PATTERNS,
            timeout_seconds=effective_timeout,
        )
    except Exception as e:
        result = _build_exception_result(
            failed_command=command,
            exc=e,
            phase="initial_command",
        )

    initial_status = str(result.get("status") or "").strip().lower()
    initial_success = initial_status == "success"

    output = {
        "initial_result": result,
        "initial_success": initial_success,
        "auto_fix_applied": False,
        "auto_fix_command": None,
        "auto_fix_result": None,
        "auto_fix_skipped_reason": None,
        "final_result": result,
        "final_success": initial_success,
    }

    if not initial_success:
        smart_hint = result.get("smart_error_hint") or {}
        auto_fix = smart_hint.get("auto_fix") or {}
        fix_commands = auto_fix.get("commands") or []
        fix_confidence = str(auto_fix.get("confidence") or "").strip().lower()
        fix_strategy = str(auto_fix.get("strategy") or "").strip().lower()

        allowed_strategies = {"command_substitution"}

        if fix_confidence != "high":
            output["auto_fix_skipped_reason"] = str(
                smart_hint.get("no_auto_fix_reason")
                or "No safe automatic fix is available for this issue."
            )
        elif fix_strategy not in allowed_strategies:
            output["auto_fix_skipped_reason"] = (
                f"auto_fix strategy '{fix_strategy}' is not allowed"
            )
        elif not isinstance(fix_commands, list) or not fix_commands:
            output["auto_fix_skipped_reason"] = "no auto_fix commands available"
        else:
            best_command = str(fix_commands[0]).strip()

            if not best_command:
                output["auto_fix_skipped_reason"] = "best auto_fix command is empty"
            else:
                output["auto_fix_applied"] = True
                output["auto_fix_command"] = best_command

                try:
                    fix_result = run_session_command(
                        session_id=session_id,
                        command=best_command,
                        dangerous_patterns=DANGEROUS_PATTERNS,
                        interactive_patterns=INTERACTIVE_PATTERNS,
                        timeout_seconds=effective_timeout,
                    )
                except Exception as e:
                    fix_result = _build_exception_result(
                        failed_command=best_command,
                        exc=e,
                        phase="auto_fix_command",
                    )
                    output["auto_fix_skipped_reason"] = (
                        f"auto_fix execution failed: {fix_result.get('stderr')}"
                    )

                fix_status = str(fix_result.get("status") or "").strip().lower()
                fix_success = fix_status == "success"

                output["auto_fix_result"] = fix_result
                output["final_result"] = fix_result
                output["final_success"] = fix_success

    return jsonify({
        "ok": True,
        "data": output,
        "timestamp": utc_now_iso(),
    })


@app.route("/session-status", methods=["GET", "POST"])
def session_status_endpoint():
    cleanup_expired_sessions()

    if request.method == "GET":
        session_id = str(request.args.get("session_id", "")).strip()
    else:
        data = request.get_json(silent=True) or {}
        session_id = str(data.get("session_id", "")).strip()

    if not session_id:
        return error_response("Missing 'session_id'.", 400)

    try:
        status_payload = get_session_status(session_id)
        return jsonify({
            "ok": True,
            "session": status_payload,
        }), 200

    except FileNotFoundError as e:
        return error_response(str(e), 404)
    except ValueError as e:
        return error_response(str(e), 400)
    except Exception as e:
        return error_response(
            "Session status failed.",
            500,
            extra={
                "details": str(e),
                "traceback": traceback.format_exc(),
            },
        )


@app.route("/session-files", methods=["GET", "POST"])
def session_files_endpoint():
    if request.method == "GET":
        session_id = str(request.args.get("session_id", "")).strip()
        relative_path = str(request.args.get("path", "")).strip()
    else:
        data = request.get_json(silent=True) or {}
        session_id = str(data.get("session_id", "")).strip()
        relative_path = str(data.get("path", "")).strip()

    if not session_id:
        return error_response("Missing 'session_id'.", 400)

    try:
        payload = list_session_files(session_id, relative_path)
        return jsonify({
            "ok": True,
            "session_id": session_id,
            "files": payload,
        }), 200

    except FileNotFoundError as e:
        return error_response(str(e), 404)
    except NotADirectoryError as e:
        return error_response(str(e), 400)
    except ValueError as e:
        return error_response(str(e), 400)
    except Exception as e:
        return error_response(
            "Session file listing failed.",
            500,
            extra={
                "details": str(e),
                "traceback": traceback.format_exc(),
            },
        )

@app.route("/session-file-content", methods=["GET", "POST"])
def session_file_content_endpoint():
    if request.method == "GET":
        session_id = str(request.args.get("session_id", "")).strip()
        relative_path = str(request.args.get("path", "")).strip()
    else:
        data = request.get_json(silent=True) or {}
        session_id = str(data.get("session_id", "")).strip()
        relative_path = str(data.get("path", "")).strip()

    if not session_id:
        return error_response("Missing 'session_id'.", 400)

    if not relative_path:
        return error_response("Missing 'path'.", 400)

    try:
        payload = read_session_file_content(session_id, relative_path)
        return jsonify({
            "ok": True,
            "session_id": session_id,
            "file": payload,
        }), 200

    except FileNotFoundError as e:
        return error_response(str(e), 404)
    except IsADirectoryError as e:
        return error_response(str(e), 400)
    except ValueError as e:
        return error_response(str(e), 400)
    except Exception as e:
        return error_response(
            "Session file content lookup failed.",
            500,
            extra={
                "details": str(e),
                "traceback": traceback.format_exc(),
            },
        )


@app.route("/session-app-target", methods=["GET", "POST"])
def session_app_target_endpoint():
    cleanup_expired_sessions()

    if request.method == "GET":
        session_id = str(request.args.get("session_id", "")).strip()
    else:
        data = request.get_json(silent=True) or {}
        session_id = str(data.get("session_id", "")).strip()

    if not session_id:
        return error_response("Missing 'session_id'.", 400)

    try:
        status_payload = get_session_status(session_id)
        detected_server = status_payload.get("detected_server") or {}
        port = detected_server.get("port")

        if not port:
            return error_response(
                "No detected running server for this session.",
                404,
                extra={
                    "session_id": session_id,
                    "session": status_payload,
                },
            )

        return jsonify({
            "ok": True,
            "session_id": session_id,
            "target_host": "127.0.0.1",
            "target_port": port,
            "target_base_url": f"http://127.0.0.1:{port}",
            "public_url": status_payload.get("public_url"),
            "detected_server": detected_server,
        }), 200

    except FileNotFoundError as e:
        return error_response(str(e), 404)
    except ValueError as e:
        return error_response(str(e), 400)
    except Exception as e:
        return error_response(
            "Session app target lookup failed.",
            500,
            extra={
                "details": str(e),
                "traceback": traceback.format_exc(),
            },
        )


@app.route("/delete-session", methods=["POST"])
def delete_session_endpoint():
    data = request.get_json(silent=True) or {}
    session_id = str(data.get("session_id", "")).strip()

    if not session_id:
        return error_response("Missing 'session_id'.", 400)

    try:
        result = delete_runtime_session(session_id)
        return jsonify({
            "ok": True,
            **result,
        }), 200

    except ValueError as e:
        return error_response(str(e), 400)
    except Exception as e:
        return error_response(
            "Delete session failed.",
            500,
            extra={
                "details": str(e),
                "traceback": traceback.format_exc(),
            },
        )


@app.route("/session-command-log", methods=["GET", "POST"])
def session_command_log_endpoint():
    cleanup_expired_sessions()

    if request.method == "GET":
        session_id = str(request.args.get("session_id", "")).strip()
        max_chars_raw = request.args.get("max_chars", MAX_LOG_CHARS)
    else:
        data = request.get_json(silent=True) or {}
        session_id = str(data.get("session_id", "")).strip()
        max_chars_raw = data.get("max_chars", MAX_LOG_CHARS)

    if not session_id:
        return error_response("Missing 'session_id'.", 400)

    try:
        max_chars = int(max_chars_raw)
    except Exception:
        max_chars = MAX_LOG_CHARS

    try:
        result = read_session_command_log(session_id, max_chars=max_chars)
        return jsonify(result), 200

    except FileNotFoundError as e:
        return error_response(str(e), 404)
    except ValueError as e:
        return error_response(str(e), 400)
    except Exception as e:
        return error_response(
            "Read session command log failed.",
            500,
            extra={
                "details": str(e),
                "traceback": traceback.format_exc(),
            },
        )


@app.route("/s/<session_id>/", defaults={"path": ""}, methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
@app.route("/s/<session_id>/<path:path>", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
def proxy_session_app(session_id: str, path: str):
    try:
        status_payload = get_session_status(session_id)
        detected_server = status_payload.get("detected_server") or {}
        port = detected_server.get("port")
        target_host = detected_server.get("host") or "127.0.0.1"

        if not port:
            return make_response("No running app for this session.", 404)

        target_url = f"http://{target_host}:{port}/{path}"

        if request.query_string:
            target_url += "?" + request.query_string.decode()

        resp = requests.request(
            method=request.method,
            url=target_url,
            headers={
                key: value
                for key, value in request.headers
                if key.lower() != "host"
            },
            data=request.get_data(),
            cookies=request.cookies,
            allow_redirects=False,
            stream=True,
        )

        excluded_headers = {"content-encoding", "content-length", "transfer-encoding", "connection"}
        content_type = resp.headers.get("Content-Type", "")
        proxy_prefix = f"/s/{session_id}"

        response_headers = []
        cache_headers_to_strip = {
            "cache-control",
            "etag",
            "expires",
            "last-modified",
            "age",
        }

        for name, value in resp.raw.headers.items():
            lower_name = name.lower()
            if lower_name in excluded_headers:
                continue
            if lower_name in cache_headers_to_strip:
                continue
            if lower_name == "location" and value.startswith("/"):
                value = f"{proxy_prefix}{value}"
            response_headers.append((name, value))

        response_headers.append(("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0"))
        response_headers.append(("Pragma", "no-cache"))
        response_headers.append(("Expires", "0"))

        if "text/html" in content_type.lower():
            html = resp.text
            query_suffix = ""
            if request.query_string:
                query_suffix = "?" + request.query_string.decode()

            html = html.replace('src="/', f'src="{proxy_prefix}/')
            html = html.replace("src='/", f"src='{proxy_prefix}/")
            if query_suffix:
                html = re.sub(r'(src="[^"]+")(?!\?)', lambda m: m.group(1)[:-1] + query_suffix + '"', html)
                html = re.sub(r"(src='[^']+')(?!\?)", lambda m: m.group(1)[:-1] + query_suffix + "'", html)

            html = html.replace('href="/', f'href="{proxy_prefix}/')
            html = html.replace("href='/", f"href='{proxy_prefix}/")
            html = html.replace('content="/', f'content="{proxy_prefix}/')
            html = html.replace("content='/", f"content='{proxy_prefix}/")
            html = html.replace(' from "/', f' from "{proxy_prefix}/')
            html = html.replace(" from '/", f" from '{proxy_prefix}/")
            html = html.replace('import "/', f'import "{proxy_prefix}/')
            html = html.replace("import '/", f"import '{proxy_prefix}/")
            html = html.replace('import("/', f'import("{proxy_prefix}/')
            html = html.replace("import('/", f"import('{proxy_prefix}/")
            html = html.replace('fetch("/', f'fetch("{proxy_prefix}/')
            html = html.replace("fetch('/", f"fetch('{proxy_prefix}/")
            html = html.replace('url("/', f'url("{proxy_prefix}/')
            html = html.replace("url('/", f"url('{proxy_prefix}/")
            html = html.replace("url(/", f"url({proxy_prefix}/")

            def _rewrite_srcset_attr(match: re.Match[str]) -> str:
                quote = match.group(1)
                value = match.group(2)
                rewritten_items = []

                for raw_item in value.split(","):
                    item = raw_item.strip()
                    if not item:
                        continue

                    parts = item.split()
                    if parts and parts[0].startswith("/") and not parts[0].startswith(f"{proxy_prefix}/"):
                        parts[0] = f"{proxy_prefix}{parts[0]}"

                    rewritten_items.append(" ".join(parts))

                return f"srcset={quote}{', '.join(rewritten_items)}{quote}"

            html = re.sub(
                r'srcset=(["\'])(.*?)\1',
                _rewrite_srcset_attr,
                html,
                flags=re.IGNORECASE | re.DOTALL,
            )

            html = re.sub(
                r'<script\b[^>]*\bsrc=["\'][^"\']*/@vite/client[^"\']*["\'][^>]*>\s*</script>\s*',
                "",
                html,
                flags=re.IGNORECASE | re.DOTALL,
            )
            html = re.sub(
                r'<script\b[^>]*>.*?/@react-refresh.*?</script>\s*',
                "",
                html,
                flags=re.IGNORECASE | re.DOTALL,
            )
            html = re.sub(
                r'<script\b[^>]*>.*?/@vite/client.*?</script>\s*',
                "",
                html,
                flags=re.IGNORECASE | re.DOTALL,
            )
            html = re.sub(
                r'<script\b[^>]*>.*?astro:toolbar.*?</script>\s*',
                "",
                html,
                flags=re.IGNORECASE | re.DOTALL,
            )
            html = re.sub(
                r'<astro-dev-toolbar\b[^>]*>.*?</astro-dev-toolbar>\s*',
                "",
                html,
                flags=re.IGNORECASE | re.DOTALL,
            )
            html = re.sub(
                r'<script\b[^>]*\bsrc=["\'][^"\']*/@fs/[^"\']*["\'][^>]*>\s*</script>\s*',
                "",
                html,
                flags=re.IGNORECASE | re.DOTALL,
            )
            html = re.sub(
                r'<script\b[^>]*\bsrc=["\'][^"\']*/src/[^"\']*\?astro&type=script[^"\']*["\'][^>]*>\s*</script>\s*',
                "",
                html,
                flags=re.IGNORECASE | re.DOTALL,
            )
            html = re.sub(
                r'<script\b[^>]*\bsrc=["\'][^"\']*/src/styles/[^"\']*\.less[^"\']*["\'][^>]*>\s*</script>\s*',
                "",
                html,
                flags=re.IGNORECASE | re.DOTALL,
            )
            html = re.sub(
                r'<script\b[^>]*\bsrc=["\'][^"\']*dev-toolbar[^"\']*["\'][^>]*>\s*</script>\s*',
                "",
                html,
                flags=re.IGNORECASE | re.DOTALL,
            )

            html = re.sub(
                r'<script\b[^>]*>\s*window\.__astro_dev_toolbar__\s*=.*?</script>\s*',
                "",
                html,
                flags=re.IGNORECASE | re.DOTALL,
            )
            html = re.sub(
                r'<script\b[^>]*\bsrc=["\'][^"\']*\?astro&type=style[^"\']*["\'][^>]*>\s*</script>\s*',
                "",
                html,
                flags=re.IGNORECASE | re.DOTALL,
            )
            return html, resp.status_code, response_headers

        if (
            "javascript" in content_type.lower()
            or "ecmascript" in content_type.lower()
            or path.endswith(".js")
            or path.endswith(".mjs")
            or path.endswith(".ts")
            or path.endswith(".tsx")
            or path.endswith(".css")
        ):
            body = resp.text
            query_suffix = ""
            if request.query_string:
                query_suffix = "?" + request.query_string.decode()
            body = body.replace(' from "/', f' from "{proxy_prefix}/')
            body = body.replace(" from '/", f" from '{proxy_prefix}/")
            body = body.replace('import "/', f'import "{proxy_prefix}/')
            body = body.replace("import '/", f"import '{proxy_prefix}/")
            body = body.replace('import("/', f'import("{proxy_prefix}/')
            body = body.replace("import('/", f"import('{proxy_prefix}/")
            body = body.replace('fetch("/', f'fetch("{proxy_prefix}/')
            body = body.replace("fetch('/", f"fetch('{proxy_prefix}/")
            body = body.replace('url("/', f'url("{proxy_prefix}/')
            body = body.replace("url('/", f"url('{proxy_prefix}/")
            body = body.replace("url(/", f"url({proxy_prefix}/")
            body = body.replace('srcset="/', f'srcset="{proxy_prefix}/')
            body = body.replace("srcset='/", f"srcset='{proxy_prefix}/")
            body = body.replace(
                "const defines = __DEFINES__;",
                'const defines = typeof __DEFINES__ !== "undefined" ? __DEFINES__ : {};',
            )
            body = body.replace(
                "const hmrConfigName = __HMR_CONFIG_NAME__;",
                'const hmrConfigName = typeof __HMR_CONFIG_NAME__ !== "undefined" ? __HMR_CONFIG_NAME__ : "";',
            )
            body = body.replace(
                "const base$1 = __BASE__ || \"/\";",
                'const base$1 = typeof __BASE__ !== "undefined" && __BASE__ ? __BASE__ : "/";',
            )
            body = body.replace(
                "const serverHost = __SERVER_HOST__;",
                'const serverHost = typeof __SERVER_HOST__ !== "undefined" ? __SERVER_HOST__ : "";',
            )
            body = body.replace(
                'const socketProtocol = __HMR_PROTOCOL__ || (importMetaUrl.protocol === "https:" ? "wss" : "ws");',
                'const socketProtocol = (typeof __HMR_PROTOCOL__ !== "undefined" && __HMR_PROTOCOL__) || (importMetaUrl.protocol === "https:" ? "wss" : "ws");',
            )
            body = body.replace(
                "const hmrPort = __HMR_PORT__;",
                'const hmrPort = typeof __HMR_PORT__ !== "undefined" ? __HMR_PORT__ : "";',
            )
            body = body.replace(
                'const socketHost = `${__HMR_HOSTNAME__ || importMetaUrl.hostname}:${hmrPort || importMetaUrl.port}${__HMR_BASE__}`;',
                'const socketHost = `${(typeof __HMR_HOSTNAME__ !== "undefined" && __HMR_HOSTNAME__) || importMetaUrl.hostname}:${hmrPort || importMetaUrl.port}${typeof __HMR_BASE__ !== "undefined" && __HMR_BASE__ ? __HMR_BASE__ : "/"}`;',
            )
            body = body.replace(
                "const directSocketHost = __HMR_DIRECT_TARGET__;",
                'const directSocketHost = typeof __HMR_DIRECT_TARGET__ !== "undefined" ? __HMR_DIRECT_TARGET__ : "";',
            )
            body = body.replace(
                "const hmrTimeout = __HMR_TIMEOUT__;",
                'const hmrTimeout = typeof __HMR_TIMEOUT__ !== "undefined" ? __HMR_TIMEOUT__ : 30000;',
            )
            body = body.replace(
                "const wsToken = __WS_TOKEN__;",
                'const wsToken = typeof __WS_TOKEN__ !== "undefined" ? __WS_TOKEN__ : "";',
            )
            body = body.replace(
                "const enableOverlay = __HMR_ENABLE_OVERLAY__;",
                'const enableOverlay = typeof __HMR_ENABLE_OVERLAY__ !== "undefined" ? __HMR_ENABLE_OVERLAY__ : false;',
            )
            body = body.replace(
                'const base = __BASE__ || "/";',
                'const base = typeof __BASE__ !== "undefined" && __BASE__ ? __BASE__ : "/";',
            )
            if query_suffix:
                body = re.sub(
                    r'(["\'])(' + re.escape(proxy_prefix) + r'/[^"\']+)(\1)',
                    lambda m: f'{m.group(1)}{m.group(2)}{query_suffix}{m.group(3)}' if "?" not in m.group(2) else m.group(0),
                    body,
                )
            if path.endswith(".css"):
                body = re.sub(
                    r'import[^\n]*@vite/client[^\n]*\n?',
                    "",
                    body,
                )
            body = re.sub(
                r'import\.meta\.hot\s*=\s*__vite__createHotContext\([^)]*\);?',
                "",
                body,
            )
            body = re.sub(
                r'import\.meta\.hot\.accept\(\);?',
                "",
                body,
            )
            body = re.sub(
                r'import\.meta\.hot\.prune\(\(\)\s*=>\s*__vite__removeStyle\(__vite__id\)\);?',
                "",
                body,
            )

            return body, resp.status_code, response_headers

        return resp.content, resp.status_code, response_headers

    except Exception as e:
        return make_response(f"Proxy error: {str(e)}", 500)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
