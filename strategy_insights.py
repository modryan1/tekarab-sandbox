from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List


CONFIDENCE_ORDER = {"low": 1, "medium": 2, "high": 3}


def dedupe_keep_order(items: List[str]) -> List[str]:
    seen = set()
    result: List[str] = []
    for item in items or []:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def normalize_str_list(value: Any) -> List[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _safe_read_text(path: str, limit: int = 200_000) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            return fh.read(limit)
    except OSError:
        return ""


def _safe_read_json(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            value = json.load(fh)
        if isinstance(value, dict):
            return value
    except Exception:
        pass
    return {}


def _find_root_package_json(repo_dir: str, file_list: List[str]) -> tuple[str | None, Dict[str, Any]]:
    candidates = [path for path in file_list if path.lower().endswith("package.json")]
    package_json_path = None

    if "package.json" in candidates:
        package_json_path = "package.json"
    elif candidates:
        package_json_path = min(candidates, key=lambda p: (p.count("/"), len(p)))

    if not package_json_path:
        return None, {}

    return package_json_path, _safe_read_json(os.path.join(repo_dir, package_json_path))


def _find_marker_paths(file_list: List[str], suffixes: tuple[str, ...]) -> List[str]:
    lower_suffixes = tuple(item.lower() for item in suffixes)
    return [path for path in file_list if path.lower().endswith(lower_suffixes)]


def _command_for_manager(package_manager: str, script_name: str) -> str:
    package_manager = str(package_manager or "npm").strip().lower()
    script_name = str(script_name or "").strip()
    if package_manager == "pnpm":
        return f"pnpm {script_name}" if script_name in {"dev", "start", "preview", "build", "test"} else f"pnpm run {script_name}"
    if package_manager == "yarn":
        return f"yarn {script_name}" if script_name in {"dev", "start", "preview", "build", "test"} else f"yarn {script_name}"
    if package_manager == "bun":
        return f"bun run {script_name}"
    if package_manager == "deno":
        return f"deno task {script_name}"
    return f"npm run {script_name}"


def _confidence_max(current: str, candidate: str) -> str:
    return candidate if CONFIDENCE_ORDER.get(candidate, 0) > CONFIDENCE_ORDER.get(current, 0) else current


def _scan_root_relative_web_refs(repo_dir: str, max_matches: int = 8) -> List[str]:
    candidate_dirs = [
        os.path.join(repo_dir, "src"),
        os.path.join(repo_dir, "public"),
        os.path.join(repo_dir, "app"),
        os.path.join(repo_dir, "pages"),
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
    patterns = ['href="/', 'src="/', 'action="/', 'url(/', 'content="/']
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
                                rel_path = os.path.relpath(path, repo_dir).replace("\\", "/")
                                matches.append(f"{rel_path}:{lineno}: {line.strip()}")
                                if len(matches) >= max_matches:
                                    return matches
                except OSError:
                    continue
    return matches


def analyze_execution_signals(
    repo_dir: str,
    file_list: List[str],
    key_files: List[str],
    detected_language: str,
    readme_text: str,
    readme_commands: List[str],
    package_scripts: Dict[str, Any],
    run_commands: List[str],
) -> Dict[str, Any]:
    lower_files = [path.lower() for path in file_list]
    package_json_path, package_json_data = _find_root_package_json(repo_dir, file_list)
    package_json_blob = json.dumps(package_json_data, ensure_ascii=False).lower() if package_json_data else ""
    package_scripts = package_scripts if isinstance(package_scripts, dict) else {}
    dependencies = package_json_data.get("dependencies") or {}
    dev_dependencies = package_json_data.get("devDependencies") or {}
    scripts_blob = " ".join(f"{k} {v}" for k, v in package_scripts.items()).lower()
    readme_blob = (readme_text or "").lower()
    combined_blob = " ".join(lower_files + normalize_str_list(key_files) + normalize_str_list(readme_commands) + normalize_str_list(run_commands)).lower()

    package_managers_detected: List[str] = []
    lockfile_signals = {
        "present": [],
        "conflicting_lockfiles": [],
        "mismatch_risk": "low",
        "reasons": [],
    }

    if any(path.endswith("pnpm-lock.yaml") for path in lower_files):
        package_managers_detected.append("pnpm")
        lockfile_signals["present"].append("pnpm-lock.yaml")
    if any(path.endswith("yarn.lock") for path in lower_files):
        package_managers_detected.append("yarn")
        lockfile_signals["present"].append("yarn.lock")
    if any(path.endswith("package-lock.json") for path in lower_files):
        package_managers_detected.append("npm")
        lockfile_signals["present"].append("package-lock.json")
    if any(path.endswith("bun.lock") or path.endswith("bun.lockb") for path in lower_files):
        package_managers_detected.append("bun")
        lockfile_signals["present"].append("bun.lock")
    if any(path.endswith("deno.json") or path.endswith("deno.jsonc") for path in lower_files):
        package_managers_detected.append("deno")

    if package_json_data and "npm" not in package_managers_detected:
        package_managers_detected.append("npm")

    package_managers_detected = dedupe_keep_order(package_managers_detected)

    if len(lockfile_signals["present"]) > 1:
        lockfile_signals["conflicting_lockfiles"] = list(lockfile_signals["present"])
        lockfile_signals["mismatch_risk"] = "high"
        lockfile_signals["reasons"].append("Multiple lockfiles detected; install strategy must avoid frozen assumptions.")

    framework_signals: List[Dict[str, Any]] = []
    runtime_signals: List[Dict[str, Any]] = []
    workspace_signals: Dict[str, Any] = {
        "is_workspace": False,
        "tooling": [],
        "package_json_count": sum(1 for path in lower_files if path.endswith("package.json")),
        "reasons": [],
    }
    config_package_mismatches: List[Dict[str, Any]] = []

    def add_framework(name: str, confidence: str, evidence: List[str]) -> None:
        framework_signals.append({"name": name, "confidence": confidence, "evidence": evidence})

    def add_runtime(name: str, confidence: str, evidence: List[str]) -> None:
        runtime_signals.append({"name": name, "confidence": confidence, "evidence": evidence})

    dependency_blob = " ".join(list(dependencies.keys()) + list(dev_dependencies.keys())).lower()

    if _find_marker_paths(file_list, ("vite.config.js", "vite.config.ts", "vite.config.mjs", "vite.config.cjs")) or "vite" in dependency_blob or "vite" in scripts_blob:
        add_framework("vite", "high", ["vite config or dependency/script detected"])
    if _find_marker_paths(file_list, ("next.config.js", "next.config.mjs", "next.config.ts")) or "next" in dependency_blob or "next dev" in scripts_blob:
        add_framework("next", "high", ["next config or dependency/script detected"])
    if _find_marker_paths(file_list, ("astro.config.mjs", "astro.config.js", "astro.config.ts")) or "astro" in dependency_blob or "astro dev" in scripts_blob:
        add_framework("astro", "high", ["astro config or dependency/script detected"])
    if "react" in dependency_blob:
        add_framework("react", "medium", ["react dependency detected"])
    if any(path.endswith("deno.json") or path.endswith("deno.jsonc") for path in lower_files):
        add_framework("deno", "high", ["deno config detected"])
    if any(path.endswith("nx.json") for path in lower_files) or "nx " in scripts_blob:
        add_framework("nx", "high", ["nx workspace signal detected"])
    if any(path.endswith("turbo.json") for path in lower_files) or "turbo" in scripts_blob:
        add_framework("turbo", "high", ["turbo workspace signal detected"])

    if detected_language in {"javascript", "typescript"} or package_json_data:
        add_runtime("node", "high", ["Node/JS project signals detected"])
    if "bun" in package_managers_detected or "bun " in scripts_blob:
        add_runtime("bun", "medium", ["bun lockfile or scripts detected"])
    if "deno" in package_managers_detected or "deno " in scripts_blob:
        add_runtime("deno", "medium", ["deno config or commands detected"])
    if detected_language == "python":
        add_runtime("python", "high", ["Python source files detected"])
    if detected_language == "go":
        add_runtime("go", "high", ["Go source files detected"])

    workspaces = package_json_data.get("workspaces")
    if workspaces or any(path.endswith(name) for path in lower_files for name in ("pnpm-workspace.yaml", "turbo.json", "nx.json", "lerna.json")) or workspace_signals["package_json_count"] > 1:
        workspace_signals["is_workspace"] = True
        if workspaces:
            workspace_signals["tooling"].append("package.json-workspaces")
        if any(path.endswith("pnpm-workspace.yaml") for path in lower_files):
            workspace_signals["tooling"].append("pnpm-workspace")
        if any(path.endswith("turbo.json") for path in lower_files):
            workspace_signals["tooling"].append("turbo")
        if any(path.endswith("nx.json") for path in lower_files):
            workspace_signals["tooling"].append("nx")
        if any(path.endswith("lerna.json") for path in lower_files):
            workspace_signals["tooling"].append("lerna")
        workspace_signals["tooling"] = dedupe_keep_order(workspace_signals["tooling"])
        workspace_signals["reasons"].append("Workspace/monorepo signals detected.")

    preferred_package_manager = {"name": "npm", "confidence": "low", "reasons": ["Fallback default for Node-compatible repos."]}
    if "deno" in package_managers_detected and not package_json_data:
        preferred_package_manager = {"name": "deno", "confidence": "high", "reasons": ["Deno config found without a root package.json."]}
    elif any(path.endswith("pnpm-lock.yaml") for path in lower_files):
        preferred_package_manager = {"name": "pnpm", "confidence": "high", "reasons": ["pnpm lockfile detected."]}
    elif any(path.endswith("yarn.lock") for path in lower_files):
        preferred_package_manager = {"name": "yarn", "confidence": "high", "reasons": ["yarn.lock detected."]}
    elif any(path.endswith("bun.lock") or path.endswith("bun.lockb") for path in lower_files):
        preferred_package_manager = {"name": "bun", "confidence": "medium", "reasons": ["bun lockfile detected."]}
    elif any(path.endswith("package-lock.json") for path in lower_files):
        preferred_package_manager = {"name": "npm", "confidence": "high", "reasons": ["package-lock.json detected."]}
    elif "pnpm " in scripts_blob or "pnpm " in combined_blob:
        preferred_package_manager = {"name": "pnpm", "confidence": "medium", "reasons": ["pnpm commands appear in scripts or docs."]}
    elif "yarn " in scripts_blob or "yarn " in combined_blob:
        preferred_package_manager = {"name": "yarn", "confidence": "medium", "reasons": ["yarn commands appear in scripts or docs."]}
    elif "bun " in scripts_blob or "bun " in combined_blob:
        preferred_package_manager = {"name": "bun", "confidence": "medium", "reasons": ["bun commands appear in scripts or docs."]}

    if len(lockfile_signals["present"]) > 1:
        preferred_package_manager["confidence"] = "medium"
        preferred_package_manager["reasons"].append("Confidence reduced because multiple lockfiles exist.")

    if preferred_package_manager["name"] == "pnpm" and any(path.endswith("package-lock.json") for path in lower_files):
        config_package_mismatches.append({
            "type": "lockfile_mismatch",
            "severity": "high",
            "message": "Both pnpm and npm lockfiles are present; avoid npm ci unless the workspace is confirmed npm-native.",
        })
    if preferred_package_manager["name"] == "bun" and package_json_data:
        config_package_mismatches.append({
            "type": "mixed_runtime_signals",
            "severity": "medium",
            "message": "Bun lockfile exists with Node package metadata; prefer adaptive fallback commands.",
        })
    if "deno" in package_managers_detected and package_json_data:
        config_package_mismatches.append({
            "type": "mixed_runtime_signals",
            "severity": "high",
            "message": "Deno configuration and package.json coexist; treat this as a mixed-runtime repository.",
        })
    if workspace_signals["is_workspace"] and preferred_package_manager["name"] == "npm" and any(tool in workspace_signals["tooling"] for tool in ["pnpm-workspace", "turbo", "nx"]):
        config_package_mismatches.append({
            "type": "workspace_package_manager_risk",
            "severity": "medium",
            "message": "Workspace tooling is present; plain npm install/npm ci may be less reliable than workspace-native commands.",
        })

    install_strategy_candidates: List[Dict[str, Any]] = []
    build_strategy_candidates: List[Dict[str, Any]] = []
    run_strategy_candidates: List[Dict[str, Any]] = []
    preview_strategy_candidates: List[Dict[str, Any]] = []

    pm = preferred_package_manager["name"]
    mismatch_high = any(item.get("severity") == "high" for item in config_package_mismatches) or lockfile_signals["mismatch_risk"] == "high"

    if pm == "pnpm":
        install_strategy_candidates.extend([
            {"command": "pnpm install", "confidence": "high", "reason": "Preferred package manager is pnpm."},
            {"command": "pnpm install --no-frozen-lockfile", "confidence": "medium", "reason": "Safer fallback when lockfile drift exists."},
        ])
    elif pm == "yarn":
        install_strategy_candidates.extend([
            {"command": "yarn install", "confidence": "high", "reason": "Preferred package manager is yarn."},
            {"command": "yarn install --mode=skip-build", "confidence": "low", "reason": "Optional lower-risk retry for problematic installs."},
        ])
    elif pm == "bun":
        install_strategy_candidates.extend([
            {"command": "bun install", "confidence": "medium", "reason": "Preferred package manager is bun."},
            {"command": "npm install", "confidence": "low", "reason": "Fallback when bun-specific install fails in mixed repos."},
        ])
    elif pm == "deno":
        install_strategy_candidates.append({"command": "deno cache main.ts", "confidence": "low", "reason": "Deno repos usually cache dependencies rather than install them."})
    else:
        if any(path.endswith("package-lock.json") for path in lower_files) and not mismatch_high and not workspace_signals["is_workspace"]:
            install_strategy_candidates.append({"command": "npm ci", "confidence": "medium", "reason": "package-lock.json detected with low mismatch risk."})
        install_strategy_candidates.extend([
            {"command": "npm install", "confidence": "high" if mismatch_high else "medium", "reason": "Safer default npm install strategy."},
            {"command": "npm install --legacy-peer-deps", "confidence": "low", "reason": "Fallback for dependency-resolution conflicts."},
        ])

    if "build" in package_scripts:
        build_strategy_candidates.append({"command": _command_for_manager(pm, "build"), "confidence": "high", "reason": "package.json build script detected."})
    if "preview" in package_scripts:
        preview_strategy_candidates.append({"command": _command_for_manager(pm, "preview"), "confidence": "high", "reason": "package.json preview script detected."})
    if "start" in package_scripts:
        run_strategy_candidates.append({"command": _command_for_manager(pm, "start"), "confidence": "high", "reason": "package.json start script detected."})
    if "dev" in package_scripts:
        run_strategy_candidates.append({"command": _command_for_manager(pm, "dev"), "confidence": "high", "reason": "package.json dev script detected."})

    if not build_strategy_candidates and any(item["name"] in {"next", "astro", "vite"} for item in framework_signals):
        build_strategy_candidates.append({"command": _command_for_manager(pm, "build"), "confidence": "medium", "reason": "Framework suggests a conventional build command."})
    if not preview_strategy_candidates:
        if any(item["name"] == "vite" for item in framework_signals):
            preview_strategy_candidates.append({"command": "npx vite preview --host 0.0.0.0 --port 4173", "confidence": "medium", "reason": "Vite projects commonly support production preview."})
        if any(item["name"] == "astro" for item in framework_signals):
            preview_strategy_candidates.append({"command": "npx astro preview --host 0.0.0.0 --port 4173", "confidence": "medium", "reason": "Astro projects commonly support preview mode."})
        if any(item["name"] == "next" for item in framework_signals):
            preview_strategy_candidates.append({"command": "npm run start", "confidence": "medium", "reason": "Next.js production mode usually runs through start after build."})

    run_strategy_candidates.extend({"command": cmd, "confidence": "medium", "reason": "Analyzer run command candidate."} for cmd in run_commands[:4] if cmd)
    run_strategy_candidates = [item for item in run_strategy_candidates if item.get("command")]
    preview_strategy_candidates = [item for item in preview_strategy_candidates if item.get("command")]
    build_strategy_candidates = [item for item in build_strategy_candidates if item.get("command")]

    subpath_evidence = _scan_root_relative_web_refs(repo_dir)
    subpath_preview_risk = {
        "level": "medium" if subpath_evidence else "low",
        "reasons": ["Root-relative links/assets can break under session subpath previews."] if subpath_evidence else [],
        "evidence": subpath_evidence,
    }

    dev_preview_reasons: List[str] = []
    if any(item["name"] == "vite" for item in framework_signals):
        dev_preview_reasons.append("Vite dev mode commonly exposes /@vite/client through the public proxy.")
    if any(item["name"] == "react" for item in framework_signals) and any("dev" in item.get("command", "") for item in run_strategy_candidates):
        dev_preview_reasons.append("React dev mode may expose refresh artifacts such as /@react-refresh.")
    dev_preview_risk = {
        "level": "high" if dev_preview_reasons else "low",
        "reasons": dev_preview_reasons,
        "evidence": ["/@vite/client", "/@react-refresh"] if dev_preview_reasons else [],
    }

    production_preview_readiness = {
        "level": "high" if build_strategy_candidates and preview_strategy_candidates else ("medium" if build_strategy_candidates else "low"),
        "preferred_mode": "build_preview" if build_strategy_candidates and preview_strategy_candidates else ("dev" if run_strategy_candidates else "unclear"),
        "reasons": [],
    }
    if build_strategy_candidates and preview_strategy_candidates:
        production_preview_readiness["reasons"].append("Repo has both build and preview strategies.")
    elif build_strategy_candidates:
        production_preview_readiness["reasons"].append("Repo has a build strategy but preview/start is less explicit.")
    elif run_strategy_candidates:
        production_preview_readiness["reasons"].append("Repo appears runnable mainly through dev/start commands.")

    profile = "unclear"
    profile_confidence = "low"
    profile_reasons: List[str] = []
    framework_names = {item["name"] for item in framework_signals}
    runtime_names = {item["name"] for item in runtime_signals}
    if len(runtime_names) > 1 and any(name in runtime_names for name in {"deno", "bun"}) and "node" in runtime_names:
        profile = "mixed_runtime_workspace"
        profile_confidence = "high"
        profile_reasons.append("Multiple JavaScript runtimes detected in one repository.")
    elif any(name in framework_names for name in {"next", "vite", "astro", "react"}):
        if production_preview_readiness["level"] == "high":
            profile = "production_ready_web_app"
            profile_confidence = "high"
            profile_reasons.append("Web framework with build and preview strategy detected.")
        else:
            profile = "development_first_web_app"
            profile_confidence = "medium"
            profile_reasons.append("Web framework detected but production preview is incomplete.")
    elif detected_language == "go":
        profile = "compiled_cli_or_service"
        profile_confidence = "medium"
        profile_reasons.append("Go repository signals detected.")
    elif detected_language == "python":
        profile = "python_project"
        profile_confidence = "medium"
        profile_reasons.append("Python repository signals detected.")

    return {
        "root_package_json_path": package_json_path,
        "package_managers_detected": package_managers_detected,
        "preferred_package_manager": preferred_package_manager,
        "install_strategy_candidates": install_strategy_candidates,
        "run_strategy_candidates": dedupe_strategy_candidates(run_strategy_candidates),
        "build_strategy_candidates": dedupe_strategy_candidates(build_strategy_candidates),
        "preview_strategy_candidates": dedupe_strategy_candidates(preview_strategy_candidates),
        "framework_signals": framework_signals,
        "runtime_signals": runtime_signals,
        "lockfile_signals": lockfile_signals,
        "workspace_signals": workspace_signals,
        "subpath_preview_risk": subpath_preview_risk,
        "dev_preview_risk": dev_preview_risk,
        "production_preview_readiness": production_preview_readiness,
        "repo_execution_profile": {
            "profile": profile,
            "confidence": profile_confidence,
            "reasons": profile_reasons,
        },
        "config_package_mismatches": config_package_mismatches,
    }


def dedupe_strategy_candidates(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    result: List[Dict[str, Any]] = []
    for item in items or []:
        command = str(item.get("command") or "").strip()
        if not command or command in seen:
            continue
        seen.add(command)
        result.append({
            "command": command,
            "confidence": str(item.get("confidence") or "low").strip().lower(),
            "reason": str(item.get("reason") or "").strip(),
        })
    return result


def categorize_install_failure(command: str, stdout: Any, stderr: Any, analysis: Dict[str, Any] | None = None) -> Dict[str, Any] | None:
    command_text = str(command or "").strip().lower()
    combined = (str(stdout or "") + "\n" + str(stderr or "")).lower()
    analysis = analysis or {}
    install_candidates = [item.get("command") for item in analysis.get("install_strategy_candidates") or [] if isinstance(item, dict)]
    fallback_candidates = [str(cmd).strip() for cmd in install_candidates if str(cmd).strip() and str(cmd).strip().lower() != command_text]

    if not any(token in command_text for token in ["install", "npm ci", "pnpm", "yarn", "bun install"]):
        return None

    if any(token in combined for token in ["cipreferonline", "package-lock.json is not in sync", "npm ci can only install", "outdated lockfile", "frozen-lockfile", "lockfile would have been created", "lockfile is up to date, resolution step is skipped", "workspace protocol is not supported"]):
        return {
            "category": "lockfile_mismatch",
            "confidence": "high",
            "message": "Install failed because the current package manager or frozen lockfile mode does not match repository state.",
            "fallback_candidates": fallback_candidates,
        }

    if any(token in combined for token in ["this project is configured to use pnpm", "this project uses yarn", "use bun install", "unsupported url type \"workspace:\"", "workspace:*", "command not found: pnpm"]):
        return {
            "category": "package_manager_mismatch",
            "confidence": "high",
            "message": "Install failed because repository tooling expects a different package manager.",
            "fallback_candidates": fallback_candidates,
        }

    if any(token in combined for token in ["unsupported engine", "requires node", "requires a different node", "requires bun", "requires deno"]):
        return {
            "category": "runtime_mismatch",
            "confidence": "medium",
            "message": "Install failed because the selected runtime does not match repository requirements.",
            "fallback_candidates": fallback_candidates,
        }

    return None


def classify_preview_diagnostics(command: str, analysis: Dict[str, Any], subpath_preview_risk: Dict[str, Any] | None = None) -> Dict[str, Any]:
    analysis = analysis or {}
    command_text = str(command or "").strip().lower()
    dev_preview_risk = analysis.get("dev_preview_risk") or {"level": "low", "reasons": [], "evidence": []}
    degraded_preview_mode = "dev" if any(token in command_text for token in [" dev", "vite", "react-refresh", "@vite/client"]) else "production"
    preview_diagnostics = {
        "dev_preview_risk": dev_preview_risk,
        "subpath_preview_risk": subpath_preview_risk or analysis.get("subpath_preview_risk") or {"level": "low", "reasons": [], "evidence": []},
        "degraded_preview_mode": degraded_preview_mode,
    }
    return preview_diagnostics
