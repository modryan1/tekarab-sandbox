from __future__ import annotations

import json
import logging
import os
import re
import shutil
import signal
import socket
import subprocess
import time
import uuid
from urllib.parse import urlparse
from datetime import datetime, timezone
from typing import Any, Dict, List

from smart_error_hints import build_smart_error_hint
from smart_timeout import build_timeout_metadata
from strategy_insights import categorize_install_failure, classify_preview_diagnostics


SESSION_ROOT = "/tmp/tekarab-sessions"
DEFAULT_SESSION_TTL_SECONDS = 300
DEFAULT_MAX_OUTPUT_CHARS = 12000
logger = logging.getLogger(__name__)

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def truncate_output(text: Any, limit: int = DEFAULT_MAX_OUTPUT_CHARS) -> str:
    text = str(text or "")
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]"


def ensure_session_root() -> str:
    os.makedirs(SESSION_ROOT, exist_ok=True)
    return SESSION_ROOT


def generate_session_id() -> str:
    return uuid.uuid4().hex[:24]


def session_dir(session_id: str) -> str:
    return os.path.join(SESSION_ROOT, session_id)


def session_repo_dir(session_id: str) -> str:
    return os.path.join(session_dir(session_id), "repo")


def session_meta_path(session_id: str) -> str:
    return os.path.join(session_dir(session_id), "meta.json")


def session_command_log_path(session_id: str) -> str:
    return os.path.join(session_dir(session_id), "command.log")


def _read_json(path: str) -> Dict[str, Any]:
    exists = os.path.exists(path)
    logger.warning("[read_json] path=%s exists=%s", path, exists)

    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    if path.endswith("/meta.json") or path.endswith("meta.json"):
        if isinstance(payload, dict):
               logger.warning("[load_meta] path=%s keys=%s", path, sorted(payload.keys()))
        else:
            logger.warning("[load_meta] path=%s payload_type=%s", path, type(payload).__name__)

    return payload


def _write_json(path: str, payload: Dict[str, Any]) -> None:
    tmp_path = path + ".tmp"

    if path.endswith("/meta.json") or path.endswith("meta.json"):
        if isinstance(payload, dict):
            logger.warning(
                "[save_meta] start path=%s tmp_path=%s keys=%s",
                path,
                tmp_path,
                sorted(payload.keys()),
            )
        else:
            logger.warning(
                "[save_meta] start path=%s tmp_path=%s payload_type=%s",
                path,
                tmp_path,
                type(payload).__name__,
            )

    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    os.replace(tmp_path, path)

    if path.endswith("/meta.json") or path.endswith("meta.json"):
        try:
            size = os.path.getsize(path)
        except OSError:
            size = -1
        logger.warning("[save_meta] success path=%s size=%s", path, size)

def _append_text(path: str, text: str) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(text)


def _build_public_url(session_id: str, detected_server: Dict[str, Any] | None) -> str | None:
    if not isinstance(detected_server, dict) or not detected_server.get("port"):
        return None

    raw_url = str(detected_server.get("url") or "").strip()
    if not raw_url:
        return f"https://sandbox.tekarab.com/s/{session_id}/"

    try:
        parsed = urlparse(raw_url)
    except Exception:
        return f"https://sandbox.tekarab.com/s/{session_id}/"

    path = str(parsed.path or "").strip()
    if not path or path == "/":
        return f"https://sandbox.tekarab.com/s/{session_id}/"

    normalized_path = "/" + path.lstrip("/")
    return f"https://sandbox.tekarab.com/s/{session_id}{normalized_path}"


def _is_detected_server_reachable(detected_server: Dict[str, Any] | None, timeout_seconds: float = 0.5) -> bool:
    if not isinstance(detected_server, dict):
        return False

    host = str(detected_server.get("host") or "127.0.0.1").strip() or "127.0.0.1"
    port = detected_server.get("port")

    try:
        port_int = int(port)
    except Exception:
        return False

    try:
        with socket.create_connection((host, port_int), timeout=timeout_seconds):
            return True
    except OSError:
        return False


def _scan_root_relative_web_refs(repo_dir: str, max_matches: int = 8) -> List[str]:
    candidate_dirs = [
        os.path.join(repo_dir, "src"),
        os.path.join(repo_dir, "public"),
    ]
    allowed_suffixes = (
        ".astro",
        ".html",
        ".htm",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".vue",
        ".svelte",
        ".css",
        ".xml",
    )
    patterns = [
        'href="/',
        'src="/',
        'action="/',
        'url(/',
        'content="/',
    ]

    matches: List[str] = []

    for base_dir in candidate_dirs:
        if not os.path.isdir(base_dir):
            continue

        for root, _, files in os.walk(base_dir):
            for name in files:
                if not name.endswith(allowed_suffixes):
                    continue

                path = os.path.join(root, name)

                try:
                    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                        for lineno, line in enumerate(fh, start=1):
                            if any(pattern in line for pattern in patterns):
                                rel_path = os.path.relpath(path, repo_dir)
                                matches.append(f"{rel_path}:{lineno}: {line.strip()}")
                                if len(matches) >= max_matches:
                                    return matches
                except OSError:
                    continue

    return matches


def _build_preview_diagnostics(
    repo_dir: str,
    session_id: str,
    detected_server: Dict[str, Any] | None,
) -> Dict[str, Any] | None:
    public_url = _build_public_url(session_id, detected_server)
    if not public_url:
        return None

    if f"/s/{session_id}/" not in public_url:
        return None

    root_relative_refs = _scan_root_relative_web_refs(repo_dir)
    if not root_relative_refs:
        return None

    return {
        "issue_type": "subpath_preview_compatibility",
        "severity": "warning",
        "public_url": public_url,
        "message": (
            "The app is being previewed from a session subpath. "
            "Root-relative links or assets may break under /s/<session_id>/ previews."
        ),
        "evidence": root_relative_refs,
    }


# ==============================
# CREATE SESSION (FIXED CONTRACT)
# ==============================
def create_session_from_repo_dir(
    repo_source_dir: str,
    repo_url: str,
    execution_env: Dict[str, str],
    interpreter_selection: Dict[str, Any] | None = None,
    repo_analysis: Dict[str, Any] | None = None,
    repo_decision: Dict[str, Any] | None = None,
    effective_setup_steps: List[str] | None = None,
    setup_steps_executed: List[str] | None = None,
    initial_logs: List[Dict[str, Any]] | None = None,
    ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS,
) -> Dict[str, Any]:

    ensure_session_root()

    session_id = generate_session_id()
    root_dir = session_dir(session_id)
    repo_dir = session_repo_dir(session_id)

    os.makedirs(root_dir, exist_ok=False)
    shutil.move(repo_source_dir, repo_dir)

    now = utc_now_iso()

    meta = {
        "session_id": session_id,
        "repo_url": str(repo_url or ""),
        "status": "ready",
        "created_at": now,
        "last_activity_at": now,
        "ttl_seconds": int(ttl_seconds),
        "repo_dir": repo_dir,
        "env": {str(k): str(v) for k, v in (execution_env or {}).items()},
        "interpreter_selection": interpreter_selection or {},
        "repo_analysis": repo_analysis or {},
        "repo_decision": repo_decision or {},
        "effective_setup_steps": list(effective_setup_steps or []),
        "setup_steps_executed": list(setup_steps_executed or []),
        "initial_logs": list(initial_logs or []),
        "command_count": 0,
        "last_command": None,
        "last_command_status": None,
        "execution_diagnostics": {},
        "background_processes": [],
    }

    _write_json(session_meta_path(session_id), meta)

    _append_text(
        session_command_log_path(session_id),
        f"[{now}] session created for repo: {repo_url}\n",
    )

    return meta


def run_auto_setup_if_needed(
    repo_dir: str,
    command: str,
    env: Dict[str, str],
) -> List[Dict[str, Any]]:
    logs: List[Dict[str, Any]] = []

    normalized_command = str(command or "").strip().lower()
    if not normalized_command:
        return logs

    wants_researchclaw = "researchclaw" in normalized_command

    venv_python = os.path.join(repo_dir, ".venv", "bin", "python")
    venv_pip = os.path.join(repo_dir, ".venv", "bin", "pip")
    has_venv = os.path.exists(venv_python)

    has_pyproject = os.path.exists(os.path.join(repo_dir, "pyproject.toml"))
    has_setup_py = os.path.exists(os.path.join(repo_dir, "setup.py"))
    has_requirements = os.path.exists(os.path.join(repo_dir, "requirements.txt"))
    has_researchclaw_pkg = os.path.exists(os.path.join(repo_dir, "researchclaw"))

    is_python_repo = has_pyproject or has_setup_py or has_requirements or has_researchclaw_pkg

    if not is_python_repo:
        return logs

    has_config = (
        os.path.exists(os.path.join(repo_dir, "config.yaml")) or
        os.path.exists(os.path.join(repo_dir, "config.yml")) or
        os.path.exists(os.path.join(repo_dir, "config.arc.yaml"))
    )

    def _run(step: str) -> Dict[str, Any]:
        start = time.time()

        timeout_metadata = build_timeout_metadata(
            command=step,
            repo_context={},
            user_requested_timeout=None,
        )
        effective_timeout = int(timeout_metadata["timeout_seconds"])

        result = subprocess.run(
            ["bash", "-lc", step],
            cwd=repo_dir,
            env=env,
            capture_output=True,
            text=True,
            timeout=effective_timeout,
        )
        return {
            "stage": "auto_setup",
            "step": step,
            "status": "success" if result.returncode == 0 else "failed",
            "exit_code": result.returncode,
            "duration_seconds": round(time.time() - start, 3),
            "stdout": truncate_output(result.stdout),
            "stderr": truncate_output(result.stderr),
            "timeout_profile": timeout_metadata.get("timeout_profile"),
            "timeout_seconds_applied": effective_timeout,
        }

    def _skip(step: str, reason: str) -> Dict[str, Any]:
        return {
            "stage": "auto_setup",
            "step": step,
            "status": "skipped",
            "exit_code": None,
            "duration_seconds": 0.0,
            "stdout": "",
            "stderr": str(reason or ""),
        }

    venv_step = "python3.11 -m venv .venv"

    if not has_venv:
        log_item = _run(venv_step)
        logs.append(log_item)

        if log_item["status"] != "success":
            return logs

        has_venv = True
    else:
        logs.append(_skip(venv_step, "Virtual environment already exists."))

    editable_install_step = "./.venv/bin/pip install -e ."
    requirements_install_step = "./.venv/bin/pip install -r requirements.txt"
    requirements_path = os.path.join(repo_dir, "requirements.txt")
    requirements_stamp = os.path.join(repo_dir, ".venv", ".tekarab_requirements_installed")

    if os.path.exists(venv_pip):
        if has_pyproject or has_setup_py:
            egg_info = any(name.endswith(".egg-info") for name in os.listdir(repo_dir))
            dist_info = any(name.endswith(".dist-info") for name in os.listdir(repo_dir))

            if not egg_info and not dist_info:
                log_item = _run(editable_install_step)
                logs.append(log_item)

                if log_item["status"] != "success":
                    return logs
            else:
                logs.append(_skip(editable_install_step, "Project already appears to be installed."))
        else:
            logs.append(_skip(editable_install_step, "No pyproject.toml or setup.py found."))

        if has_requirements:
            try:
                requirements_mtime = os.path.getmtime(requirements_path)
            except OSError:
                requirements_mtime = 0.0

            try:
                stamp_mtime = os.path.getmtime(requirements_stamp)
            except OSError:
                stamp_mtime = 0.0

            if stamp_mtime < requirements_mtime:
                log_item = _run(requirements_install_step)
                logs.append(log_item)

                if log_item["status"] != "success":
                    return logs

                try:
                    with open(requirements_stamp, "w", encoding="utf-8") as fh:
                        fh.write(utc_now_iso() + "\n")
                except OSError:
                    pass
            else:
                logs.append(_skip(requirements_install_step, "Requirements already appear to be installed."))
        else:
            logs.append(_skip(requirements_install_step, "No requirements.txt found."))
    else:
        logs.append(_skip(editable_install_step, "Virtualenv pip was not found."))
        logs.append(_skip(requirements_install_step, "Virtualenv pip was not found."))

    init_step = "./.venv/bin/python -m researchclaw init"

    if wants_researchclaw and has_researchclaw_pkg:
        if not has_config and os.path.exists(venv_python):
            log_item = _run(init_step)
            logs.append(log_item)
        elif has_config:
            logs.append(_skip(init_step, "Configuration file already exists."))
        else:
            logs.append(_skip(init_step, "Virtualenv python was not found."))
    else:
        logs.append(_skip(init_step, "Command does not require ResearchClaw init."))

    return logs


# ==============================
# BASIC HELPERS
# ==============================
def load_session_meta(session_id: str) -> Dict[str, Any]:
    return _read_json(session_meta_path(session_id))


def save_session_meta(session_id: str, meta: Dict[str, Any]) -> None:
    _write_json(session_meta_path(session_id), meta)


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _terminate_background_process(pid: int) -> bool:
    if pid <= 0:
        return False

    try:
        os.killpg(pid, signal.SIGTERM)
    except OSError:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            return not _pid_is_running(pid)

    for _ in range(10):
        if not _pid_is_running(pid):
            return True
        time.sleep(0.2)

    try:
        os.killpg(pid, signal.SIGKILL)
    except OSError:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            return not _pid_is_running(pid)

    for _ in range(10):
        if not _pid_is_running(pid):
            return True
        time.sleep(0.1)

    return not _pid_is_running(pid)


def _cleanup_session_background_processes(meta: Dict[str, Any]) -> List[int]:
    processes = meta.get("background_processes") or []
    if not isinstance(processes, list):
        processes = []

    killed_pids: List[int] = []
    remaining: List[Dict[str, Any]] = []

    for item in processes:
        if not isinstance(item, dict):
            continue

        raw_pid = item.get("pid")
        try:
            pid = int(raw_pid)
        except (TypeError, ValueError):
            pid = 0

        log_path = str(item.get("log_path") or "").strip()
        pid_path = str(item.get("pid_path") or "").strip()

        was_running = _pid_is_running(pid) if pid > 0 else False
        terminated = _terminate_background_process(pid) if was_running else True

        if was_running and terminated and pid > 0:
            killed_pids.append(pid)

        for cleanup_path in [log_path, pid_path]:
            if cleanup_path:
                try:
                    os.remove(cleanup_path)
                except OSError:
                    pass

        if pid > 0 and _pid_is_running(pid):
            remaining.append(item)

    if remaining:
        meta["background_processes"] = remaining
    else:
        meta.pop("background_processes", None)
        meta.pop("detected_server", None)
        meta.pop("preview_diagnostics", None)

    return killed_pids


def delete_session(session_id: str) -> Dict[str, Any]:
    root_dir = session_dir(session_id)

    logger.warning("[delete_session] start session_id=%s root_dir=%s", session_id, root_dir)

    if not os.path.isdir(root_dir):
        logger.warning("[delete_session] missing_dir session_id=%s root_dir=%s", session_id, root_dir)
        return {"deleted": False, "session_id": session_id}

    try:
        meta = load_session_meta(session_id)
    except Exception:
        meta = {}

    try:
        _cleanup_session_background_processes(meta)
    except Exception:
        pass

    shutil.rmtree(root_dir, ignore_errors=False)
    logger.warning("[delete_session] success session_id=%s root_dir=%s", session_id, root_dir)
    return {"deleted": True, "session_id": session_id}

def _rebuild_minimal_meta_from_command_log(session_id: str) -> Dict[str, Any] | None:
    log_path = session_command_log_path(session_id)
    if not os.path.exists(log_path):
        return None

    last_status = "unknown"
    last_command = None

    try:
        with open(log_path, "r", encoding="utf-8", errors="ignore") as fh:
            lines = fh.readlines()
    except OSError:
        return None

    for line in reversed(lines):
        line = str(line).strip()
        if "status=" not in line or "command=" not in line:
            continue

        status_match = re.search(r"status=([^\s]+)", line)
        command_match = re.search(r"command=(.+)$", line)

        if status_match:
            last_status = status_match.group(1).strip()

        if command_match:
            last_command = command_match.group(1).strip()

        break

    return {
        "status": "recovered",
        "repo_dir": session_repo_dir(session_id),
        "command_count": None,
        "last_command": last_command,
        "last_command_status": last_status,
        "last_command_result": {
            "command": last_command,
            "status": last_status,
            "result_state": last_status,
            "exit_code": None,
            "next_best_commands": [],
            "next_step_suggestions": [],
            "preview_diagnostics": None,
            "execution_diagnostics": {},
            "user_guidance": {
                "summary": "Session metadata was recovered from command.log.",
                "what_worked": "",
                "what_failed": "",
                "next_commands": [],
                "required_user_actions": [],
            },
        },
        "auto_start_state": "unknown",
        "auto_start_current_command": last_command,
        "auto_start_started_at": None,
        "auto_start_updated_at": None,
        "detected_server": None,
        "execution_diagnostics": {},
    }



def get_session_status(session_id: str) -> Dict[str, Any]:
    try:
        meta = load_session_meta(session_id)
    except Exception:
        meta = None

    if not meta:
        meta = _rebuild_minimal_meta_from_command_log(session_id) or {}

    detected_server = meta.get("detected_server")
    repo_dir = meta.get("repo_dir")

    if not isinstance(detected_server, dict):
        background_processes = meta.get("background_processes")
        if not isinstance(background_processes, list):
            background_processes = []

        for item in reversed(background_processes):
            if not isinstance(item, dict):
                continue

            log_path = str(item.get("log_path") or "").strip()
            if not log_path:
                continue

            try:
                with open(log_path, "r", encoding="utf-8", errors="ignore") as fh:
                    log_text = fh.read()
            except OSError:
                continue

            candidate_server = _build_detected_server_payload(log_text)
            if not isinstance(candidate_server, dict):
                continue

            if not _is_detected_server_reachable(candidate_server):
                continue

            detected_server = candidate_server
            meta["detected_server"] = detected_server
            meta["last_activity_at"] = utc_now_iso()

            auto_start_state = str(meta.get("auto_start_state") or "").strip().lower()
            if auto_start_state == "running":
                meta["auto_start_state"] = "completed"
                meta["auto_start_updated_at"] = utc_now_iso()
                meta["auto_start_current_command"] = None
                meta["auto_start_final_reason"] = "primary_experience_ready_from_background_log"

            save_session_meta(session_id, meta)
            break

    public_url = _build_public_url(session_id, detected_server)
    preview_diagnostics = _build_preview_diagnostics(repo_dir, session_id, detected_server) if repo_dir else None

    primary_experience: Dict[str, Any] = {
        "state": "unavailable",
        "display_mode": "terminal",
        "title": "Open Terminal",
        "public_url": None,
        "launch_strategy": None,
        "fallback_available": False,
    }

    if isinstance(detected_server, dict) and public_url and _is_detected_server_reachable(detected_server):
        launch_strategy = "preview"
        detected_url = str(detected_server.get("url") or "").strip().lower()
        if " dev" in str(meta.get("last_command") or "").lower():
            launch_strategy = "dev"
        elif "start" in str(meta.get("last_command") or "").lower():
            launch_strategy = "start"
        elif "/dist" in detected_url:
            launch_strategy = "static_serve"

        primary_experience = {
            "state": "ready",
            "display_mode": "web_link",
            "title": "Open Web App",
            "public_url": public_url,
            "launch_strategy": launch_strategy,
            "fallback_available": True,
        }
    elif isinstance(preview_diagnostics, dict):
        primary_experience = {
            "state": "needs_user_action",
            "display_mode": "terminal",
            "title": "Continue in Terminal",
            "public_url": None,
            "launch_strategy": None,
            "fallback_available": False,
        }

    return {
        "session_id": session_id,
        "status": meta.get("status"),
        "repo_dir": repo_dir,
        "command_count": meta.get("command_count"),
        "last_command": meta.get("last_command"),
        "last_command_status": meta.get("last_command_status"),
        "last_command_result": meta.get("last_command_result"),
        "auto_start_state": meta.get("auto_start_state"),
        "auto_start_current_command": meta.get("auto_start_current_command"),
        "auto_start_started_at": meta.get("auto_start_started_at"),
        "auto_start_updated_at": meta.get("auto_start_updated_at"),
        "detected_server": detected_server,
        "public_url": public_url,
        "preview_diagnostics": preview_diagnostics,
        "execution_diagnostics": meta.get("execution_diagnostics") or {},
        "primary_experience": primary_experience,
    }



def _safe_session_repo_path(session_id: str, relative_path: str = "") -> str:
    repo_dir = session_repo_dir(session_id)
    if not os.path.isdir(repo_dir):
        raise FileNotFoundError("Session repository directory does not exist.")

    repo_dir_real = os.path.realpath(repo_dir)
    target_path = os.path.realpath(os.path.join(repo_dir_real, relative_path or ""))

    if target_path != repo_dir_real and not target_path.startswith(repo_dir_real + os.sep):
        raise ValueError("Requested path is outside the session repository.")

    return target_path


def list_session_files(session_id: str, relative_path: str = "") -> Dict[str, Any]:
    target_dir = _safe_session_repo_path(session_id, relative_path)

    if not os.path.exists(target_dir):
        raise FileNotFoundError("Requested path does not exist.")

    if not os.path.isdir(target_dir):
        raise NotADirectoryError("Requested path is not a directory.")

    ignored_names = {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "node_modules",
        "dist",
        "build",
        ".next",
        ".nuxt",
        ".cache",
        "__pycache__",
    }

    entries: List[Dict[str, Any]] = []
    for name in sorted(os.listdir(target_dir), key=lambda item: (not os.path.isdir(os.path.join(target_dir, item)), item.lower())):
        if name in ignored_names:
            continue

        abs_path = os.path.join(target_dir, name)
        is_dir = os.path.isdir(abs_path)

        repo_dir_real = os.path.realpath(session_repo_dir(session_id))
        rel_path = os.path.relpath(abs_path, repo_dir_real).replace("\\", "/")
        if rel_path == ".":
            rel_path = ""

        entries.append({
            "name": name,
            "path": rel_path,
            "type": "directory" if is_dir else "file",
        })

    current_rel = os.path.relpath(target_dir, os.path.realpath(session_repo_dir(session_id))).replace("\\", "/")
    if current_rel == ".":
        current_rel = ""

    parent_rel = ""
    if current_rel:
        parent_rel = os.path.dirname(current_rel).replace("\\", "/")

    return {
        "session_id": session_id,
        "current_path": current_rel,
        "parent_path": parent_rel,
        "entries": entries,
    }


def read_session_file_content(session_id: str, relative_path: str) -> Dict[str, Any]:
    if not relative_path or not str(relative_path).strip():
        raise ValueError("A file path is required.")

    file_path = _safe_session_repo_path(session_id, relative_path)

    if not os.path.exists(file_path):
        raise FileNotFoundError("Requested file does not exist.")

    if not os.path.isfile(file_path):
        raise IsADirectoryError("Requested path is not a file.")

    max_file_size = 1024 * 1024
    file_size = os.path.getsize(file_path)
    if file_size > max_file_size:
        raise ValueError("Requested file is too large to display.")

    with open(file_path, "rb") as f:
        raw = f.read()

    try:
        content = raw.decode("utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError:
        try:
            content = raw.decode("utf-8", errors="replace")
            encoding = "utf-8-replaced"
        except Exception as exc:
            raise ValueError("Requested file is not a readable text file.") from exc

    repo_dir_real = os.path.realpath(session_repo_dir(session_id))
    rel_path = os.path.relpath(file_path, repo_dir_real).replace("\\", "/")

    return {
        "session_id": session_id,
        "path": rel_path,
        "size": file_size,
        "encoding": encoding,
        "content": content,
    }


def read_session_command_log(session_id: str, max_chars: int | None = None) -> Dict[str, Any]:
    path = session_command_log_path(session_id)

    if not os.path.isfile(path):
        return {"log": ""}

    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    if isinstance(max_chars, int) and max_chars > 0:
        content = content[-max_chars:]

    return {"log": content}


# ==============================
# NEXT STEP SUGGESTION ENGINE (SMARTER)
# ==============================

def infer_next_steps(repo_dir: str) -> List[str]:
    suggestions: List[str] = []

    try:
        files = os.listdir(repo_dir)
    except Exception:
        return suggestions

    has_local_venv = os.path.isfile(os.path.join(repo_dir, ".venv", "bin", "python"))
    python_cmd = "./.venv/bin/python" if has_local_venv else "python3"
    pip_cmd = "./.venv/bin/pip" if has_local_venv else "pip"

    def _safe_load_json(path: str) -> Dict[str, Any] | None:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def _detect_package_manager(app_dir: str, pkg: Dict[str, Any] | None) -> tuple[str, str]:
        package_manager = str((pkg or {}).get("packageManager", "")).lower()

        if "pnpm" in package_manager or os.path.isfile(os.path.join(app_dir, "pnpm-lock.yaml")) or os.path.isfile(os.path.join(repo_dir, "pnpm-lock.yaml")):
            return "pnpm", "pnpm install"

        if "yarn" in package_manager or os.path.isfile(os.path.join(app_dir, "yarn.lock")) or os.path.isfile(os.path.join(repo_dir, "yarn.lock")):
            return "yarn", "yarn install"

        if os.path.isfile(os.path.join(app_dir, "package-lock.json")) or os.path.isfile(os.path.join(repo_dir, "package-lock.json")):
            return "npm", "npm ci"

        return "npm", "npm install"

    def _format_node_script_command(run_cmd: str, script_name: str) -> str:
        if run_cmd == "npm":
            return f"npm run {script_name}"
        if run_cmd == "yarn":
            return f"yarn {script_name}"
        return f"pnpm {script_name}"

    def _with_prefix(app_dir: str, command: str) -> str:
        rel_dir = os.path.relpath(app_dir, repo_dir)
        if rel_dir == ".":
            return command
        return f"cd {rel_dir} && {command}"

    def _collect_node_candidates() -> List[Dict[str, Any]]:
        candidates: List[Dict[str, Any]] = []
        seen: set[str] = set()

        preferred_dirs = [
            repo_dir,
            os.path.join(repo_dir, "homepage"),
            os.path.join(repo_dir, "app"),
            os.path.join(repo_dir, "apps", "web"),
            os.path.join(repo_dir, "apps", "app"),
            os.path.join(repo_dir, "web"),
            os.path.join(repo_dir, "frontend"),
            os.path.join(repo_dir, "client"),
            os.path.join(repo_dir, "site"),
            os.path.join(repo_dir, "dashboard"),
        ]

        for app_dir in preferred_dirs:
            pkg_path = os.path.join(app_dir, "package.json")
            if not os.path.isfile(pkg_path):
                continue

            norm_dir = os.path.normpath(app_dir)
            if norm_dir in seen:
                continue
            seen.add(norm_dir)

            pkg = _safe_load_json(pkg_path) or {}
            scripts = pkg.get("scripts", {})
            if not isinstance(scripts, dict):
                scripts = {}

            score = 0
            if norm_dir != os.path.normpath(repo_dir):
                score += 5
            base_name = os.path.basename(norm_dir).lower()
            if base_name in {"homepage", "web", "frontend", "client", "site", "app"}:
                score += 3
            if "preview" in scripts:
                score += 5
            if "dev" in scripts:
                score += 4
            if "start" in scripts:
                score += 3
            if "build" in scripts:
                score += 2

            candidates.append(
                {
                    "dir": norm_dir,
                    "pkg": pkg,
                    "scripts": scripts,
                    "score": score,
                }
            )

        if not candidates and "package.json" in files:
            pkg = _safe_load_json(os.path.join(repo_dir, "package.json")) or {}
            scripts = pkg.get("scripts", {})
            if not isinstance(scripts, dict):
                scripts = {}
            candidates.append(
                {
                    "dir": os.path.normpath(repo_dir),
                    "pkg": pkg,
                    "scripts": scripts,
                    "score": 0,
                }
            )

        candidates.sort(key=lambda item: item["score"], reverse=True)
        return candidates

    node_candidates = _collect_node_candidates()
    if node_candidates:
        best = node_candidates[0]
        app_dir = best["dir"]
        scripts = best["scripts"]
        run_cmd, install_cmd = _detect_package_manager(app_dir, best["pkg"])

        if isinstance(scripts, dict):
            if "dev" in scripts:
                suggestions.append(_with_prefix(app_dir, _format_node_script_command(run_cmd, "dev")))
            if "preview" in scripts:
                suggestions.append(_with_prefix(app_dir, _format_node_script_command(run_cmd, "preview")))
            if "start" in scripts:
                suggestions.append(_with_prefix(app_dir, _format_node_script_command(run_cmd, "start")))
            if "build" in scripts:
                suggestions.append(_with_prefix(app_dir, _format_node_script_command(run_cmd, "build")))

        if not suggestions:
            suggestions.append(_with_prefix(app_dir, install_cmd))

    if "requirements.txt" in files:
        suggestions.append(f"{pip_cmd} install -r requirements.txt")

    if "pyproject.toml" in files:
        suggestions.append(f"{pip_cmd} install -e .")

    for item in files:
        item_path = os.path.join(repo_dir, item)

        if os.path.isdir(item_path):
            main_file = os.path.join(item_path, "__main__.py")
            if os.path.isfile(main_file):
                suggestions.append(f"{python_cmd} -m {item}")

                if item == "researchclaw":
                    suggestions.append(f"{python_cmd} -m researchclaw --help")
                    suggestions.append(f"{python_cmd} -m researchclaw doctor")

    if "main.py" in files:
        suggestions.append(f"{python_cmd} main.py")

    if "app.py" in files:
        suggestions.append(f"{python_cmd} app.py")

    clean: List[str] = []
    for s in suggestions:
        if s not in clean:
            clean.append(s)

    return clean


def refine_next_steps_for_command(
    command: str,
    suggestions: List[str],
    result_returncode: int,
    auto_setup_logs: List[Dict[str, Any]] | None = None,
) -> List[str]:
    clean: List[str] = []
    seen: set[str] = set()

    def _normalize(cmd: str) -> str:
        return " ".join(str(cmd or "").strip().lower().split())

    normalized_command = _normalize(command)

    for suggestion in suggestions:
        value = str(suggestion).strip()
        normalized_value = _normalize(value)
        if not value or normalized_value in seen:
            continue
        seen.add(normalized_value)
        clean.append(value)

    if result_returncode != 0:
        return clean

    successful_commands: set[str] = set()
    if normalized_command:
        successful_commands.add(normalized_command)

    if isinstance(auto_setup_logs, list):
        for item in auto_setup_logs:
            if not isinstance(item, dict):
                continue
            if str(item.get("status") or "").strip().lower() != "success":
                continue
            step = _normalize(str(item.get("step") or ""))
            if step:
                successful_commands.add(step)

    def _matches_script(cmd: str, script_name: str) -> bool:
        cmd = _normalize(cmd)
        return (
            f" run {script_name}" in cmd
            or cmd.endswith(f" {script_name}")
            or f"pnpm {script_name}" in cmd
            or f"npm run {script_name}" in cmd
            or f"yarn {script_name}" in cmd
        )

    def _is_python_install_command(cmd: str) -> bool:
        normalized = _normalize(cmd)
        python_install_markers = [
            "pip install -e .",
            "python -m pip install -e .",
            "python3 -m pip install -e .",
            "./.venv/bin/pip install -e .",
            "./.venv/bin/python -m pip install -e .",
        ]
        return any(marker in normalized for marker in python_install_markers)

    prioritized: List[str] = []
    remaining = list(clean)

    def _pull(predicate) -> None:
        nonlocal remaining
        matched = [item for item in remaining if predicate(item)]
        remaining = [item for item in remaining if not predicate(item)]
        prioritized.extend(matched)

    if any(_is_python_install_command(cmd) for cmd in successful_commands):
        remaining = [item for item in remaining if not _is_python_install_command(item)]
    elif (
        _matches_script(normalized_command, "install")
        or normalized_command.startswith("npm ci")
        or normalized_command.startswith("npm install")
        or normalized_command.startswith("pnpm install")
        or normalized_command.startswith("yarn install")
    ):
        _pull(lambda item: _matches_script(item, "build"))
        _pull(lambda item: _matches_script(item, "preview"))
        _pull(lambda item: _matches_script(item, "dev"))
        _pull(lambda item: _matches_script(item, "start"))
    elif _matches_script(normalized_command, "build"):
        remaining = [item for item in remaining if not _matches_script(item, "build")]
        _pull(lambda item: _matches_script(item, "preview"))
        _pull(lambda item: _matches_script(item, "start"))
        _pull(lambda item: _matches_script(item, "dev"))
    elif (
        _matches_script(normalized_command, "preview")
        or _matches_script(normalized_command, "start")
        or _matches_script(normalized_command, "dev")
    ):
        remaining = [
            item for item in remaining
            if not (
                _matches_script(item, "preview")
                or _matches_script(item, "start")
                or _matches_script(item, "dev")
            )
        ]
        _pull(lambda item: _matches_script(item, "build"))

    result = prioritized + remaining

    final: List[str] = []
    final_seen: set[str] = set()
    for item in result:
        normalized_item = _normalize(item)
        if normalized_item not in final_seen:
            final_seen.add(normalized_item)
            final.append(item)

    return final



def should_apply_auto_fix(category: str, fix_commands: list, fix_confidence: str) -> tuple[bool, str]:
    if not fix_commands:
        return False, "no_fix_commands"

    if fix_confidence not in ["high", "medium"]:
        return False, "low_confidence"

    no_retry_categories = {
        "node_version_mismatch": "node_version_change_required",
        "python_version_mismatch": "python_version_change_required",
        "missing_config_file": "manual_setup_required",
        "llm_connectivity_mismatch": "external_service_required",
        "git_access_error": "repository_access_issue",
    }

    if category in no_retry_categories:
        return False, no_retry_categories[category]

    return True, ""


def is_safe_auto_fix_command(command: str) -> bool:
    command = str(command or "").strip()

    if not command:
        return False

    blocked_tokens = [
        "&&",
        "||",
        ";",
        "|",
        "`",
        "$(",
        "\n",
    ]

    for token in blocked_tokens:
        if token in command:
            return False

    return True


# ==============================
# RUN COMMAND
# ==============================
def run_session_command(
    session_id: str,
    command: str,
    dangerous_patterns: List[str],
    interactive_patterns: List[str],
    timeout_seconds: int | None = None,
    max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
) -> Dict[str, Any]:

    command = str(command or "").strip()

    normalized_command = command.lower()
    normalized_command = normalized_command.replace("python -m", "python3 -m")

    if command.startswith("python -m"):
        command = command.replace("python -m", "python3 -m", 1)

    meta = load_session_meta(session_id)
    repo_dir = meta["repo_dir"]
    repo_analysis = meta.get("repo_analysis") or {}
    repo_decision = meta.get("repo_decision") or {}

    provided_env_vars = meta.get("provided_env_vars") or meta.get("env") or {}
    execution_env = os.environ.copy()
    if isinstance(provided_env_vars, dict):
        for key, value in provided_env_vars.items():
            key_str = str(key or "").strip()
            if not key_str:
                continue
            execution_env[key_str] = str(value or "")

    venv_bin_dir = os.path.join(repo_dir, ".venv", "bin")
    if os.path.isdir(venv_bin_dir):
        current_path = execution_env.get("PATH", "")
        execution_env["PATH"] = (
            f"{venv_bin_dir}:{current_path}"
            if current_path
            else venv_bin_dir
        )

    extra_bin_candidates = [
        "/usr/local/go/bin",
        "/root/.bun/bin",
        os.path.expanduser("~/.bun/bin"),
    ]

    for extra_bin_dir in extra_bin_candidates:
        if not os.path.isdir(extra_bin_dir):
            continue

        current_path = execution_env.get("PATH", "")
        path_parts = current_path.split(":") if current_path else []

        if extra_bin_dir not in path_parts:
            execution_env["PATH"] = (
                f"{extra_bin_dir}:{current_path}"
                if current_path
                else extra_bin_dir
            )
        break


    try:
        setup_steps = meta.get("effective_setup_steps") or []
        setup_executed = meta.get("setup_steps_executed") or []

        if setup_steps and not setup_executed:
            for step in setup_steps:
                subprocess.run(
                    ["bash", "-lc", step],
                    cwd=repo_dir,
                    capture_output=True,
                    text=True,
                    timeout=timeout_seconds or 600,
                    env=execution_env,
                )

            meta["setup_steps_executed"] = setup_steps
            save_session_meta(session_id, meta)

    except Exception:
        pass


    try:
        import json
        import re

        selected_node_version = "20"
        runtime_selection_reason = "default_system_node20"
        package_json_candidates = [
            os.path.join(repo_dir, "package.json"),
            os.path.join(repo_dir, "homepage", "package.json"),
            os.path.join(repo_dir, "understand-anything-plugin", "package.json"),
        ]

        discovered_node_requirements = []

        for package_json_path in package_json_candidates:
            if not os.path.exists(package_json_path):
                continue

            with open(package_json_path, "r", encoding="utf-8") as f:
                pkg = json.load(f)

            engines = pkg.get("engines") or {}
            candidate_version = str(engines.get("node") or "").strip()

            if not candidate_version:
                continue

            discovered_node_requirements.append(
                {
                    "path": package_json_path,
                    "requirement": candidate_version,
                }
            )

        def _extract_node_major_candidates(requirement_text: str) -> List[int]:
            normalized = str(requirement_text or "").strip().lower()
            if not normalized:
                return []

            found_numbers = re.findall(r"\d+(?:\.\d+){0,2}", normalized)
            majors: List[int] = []

            for token in found_numbers:
                try:
                    major = int(token.split(".")[0])
                except Exception:
                    continue
                if major not in majors:
                    majors.append(major)

            return majors

        def _choose_supported_node_version(requirement_text: str):
            majors = _extract_node_major_candidates(requirement_text)
            supported_versions = ["22", "20", "18"]

            for supported in supported_versions:
                if int(supported) in majors:
                    return supported

            normalized = str(requirement_text or "").replace(" ", "").lower()

            if normalized.startswith(">="):
                for supported in supported_versions:
                    try:
                        if int(supported) >= int(normalized[2:].split(".")[0]):
                            return supported
                    except Exception:
                        continue

            return None

        for item in discovered_node_requirements:
            requirement_text = item["requirement"]
            matched_version = _choose_supported_node_version(requirement_text)

            if matched_version:
                selected_node_version = matched_version
                runtime_selection_reason = (
                    f"package.json engine '{requirement_text}' selected Node "
                    f"{matched_version} ({item['path']})"
                )

                if matched_version == "22":
                    break

        if selected_node_version == "22":
            selected_node_bin_path = "/opt/nodejs/node22/bin"
        elif selected_node_version == "18":
            selected_node_bin_path = "/opt/nodejs/node18/bin"
        else:
            selected_node_bin_path = "/usr/bin"

        current_path = execution_env.get("PATH", "")
        path_parts = current_path.split(":") if current_path else []

        if selected_node_bin_path != "/usr/bin" and selected_node_bin_path not in path_parts:
            execution_env["PATH"] = (
                f"{selected_node_bin_path}:{current_path}"
                if current_path
                else selected_node_bin_path
            )

        execution_env["NODE_VERSION_SELECTED"] = selected_node_version
        execution_env["RUNTIME_SELECTION_REASON"] = runtime_selection_reason

    except Exception:
        execution_env["NODE_VERSION_SELECTED"] = execution_env.get(
            "NODE_VERSION_SELECTED",
            "20",
        )
        execution_env["RUNTIME_SELECTION_REASON"] = execution_env.get(
            "RUNTIME_SELECTION_REASON",
            "node_runtime_selection_failed_fallback",
        )

    timeout_metadata = build_timeout_metadata(
        command=command,
        repo_context={
            "detected_language": repo_analysis.get("detected_language"),
            "repo_type_guess": repo_decision.get("repo_type_guess"),
            "package_managers": repo_analysis.get("package_managers_detected") or [],
            "has_monorepo_signals": bool((repo_analysis.get("workspace_signals") or {}).get("is_workspace")),
        },
        user_requested_timeout=timeout_seconds,
    )

    effective_timeout = int(timeout_metadata["timeout_seconds"])

    def _detect_server_urls(text: str) -> List[str]:
        if not text:
            return []

        matches = re.findall(
            r"https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0|(?:\d{1,3}\.){3}\d{1,3}):\d+(?:/[^\s\"'<>]*)?",
            text,
            flags=re.IGNORECASE,
        )

        seen: List[str] = []
        for match in matches:
            normalized = match.rstrip(".,);]}>")
            if normalized not in seen:
                seen.append(normalized)
        return seen

    def _build_detected_server_payload(text: str):
        urls = _detect_server_urls(text)
        if urls:
            primary_url = urls[0]
            parsed = urlparse(primary_url)

            try:
                port = int(parsed.port) if parsed.port else None
            except (TypeError, ValueError):
                port = None

            return {
                "url": primary_url,
                "host": parsed.hostname or "",
                "port": port,
                "all_urls": urls,
            }

        http_server_match = re.search(
            r"Serving HTTP on\s+([^\s]+)\s+port\s+(\d+)",
            text,
            flags=re.IGNORECASE,
        )
        if http_server_match:
            detected_host = str(http_server_match.group(1) or "").strip()
            detected_port_raw = http_server_match.group(2)

            try:
                detected_port = int(detected_port_raw)
            except (TypeError, ValueError):
                detected_port = None

            public_host = detected_host
            if public_host in {"0.0.0.0", "::", "[::]", ""}:
                public_host = "127.0.0.1"

            if detected_port:
                normalized_url = f"http://{public_host}:{detected_port}"
            else:
                normalized_url = f"http://{public_host}"

            return {
                "url": normalized_url,
                "host": public_host,
                "port": detected_port,
                "all_urls": [normalized_url],
            }

        return None

    def _looks_like_background_command(cmd: str) -> bool:
        stripped = str(cmd or "").strip()
        if not stripped:
            return False
        if re.search(r"(^|\s)(nohup|setsid)\b", stripped):
            return True
        if re.search(r"&\s*$", stripped):
            return True
        return False

    def _looks_like_server_command(cmd: str) -> bool:
        lowered = str(cmd or "").strip().lower()
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
            "bun dev",
            "bun preview",
            "bun start",
            "bun run dev",
            "bun run preview",
            "bun run start",
            "vite dev",
            "vite preview",
            "next dev",
            "astro dev",
            "uvicorn ",
            "gunicorn ",
            "flask run",
            "streamlit run",
            "streamlit hello",
            "python -m http.server",
            "python3 -m http.server",
        ]
        return any(marker in lowered for marker in server_markers)


    def _execute_command(command_text: str, allow_background_server: bool = True):
        should_background = allow_background_server and (
            _looks_like_background_command(command_text) or _looks_like_server_command(command_text)
        )
        is_server_command = _looks_like_server_command(command_text)

        def _extract_explicit_server_port(cmd: str) -> int | None:
            stripped = str(cmd or "").strip()

            http_server_match = re.search(
                r"\bpython(?:3)?\s+-m\s+http\.server\s+(\d+)\b",
                stripped,
                flags=re.IGNORECASE,
            )
            if http_server_match:
                try:
                    return int(http_server_match.group(1))
                except (TypeError, ValueError):
                    return None

            common_port_patterns = [
                r"(?:^|\s)--port(?:=|\s+)(\d+)\b",
                r"(?:^|\s)-p\s+(\d+)\b",
                r"\bPORT=(\d+)\b",
                r"\bport\s+(\d+)\b",
            ]
            for pattern in common_port_patterns:
                match = re.search(pattern, stripped, flags=re.IGNORECASE)
                if match:
                    try:
                        return int(match.group(1))
                    except (TypeError, ValueError):
                        return None

            return None

        explicit_server_port = _extract_explicit_server_port(command_text)

        if not should_background:
            return subprocess.run(
                ["bash", "-lc", command_text],
                cwd=repo_dir,
                capture_output=True,
                text=True,
                timeout=effective_timeout,
                env=execution_env,
            )

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
            save_session_meta(session_id, meta)

            running_commands = []
            for item in active_background_processes[:5]:
                command_summary = str(item.get("command") or "").strip()
                if command_summary:
                    running_commands.append(f"- {command_summary}")

            running_commands_text = "\n".join(running_commands) if running_commands else "- Existing background process"

            return subprocess.CompletedProcess(
                args=["bash", "-lc", command_text],
                returncode=98,
                stdout="",
                stderr=(
                    "Another background process is already running in this session.\n"
                    "Start a new session or stop the existing background process before launching another long-running command.\n"
                    "Running background commands:\n"
                    f"{running_commands_text}"
                ),
            )

        meta.pop("background_processes", None)

        launch_id = uuid.uuid4().hex[:8]

        background_log_path = f"/tmp/tekarab-bg-{session_id}-{launch_id}.log"
        background_pid_path = f"/tmp/tekarab-bg-{session_id}-{launch_id}.pid"

        with open(background_log_path, "w", encoding="utf-8") as log_fh:
            process = subprocess.Popen(
                ["bash", "-lc", command_text],
                cwd=repo_dir,
                stdout=log_fh,
                stderr=log_fh,
                stdin=subprocess.DEVNULL,
                text=True,
                env=execution_env,
                start_new_session=True,
            )

        try:
            with open(background_pid_path, "w", encoding="utf-8") as fh:
                fh.write(str(process.pid))
        except OSError:
            pass

        background_processes = meta.get("background_processes")
        if not isinstance(background_processes, list):
            background_processes = []

        background_processes.append(
            {
                "pid": process.pid,
                "launch_id": launch_id,
                "command": command_text,
                "log_path": background_log_path,
                "pid_path": background_pid_path,
                "started_at": utc_now_iso(),
            }
        )
        meta["background_processes"] = background_processes
        save_session_meta(session_id, meta)

        def _read_startup_log() -> str:
            try:
                with open(background_log_path, "r", encoding="utf-8", errors="ignore") as fh:
                    return fh.read()
            except OSError:
                return ""

        def _remove_current_background_process() -> None:
            latest_meta = load_session_meta(session_id) or {}
            latest_background_processes = latest_meta.get("background_processes")
            if not isinstance(latest_background_processes, list):
                latest_background_processes = []

            remaining_background_processes = []
            for item in latest_background_processes:
                if not isinstance(item, dict):
                    continue

                raw_pid = item.get("pid")
                try:
                    pid = int(raw_pid)
                except (TypeError, ValueError):
                    pid = 0

                if pid != process.pid:
                    remaining_background_processes.append(item)

            if remaining_background_processes:
                latest_meta["background_processes"] = remaining_background_processes
            else:
                latest_meta.pop("background_processes", None)

            save_session_meta(session_id, latest_meta)

        startup_log_text = ""
        detected_server = None
        server_verified = False
        process_exited_code = None

        for _ in range(6):
            time.sleep(1)
            startup_log_text = _read_startup_log()

            poll_code = process.poll()
            if poll_code is not None:
                process_exited_code = poll_code
                break

            detected_server = _build_detected_server_payload(startup_log_text)
            if detected_server:
                for _ in range(4):
                    if process.poll() is not None:
                        process_exited_code = process.poll()
                        break

                    if _is_detected_server_reachable(detected_server, timeout_seconds=0.75):
                        server_verified = True
                        break

                    time.sleep(0.5)

                if server_verified or process_exited_code is not None:
                    break

        if (
            not server_verified
            and process.poll() is None
            and is_server_command
            and explicit_server_port is not None
        ):
            explicit_detected_server = {
                "url": f"http://127.0.0.1:{explicit_server_port}",
                "host": "127.0.0.1",
                "port": explicit_server_port,
                "all_urls": [f"http://127.0.0.1:{explicit_server_port}"],
            }

            for _ in range(4):
                if process.poll() is not None:
                    process_exited_code = process.poll()
                    break

                if _is_detected_server_reachable(explicit_detected_server, timeout_seconds=0.75):
                    detected_server = explicit_detected_server
                    server_verified = True
                    break

                time.sleep(0.5)

            if not server_verified:
                startup_log_text = (
                    startup_log_text.rstrip() + "\n" if startup_log_text.strip() else ""
                )
                startup_log_text += f"Explicit server URL: {explicit_detected_server['url']}\n"

        if (
            not server_verified
            and not detected_server
            and process.poll() is None
            and is_server_command
            and explicit_server_port is None
        ):
            port_value = str(execution_env.get("PORT") or "").strip()
            if port_value.isdigit():
                synthetic_detected_server = {
                    "url": f"http://127.0.0.1:{port_value}",
                    "host": "127.0.0.1",
                    "port": int(port_value),
                    "all_urls": [f"http://127.0.0.1:{port_value}"],
                }

                for _ in range(4):
                    if process.poll() is not None:
                        process_exited_code = process.poll()
                        break

                    if _is_detected_server_reachable(synthetic_detected_server, timeout_seconds=0.75):
                        detected_server = synthetic_detected_server
                        server_verified = True
                        break

                    time.sleep(0.5)

                if not server_verified:
                    startup_log_text = (
                        startup_log_text.rstrip() + "\n" if startup_log_text.strip() else ""
                    )
                    startup_log_text += f"Synthetic server URL: {synthetic_detected_server['url']}\n"

        final_poll_code = process.poll()
        if final_poll_code is not None:
            process_exited_code = final_poll_code

        if process_exited_code is not None:
            _remove_current_background_process()
            return subprocess.CompletedProcess(
                args=["bash", "-lc", command_text],
                returncode=process_exited_code,
                stdout=startup_log_text,
                stderr="",
            )

        if is_server_command and not server_verified:
            _remove_current_background_process()
            failure_output_lines = [
                f"Background process started with PID {process.pid}, but the server did not become reachable.",
                f"Log file: {background_log_path}",
            ]
            if startup_log_text.strip():
                failure_output_lines.append("----- STARTUP LOG -----")
                failure_output_lines.append(startup_log_text.strip())

            return subprocess.CompletedProcess(
                args=["bash", "-lc", command_text],
                returncode=97,
                stdout="\n".join(failure_output_lines).strip() + "\n",
                stderr="",
            )

        summary_lines = [
            f"Background process started with PID {process.pid}.",
            f"Log file: {background_log_path}",
        ]
        if isinstance(detected_server, dict):
            detected_url = str(detected_server.get("url") or "").strip()
            if detected_url:
                summary_lines.append(f"Detected server URL: {detected_url}")

        if startup_log_text.strip():
            summary_lines.append("----- STARTUP LOG -----")
            summary_lines.append(startup_log_text.strip())

        return subprocess.CompletedProcess(
            args=["bash", "-lc", command_text],
            returncode=0,
            stdout="\n".join(summary_lines).strip() + "\n",
            stderr="",
        )


    auto_setup_logs = run_auto_setup_if_needed(
        repo_dir=repo_dir,
        command=command,
        env=execution_env,
    )

    venv_python = os.path.join(repo_dir, ".venv", "bin", "python")
    if os.path.exists(venv_python):
        if command.startswith("python "):
            command = command.replace("python ", "./.venv/bin/python ", 1)
        elif command.startswith("python3 "):
            command = command.replace("python3 ", "./.venv/bin/python ", 1)

    latest_meta = load_session_meta(session_id) or {}
    latest_meta["last_activity_at"] = utc_now_iso()
    latest_meta["last_command"] = command
    latest_meta["last_command_status"] = "running"
    latest_meta["last_command_result"] = {
        "status": "running",
        "result_state": "running",
        "exit_code": None,
        "command": command,
        "next_best_commands": [],
        "next_step_suggestions": [],
        "user_guidance": {
            "summary": f'Command "{command}" is currently running.',
            "what_worked": "",
            "what_failed": "",
            "next_commands": [],
            "required_user_actions": [],
        },
        "detected_server": None,
        "preview_diagnostics": None,
        "execution_diagnostics": {},
    }
    save_session_meta(session_id, latest_meta)

    _append_text(
        session_command_log_path(session_id),
        f"[{utc_now_iso()}] status=running command={command}\n",
    )

    result = _execute_command(command)

    smart_error_hint = build_smart_error_hint(
        command=command,
        exit_code=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
    )
    install_failure = categorize_install_failure(
        command=command,
        stdout=result.stdout,
        stderr=result.stderr,
        analysis=repo_analysis,
    )

    next_best_commands = []
    if isinstance(smart_error_hint, dict):
        auto_fix = smart_error_hint.get("auto_fix")
        if isinstance(auto_fix, dict):
            cmds = auto_fix.get("commands")
            if isinstance(cmds, list):
                next_best_commands = [str(c) for c in cmds if str(c).strip()]
    if isinstance(install_failure, dict):
        for cmd in install_failure.get("fallback_candidates") or []:
            cmd_text = str(cmd).strip()
            if cmd_text and cmd_text not in next_best_commands:
                next_best_commands.append(cmd_text)

    next_step_suggestions: List[str] = refine_next_steps_for_command(
        command,
        infer_next_steps(repo_dir),
        result.returncode,
        auto_setup_logs=auto_setup_logs,
    )

    if result.returncode == 0:
        next_best_commands = []

    logical_failure = False

    combined_output = (result.stdout or "") + "\n" + (result.stderr or "")
    lower_output = combined_output.lower()

    fatal_failure_signals = [
        "traceback (most recent call last):",
        "command not found",
        "no such file or directory",
        "cannot find module",
        "syntaxerror:",
        "module not found",
        "spawn enoent",
    ]

    failure_signals = list(fatal_failure_signals) + [
        "error:",
        "failed",
        "not supported",
        "unsupported",
        "requires node",
    ]

    if result.returncode == 0:
        for signal in fatal_failure_signals:
            if signal in lower_output:
                logical_failure = True
                break

    server_start_signals = [
        "vite v",
        "ready in",
        "local:",
        "network:",
        "running on http",
        "listening on",
        "background process started with pid",
        "serving http on",
        "servering http on",
        "http.server",
        "port 8080",
    ]

    background_start_signals = [
        "background process started with pid",
        "log file: /tmp/tekarab-bg-",
    ]

    background_started = any(signal in lower_output for signal in background_start_signals)
    server_started = any(signal in lower_output for signal in server_start_signals)

    if (
        not server_started
        and _looks_like_server_command(command)
        and result.returncode != 0
        and "address already in use" in lower_output
    ):
        server_started = True
        logical_failure = False
        result.returncode = 0

    if background_started and result.returncode == 0 and not logical_failure:
        result_state = "running"
    elif (result.returncode == 0 and not logical_failure) or (
        result.returncode == 124 and server_started
    ):
        result_state = "success"
    else:
        result_state = "failed"

    auto_fix_applied = False
    auto_fix_skip_reason = ""
    fix_commands = []
    fix_confidence = ""
    category = ""

    if result.returncode != 0 and isinstance(smart_error_hint, dict):
        auto_fix = smart_error_hint.get("auto_fix") or {}
        fix_commands = auto_fix.get("commands") or []
        fix_confidence = str(auto_fix.get("confidence") or "").strip().lower()
        category = str(smart_error_hint.get("category") or "").strip()
    elif result.returncode != 0 and isinstance(install_failure, dict):
        fix_commands = [str(cmd).strip() for cmd in install_failure.get("fallback_candidates") or [] if str(cmd).strip()]
        fix_confidence = str(install_failure.get("confidence") or "medium").strip().lower()
        category = str(install_failure.get("category") or "").strip()

    if not fix_commands:
        auto_fix_skip_reason = "no_fix_commands"
    else:
        can_apply_auto_fix, auto_fix_policy_reason = should_apply_auto_fix(
            category,
            fix_commands,
            fix_confidence,
        )
        if not can_apply_auto_fix:
            auto_fix_skip_reason = auto_fix_policy_reason or "policy_blocked"
        else:
            best_command = str(fix_commands[0]).strip()

            if not best_command:
                auto_fix_skip_reason = "empty_fix_command"
            elif not is_safe_auto_fix_command(best_command):
                auto_fix_skip_reason = "unsafe_fix_command"
            else:
                try:
                    retry_result = _execute_command(best_command, allow_background_server=False)

                    auto_fix_applied = True

                    if retry_result.returncode == 0:
                        rerun_result = _execute_command(command)

                        result = rerun_result
                        smart_error_hint = build_smart_error_hint(
                            command=command,
                            exit_code=result.returncode,
                            stdout=result.stdout,
                            stderr=result.stderr,
                        )

                        next_best_commands = []
                        if isinstance(smart_error_hint, dict):
                            auto_fix = smart_error_hint.get("auto_fix")
                            if isinstance(auto_fix, dict):
                                cmds = auto_fix.get("commands")
                                if isinstance(cmds, list):
                                    next_best_commands = [str(c) for c in cmds if str(c).strip()]

                        next_step_suggestions = refine_next_steps_for_command(
                            command,
                            infer_next_steps(repo_dir),
                            result.returncode,
                            auto_setup_logs=auto_setup_logs,
                        )

                        if result.returncode == 0:
                            next_best_commands = []

                        combined_output = (result.stdout or "") + "\n" + (result.stderr or "")
                        logical_failure = False

                        for signal in failure_signals:
                            if signal in combined_output.lower():
                                logical_failure = True
                                break

                        lower_output = combined_output.lower()
                        server_started = any(signal in lower_output for signal in server_start_signals)

                        if (result.returncode == 0 and not logical_failure) or (
                            result.returncode == 124 and server_started
                        ):
                            result_state = "success"
                        else:
                            result_state = "failed"

                except Exception:
                    pass

    if result.returncode == 0 and not logical_failure and not isinstance(smart_error_hint, dict):
        result_state = "success"

    if result_state == "failed" and isinstance(smart_error_hint, dict):
        category = str(smart_error_hint.get("category") or "").strip()

        if category in [
            "missing_python_dependency",
            "missing_node_dependency",
            "missing_command",
            "missing_config_file",
            "llm_connectivity_mismatch",
            "node_version_mismatch",
            "python_version_mismatch",
            "git_access_error",
        ]:
            result_state = "needs_user_action"

    if result_state == "success":
        smart_error_hint = None

    if result_state == "success":
        user_guidance = {
            "what_worked": f'The command "{command}" executed successfully.',
            "what_failed": "",
            "required_user_actions": [],
            "next_commands": next_step_suggestions,
            "summary": "Command executed successfully. You can continue with one of the suggested next steps.",
        }

    elif result_state == "needs_user_action":
        required_user_actions = []
        hint_summary = ""

        if isinstance(smart_error_hint, dict):
            category = str(smart_error_hint.get("category") or "").strip()
            title_text = str(smart_error_hint.get("title") or "").strip()
            hint_text = str(smart_error_hint.get("hint") or "").strip()
            details_text = str(smart_error_hint.get("details") or "").strip()

            action_map = {
                "missing_python_dependency": [
                    "Install the missing Python dependency inside the session virtual environment.",
                ],
                "missing_node_dependency": [
                    "Install the missing Node.js dependency in the repository before retrying.",
                ],
                "missing_command": [
                    "Install the missing command or use an alternative command that exists in the session.",
                ],
                "missing_config_file": [
                    "Create or copy the required configuration file before retrying.",
                ],
                "llm_connectivity_mismatch": [
                    "Provide a working API key or correct the LLM provider/base URL configuration.",
                ],
                "node_version_mismatch": [
                    "Run the repository with a compatible Node.js version.",
                ],
                "python_version_mismatch": [
                    "Run the repository with a compatible Python version.",
                ],
                "git_access_error": [
                    "Verify that the repository URL is reachable and that required access is available.",
                ],
            }

            command_map = {
                "missing_python_dependency": [
                    "pwd",
                    "ls -la",
                    "./.venv/bin/pip install -e .",
                ],
                "missing_node_dependency": [
                    "pwd",
                    "ls -la",
                    "pnpm install",
                    "npm install",
                    "yarn install",
                ],
                "missing_command": [
                    "pwd",
                    "ls -la",
                ],
                "missing_config_file": [
                    "pwd",
                    "find . -maxdepth 3 -type f | sed -n '1,120p'",
                ],
                "python_version_mismatch": [
                    "python3 --version",
                    "python3.11 --version",
                    "find . -maxdepth 2 \\( -name pyproject.toml -o -name setup.py -o -name setup.cfg \\) -print",
                    "cat pyproject.toml",
                    "python3.11 -m venv .venv",
                    "./.venv/bin/python --version",
                ],
                "node_version_mismatch": [
                    "node -v",
                    "find . -maxdepth 3 -name package.json -print",
                    "cat package.json",
                ],
                "git_access_error": [
                    "pwd",
                    "git remote -v",
                ],
            }

            required_user_actions.extend(action_map.get(category, []))

            mapped_commands = command_map.get(category, []).copy()

            for cmd in mapped_commands:
                if cmd not in next_best_commands:
                    next_best_commands.append(cmd)

            if hint_text and hint_text not in required_user_actions:
                required_user_actions.append(hint_text)
            if details_text and details_text not in required_user_actions:
                required_user_actions.append(details_text)

            hint_summary = title_text or hint_text or details_text

        user_guidance = {
            "what_worked": "Dependencies were installed and the build process started successfully, but further progress is blocked by an environment mismatch.",
            "what_failed": f'The command "{command}" could not complete without user action.',
            "required_user_actions": required_user_actions,
            "next_commands": next_best_commands,
            "summary": hint_summary or "This command needs user action before it can succeed.",
        }

    else:
        failure_summary = truncate_output(result.stderr or result.stdout, 500)
        user_guidance = {
            "what_worked": "",
            "what_failed": f'The command "{command}" failed with exit code {result.returncode}.',
            "required_user_actions": [],
            "next_commands": next_best_commands,
            "summary": failure_summary or "Command failed. Review the error output and suggested fixes.",
        }

    combined_output_text = "\n".join(part for part in [result.stdout, result.stderr] if part).strip()
    detected_server = _build_detected_server_payload(combined_output_text)

    if not detected_server:
        redirected_log_match = re.search(r'Log file:\s*(/tmp/[^\s]+)', combined_output_text)
        if not redirected_log_match:
            redirected_log_match = re.search(r'>\s*(/tmp/[^\s]+)', command)

        if redirected_log_match:
            redirected_log_path = redirected_log_match.group(1)
            for _ in range(5):
                try:
                    with open(redirected_log_path, "r", encoding="utf-8", errors="ignore") as fh:
                        redirected_log_text = fh.read()
                    detected_server = _build_detected_server_payload(redirected_log_text)
                    if detected_server:
                        break
                except OSError:
                    pass
                time.sleep(1)

    preview_diagnostics = _build_preview_diagnostics(repo_dir, session_id, detected_server)
    strategy_preview_diagnostics = classify_preview_diagnostics(
        command=command,
        analysis=repo_analysis,
        subpath_preview_risk=preview_diagnostics,
    )
    execution_diagnostics = {
        "install_failure_category": str((install_failure or {}).get("category") or ""),
        "fallback_candidates": list((install_failure or {}).get("fallback_candidates") or []),
        "preview_diagnostics": strategy_preview_diagnostics,
        "dev_preview_risk": (strategy_preview_diagnostics.get("dev_preview_risk") or {}),
        "subpath_preview_risk": (strategy_preview_diagnostics.get("subpath_preview_risk") or {}),
        "degraded_preview_mode": strategy_preview_diagnostics.get("degraded_preview_mode"),
    }
    if isinstance(preview_diagnostics, dict):
        preview_message = str(preview_diagnostics.get("message") or "").strip()
        if preview_message:
            warning_step = (
                "Preview warning: this web app uses root-relative links or assets, "
                "so images/routes may break under the session subpath preview."
            )
            if warning_step not in next_step_suggestions:
                next_step_suggestions.append(warning_step)

            required_actions = user_guidance.get("required_user_actions")
            if isinstance(required_actions, list) and preview_message not in required_actions:
                required_actions.append(preview_message)

            evidence = preview_diagnostics.get("evidence") or []
            if isinstance(evidence, list):
                for item in evidence[:3]:
                    text = str(item).strip()
                    if text and text not in required_actions:
                        required_actions.append(f"Evidence: {text}")

    if detected_server and result.returncode == 0:
        result_state = "success"
        smart_error_hint = None
        next_best_commands = []

        success_next_commands: List[str] = []
        for suggestion in next_step_suggestions:
            suggestion_text = str(suggestion).strip()
            if suggestion_text and suggestion_text not in success_next_commands:
                success_next_commands.append(suggestion_text)

        user_guidance = {
            "what_worked": f'The command "{command}" started a web server successfully.',
            "what_failed": "",
            "required_user_actions": [],
            "next_commands": success_next_commands,
            "summary": "Command executed successfully and the web app became reachable.",
        }

    payload = {
        "ok": True,
        "session_id": session_id,
        "status": (
            "running"
            if result_state == "running"
            else (
                "success"
                if result_state == "success"
                else (
                    "needs_user_action"
                    if result_state == "needs_user_action"
                    else "failed"
                )
            )
        ),
        "result_state": result_state,
        "exit_code": result.returncode,
        "command": command,
        "node_version_selected": execution_env.get("NODE_VERSION_SELECTED", ""),
        "runtime_selection_reason": execution_env.get("RUNTIME_SELECTION_REASON", ""),
        "stdout": truncate_output(result.stdout, max_output_chars),
        "stderr": truncate_output(result.stderr, max_output_chars),
        "smart_error_hint": smart_error_hint,
        "auto_fix_skip_reason": auto_fix_skip_reason,
        "next_best_commands": next_best_commands,
        "next_step_suggestions": next_step_suggestions,
        "user_guidance": user_guidance,
        "auto_setup_logs": auto_setup_logs,
        "timeout_seconds_applied": effective_timeout,
        "timeout_profile": timeout_metadata["timeout_profile"],
        "detected_server": detected_server,
        "preview_diagnostics": preview_diagnostics,
        "execution_diagnostics": execution_diagnostics,
    }

    latest_meta = load_session_meta(session_id) or {}
    latest_meta["last_activity_at"] = utc_now_iso()
    latest_meta["command_count"] = latest_meta.get("command_count", 0) + 1
    latest_meta["last_command"] = command
    latest_meta["last_command_status"] = payload["status"]
    latest_meta["last_command_result"] = {
        "status": payload["status"],
        "result_state": payload["result_state"],
        "exit_code": payload["exit_code"],
        "command": payload["command"],
        "next_best_commands": payload["next_best_commands"],
        "next_step_suggestions": payload["next_step_suggestions"],
        "user_guidance": payload["user_guidance"],
        "detected_server": payload["detected_server"],
        "preview_diagnostics": payload["preview_diagnostics"],
        "execution_diagnostics": payload["execution_diagnostics"],
    }
    latest_meta["execution_diagnostics"] = execution_diagnostics

    if detected_server:
        latest_meta["detected_server"] = detected_server

    if preview_diagnostics:
        latest_meta["preview_diagnostics"] = preview_diagnostics
    else:
        latest_meta.pop("preview_diagnostics", None)

    save_session_meta(session_id, latest_meta)

    _append_text(
        session_command_log_path(session_id),
        f"[{utc_now_iso()}] status={payload['status']} command={command}\n",
    )

    return payload


def cleanup_expired_sessions() -> Dict[str, Any]:
    ensure_session_root()

    deleted = []

    for name in os.listdir(SESSION_ROOT):
        root_dir = os.path.join(SESSION_ROOT, name)
        meta_path = os.path.join(root_dir, "meta.json")
        logger.warning("[cleanup_expired_sessions] inspect session_id=%s meta_path=%s", name, meta_path)

        if not os.path.isdir(root_dir) or not os.path.isfile(meta_path):
            logger.warning(
                "[cleanup_expired_sessions] skip session_id=%s is_dir=%s meta_exists=%s",
                name,
                os.path.isdir(root_dir),
                os.path.isfile(meta_path),
            )

            continue

        try:
            meta = _read_json(meta_path)

            last = meta.get("last_activity_at")
            ttl = int(meta.get("ttl_seconds", DEFAULT_SESSION_TTL_SECONDS))

            background_processes = meta.get("background_processes")
            if not isinstance(background_processes, list):
                background_processes = []

            running_background_processes = []
            for item in background_processes:
                if not isinstance(item, dict):
                    continue

                raw_pid = item.get("pid")
                try:
                    pid = int(raw_pid)
                except (TypeError, ValueError):
                    pid = 0

                if pid > 0 and _pid_is_running(pid):
                    running_background_processes.append(item)

            if running_background_processes:
                meta["background_processes"] = running_background_processes
                meta["last_activity_at"] = utc_now_iso()
                logger.warning(
                    "[cleanup_expired_sessions] keep_alive session_id=%s running_background_processes=%s",
                    name,
                    len(running_background_processes),
                )

                save_session_meta(name, meta)
                continue

            if background_processes:
                meta.pop("background_processes", None)
                if meta.get("last_command_status") == "running":
                    meta["last_command_status"] = "success"
                logger.warning(
                    "[cleanup_expired_sessions] cleared_background session_id=%s previous_count=%s",
                    name,
                    len(background_processes),
                )

                save_session_meta(name, meta)

            auto_start_state = str(meta.get("auto_start_state") or "").strip().lower()
            last_command_status = str(meta.get("last_command_status") or "").strip().lower()
            if auto_start_state == "running" or last_command_status == "running":
                meta["last_activity_at"] = utc_now_iso()
                logger.warning(
                    "[cleanup_expired_sessions] keep_running session_id=%s auto_start_state=%s last_command_status=%s",
                    name,
                    auto_start_state,
                    last_command_status,
                )
                save_session_meta(name, meta)
                continue


            if last:
                dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
                age = (datetime.now(timezone.utc) - dt).total_seconds()

                if age > ttl:
                    try:
                        _cleanup_session_background_processes(meta)
                    except Exception:
                        pass
                    logger.warning(
                        "[cleanup_expired_sessions] delete_expired session_id=%s age=%s ttl=%s root_dir=%s",
                        name,
                        age,
                        ttl,
                        root_dir,
                    )
                    shutil.rmtree(root_dir, ignore_errors=False)
                    deleted.append(name)

        except Exception:
            continue

    return {
        "ok": True,
        "deleted_sessions": deleted,
    }
