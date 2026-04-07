from __future__ import annotations

from typing import Any, Dict, List


def normalize_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def has_any_script(scripts: Dict[str, Any], names: List[str]) -> bool:
    if not isinstance(scripts, dict):
        return False
    return any(isinstance(scripts.get(name), str) and scripts.get(name).strip() for name in names)


def detect_package_manager(key_files: List[str]) -> str:
    key_file_set = set(key_files)

    if "pnpm-lock.yaml" in key_file_set:
        return "pnpm"
    if "yarn.lock" in key_file_set:
        return "yarn"
    if "package-lock.json" in key_file_set:
        return "npm"
    return "npm"


def should_infer_run_commands(analysis: Dict[str, Any]) -> Dict[str, Any]:
    detected_language = analysis.get("detected_language")
    key_files = normalize_list(analysis.get("key_files"))
    package_scripts = analysis.get("package_scripts", {})
    env_vars = normalize_list(analysis.get("env_vars"))
    existing_run_commands = normalize_list(analysis.get("run_commands"))
    warnings = normalize_list(analysis.get("warnings"))

    result: Dict[str, Any] = {
        "should_infer": False,
        "reason": "",
        "repo_shape": "unknown",
        "package_manager": None,
        "candidate_mode": "none",
    }

    if existing_run_commands:
        result["should_infer"] = False
        result["reason"] = "run_commands_already_present"
        result["repo_shape"] = "already_runnable"
        result["candidate_mode"] = "keep_existing"
        return result

    if detected_language in {"typescript", "javascript"}:
        package_manager = detect_package_manager(key_files)
        result["package_manager"] = package_manager

        if "package.json" not in key_files:
            result["should_infer"] = False
            result["reason"] = "missing_package_json"
            result["repo_shape"] = "non_standard_js_repo"
            return result

        if has_any_script(package_scripts, ["dev", "start", "preview"]):
            result["should_infer"] = True
            result["reason"] = "js_repo_with_runnable_scripts"
            result["repo_shape"] = "js_app_or_workspace"
            result["candidate_mode"] = "direct_script_promotion"
            return result

        if has_any_script(package_scripts, ["build", "test"]):
            result["should_infer"] = False
            result["reason"] = "build_or_test_only_without_default_run_script"
            result["repo_shape"] = "tooling_or_framework_root"
            result["candidate_mode"] = "supporting_only"
            return result

        result["should_infer"] = False
        result["reason"] = "no_relevant_js_scripts_detected"
        result["repo_shape"] = "unclear_js_repo"
        return result

    if detected_language == "python":
        if env_vars:
            result["should_infer"] = True
            result["reason"] = "python_repo_with_env_requirements"
            result["repo_shape"] = "python_env_repo"
            result["candidate_mode"] = "placeholder_or_readme_guided"
            return result

        if "pyproject.toml" in key_files or "requirements.txt" in key_files:
            result["should_infer"] = True
            result["reason"] = "python_repo_with_installable_structure"
            result["repo_shape"] = "python_package_or_app"
            result["candidate_mode"] = "module_or_entrypoint_guess"
            return result

        result["should_infer"] = False
        result["reason"] = "python_repo_without_clear_install_or_run_shape"
        result["repo_shape"] = "unclear_python_repo"
        return result

    if warnings and "No clear run command detected yet" in warnings:
        result["reason"] = "warning_present_but_language_policy_unknown"
        result["repo_shape"] = "unknown_with_warning"

    return result
