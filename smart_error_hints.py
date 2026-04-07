from __future__ import annotations

import re
from typing import Any


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", errors="replace")
        except Exception:
            return str(value)
    return str(value)


def _contains_any(text: str, patterns: list[str]) -> bool:
    return any(p in text for p in patterns)


def _build_auto_fix(
    strategy: str,
    summary: str,
    commands: list[str] | None = None,
    confidence: str = "medium",
) -> dict[str, Any]:
    return {
        "strategy": strategy,
        "summary": summary,
        "commands": list(commands or []),
        "confidence": confidence,
    }

def _extract_node_versions(text: str) -> tuple[str | None, str | None]:
    """
    Extract required and current Node.js versions from error output.
    """

    required = None
    current = None

    # Pattern like:
    # wanted: {"node":">=22.12.0"} (current: {"node":"v20.20.1"})
    wanted_match = re.search(r'wanted:\s*\{[^}]*"node"\s*:\s*"([^"]+)"', text)
    current_match = re.search(r'current:\s*\{[^}]*"node"\s*:\s*"([^"]+)"', text)

    if wanted_match:
        required = wanted_match.group(1).strip()

    if current_match:
        current = current_match.group(1).strip()

    # Fallback: plain version mentions
    if not required:
        generic_req = re.search(r'node.*>=\s*([0-9]+\.[0-9]+\.[0-9]+)', text, re.IGNORECASE)
        if generic_req:
            required = f">={generic_req.group(1)}"

    if not current:
        generic_cur = re.search(r'node\.js\s*v?([0-9]+\.[0-9]+\.[0-9]+)', text, re.IGNORECASE)
        if generic_cur:
            current = f"v{generic_cur.group(1)}"

    return required, current

def _extract_python_versions(text: str) -> tuple[str | None, str | None]:
    """
    Extract required and current Python versions from error output.
    """

    required = None
    current = None

    # Pattern like:
    # requires a different Python: 3.10.12 not in '>=3.11'
    mismatch_match = re.search(
        r"requires(?: a different)? python:\s*([0-9]+\.[0-9]+(?:\.[0-9]+)?)\s+not in\s+['\"]([^'\"]+)['\"]",
        text,
        re.IGNORECASE,
    )
    if mismatch_match:
        current = f"v{mismatch_match.group(1).strip()}"
        required = mismatch_match.group(2).strip()
        return required, current

    required_patterns = [
        r"not in\s*['\"]([^'\"]+)['\"]",
        r"requires-python\s*[=:]\s*['\"]?([^\"'\n]+)",
        r"python\s*>=\s*([0-9]+\.[0-9]+(?:\.[0-9]+)?)",
        r"supported version:\s*['\"]([^'\"]+)['\"]",
    ]

    current_patterns = [
        r"current python[:\s]+v?([0-9]+\.[0-9]+(?:\.[0-9]+)?)",
        r"python version[:\s]+v?([0-9]+\.[0-9]+(?:\.[0-9]+)?)",
        r"python\s+([0-9]+\.[0-9]+(?:\.[0-9]+)?)",
    ]

    for pattern in required_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = (match.group(1) or "").strip()
            if value:
                required = value
                if re.fullmatch(r"[0-9]+\.[0-9]+(?:\.[0-9]+)?", required):
                    required = f">={required}"
                break

    for pattern in current_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = (match.group(1) or "").strip()
            if value:
                current = value
                if re.fullmatch(r"[0-9]+\.[0-9]+(?:\.[0-9]+)?", current):
                    current = f"v{current}"
                break

    return required, current

def build_smart_error_hint(
    command: str,
    exit_code: int | None,
    stdout: Any = None,
    stderr: Any = None,
) -> dict[str, Any] | None:
    cmd = _normalize_text(command).strip()
    out = _normalize_text(stdout)
    err = _normalize_text(stderr)
    combined = f"{out}\n{err}".strip()
    lower = combined.lower()

    if exit_code in (0, None) and not lower:
        return None

    # --------------------------------------------------
    # 1) Node.js version mismatch
    # --------------------------------------------------
    if _contains_any(
        lower,
        [
            "unsupported engine",
            "not compatible with your version of node",
            "requires node",
            "engines \"node\"",
            "is not supported by astro",
            "please upgrade node.js to a supported version",
        ],
    ):
        required_node, current_node = _extract_node_versions(combined)

        details = (
            "The current command output suggests the repository's required Node.js version does not match "
            "the installed version. Check the package.json engines field in the repo root and in any nested "
            "workspace package.json files, because monorepos often define stricter version requirements in "
            "subprojects."
        )

        if required_node or current_node:
            version_bits = []
            if required_node:
                version_bits.append(f"Required Node.js: {required_node}.")
            if current_node:
                version_bits.append(f"Current Node.js: {current_node}.")
            details = " ".join(version_bits) + " " + details

        return {
            "category": "node_version_mismatch",
            "title": "Node.js version mismatch",
            "hint": "This repo requires a different Node.js version than the one currently available.",
            "details": details,
            "suggested_commands": [
                "node -v",
                "find . -maxdepth 3 -name package.json -print",
                "cat package.json",
            ],
            "auto_fix": _build_auto_fix(
                strategy="inspect_required_runtime",
                summary="Inspect the current Node.js version and compare it with root and nested package.json engine requirements.",
                commands=[
                    "node -v",
                    "find . -maxdepth 3 -name package.json -print",
                    "cat package.json",
                ],
                confidence="high",
            ),
            "confidence": "high",
        }

    # --------------------------------------------------
    # 2) Python version mismatch
    # --------------------------------------------------
    if _contains_any(
        lower,
        [
            "requires python",
            "requires a different python",
            "unsupported python version",
            "python version",
            "not supported on this python",
            "requires-python",
        ],
    ):
        required_python, current_python = _extract_python_versions(combined)

        details = (
            "The current command output suggests the repository requires a different Python version. "
            "Check pyproject.toml, setup.py, setup.cfg, or the error output to confirm the required version "
            "before creating a new virtual environment or reinstalling dependencies."
        )

        if required_python or current_python:
            version_bits = []
            if required_python:
                version_bits.append(f"Required Python: {required_python}.")
            if current_python:
                version_bits.append(f"Current Python: {current_python}.")
            details = " ".join(version_bits) + " " + details

        return {
            "category": "python_version_mismatch",
            "title": "Python version mismatch",
            "hint": "This repo appears to require a newer or different Python version than the one used in the current command.",
            "details": details,
            "suggested_commands": [
                "python3 --version",
                "python3.11 --version",
                "find . -maxdepth 2 \\( -name pyproject.toml -o -name setup.py -o -name setup.cfg \\) -print",
                "cat pyproject.toml",
            ],
            "auto_fix": _build_auto_fix(
                strategy="inspect_required_runtime",
                summary="Inspect the current Python version and compare it with the repository's declared Python requirement before rebuilding the environment.",
                commands=[
                    "python3 --version",
                    "python3.11 --version",
                    "find . -maxdepth 2 \\( -name pyproject.toml -o -name setup.py -o -name setup.cfg \\) -print",
                    "cat pyproject.toml",
                ],
                confidence="high",
            ),
            "confidence": "high",
        }

    # --------------------------------------------------
    # 3) Command not found
    # --------------------------------------------------
    not_found_patterns = [
        r"(?:/bin/sh:\s*\d+:\s*)?([a-zA-Z0-9._-]+):\s*not found",
        r"(?:bash:\s*line\s*\d+:\s*)?([a-zA-Z0-9._-]+):\s*command not found",
        r"([a-zA-Z0-9._-]+):\s*command not found",
    ]

    missing_cmd = None
    for pattern in not_found_patterns:
        match = re.search(pattern, combined, flags=re.IGNORECASE)
        if match:
            missing_cmd = match.group(1).strip()
            break

    if missing_cmd:
        known_command_fixes: dict[str, dict[str, Any]] = {
            "python": {
                "title": "python is not available as a command",
                "hint": "The environment likely exposes Python as python3 instead of python.",
                "details": "Many Linux environments provide python3 but not python.",
                "suggested_commands": [
                    "python3 --version",
                    "python3 main.py",
                ],
                "auto_fix": _build_auto_fix(
                    strategy="command_substitution",
                    summary="Try the python3 command instead of python.",
                    commands=[
                        cmd.replace("python", "python3", 1) if cmd.startswith("python") else "python3 --version",
                    ],
                    confidence="high",
                ),
            },
            "pip": {
                "title": "pip is not available as a command",
                "hint": "The environment likely exposes pip as pip3 instead of pip.",
                "details": "Many Linux environments provide pip3 but not pip.",
                "suggested_commands": [
                    "pip3 --version",
                    "pip3 install -r requirements.txt",
                ],
                "auto_fix": _build_auto_fix(
                    strategy="command_substitution",
                    summary="Try pip3 instead of pip.",
                    commands=[
                        cmd.replace("pip", "pip3", 1) if cmd.startswith("pip") else "pip3 --version",
                    ],
                    confidence="high",
                ),
            },
            "yarn": {
                "title": "yarn is not installed",
                "hint": "The repo is trying to use yarn, but yarn is not available in this sandbox.",
                "details": "If the project does not depend on yarn-specific workspace behavior, npm may work as a fallback.",
                "suggested_commands": [
                    "npm install",
                    "npm run build",
                    "npm run dev",
                    "npm install -g yarn",
                ],
                "auto_fix": _build_auto_fix(
                    strategy="fallback_package_manager",
                    summary="Try npm as a fallback, or install yarn if the repo depends on it.",
                    commands=[
                        "npm install",
                        "npm run build",
                    ],
                    confidence="medium",
                ),
            },
            "pnpm": {
                "title": "pnpm is not installed",
                "hint": "The repo is trying to use pnpm, but pnpm is not available in this sandbox.",
                "details": "If this repo is simple, npm may work. If it depends on pnpm workspace behavior, install pnpm first.",
                "suggested_commands": [
                    "npm install",
                    "npm run build",
                    "npm install -g pnpm",
                ],
                "auto_fix": _build_auto_fix(
                    strategy="fallback_package_manager",
                    summary="Try npm first, or install pnpm if the repo uses pnpm workspaces.",
                    commands=[
                        "npm install",
                    ],
                    confidence="medium",
                ),
            },
            "poetry": {
                "title": "poetry is not installed",
                "hint": "This Python repo expects Poetry, but Poetry is not available in the environment.",
                "details": "You can either install Poetry or use a pip-based fallback if the repo supports it.",
                "suggested_commands": [
                    "pip install poetry",
                    "poetry install",
                    "pip install -r requirements.txt",
                    "pip install -e .",
                ],
                "auto_fix": _build_auto_fix(
                    strategy="python_dependency_bootstrap",
                    summary="Try a pip-based install flow if the project supports it.",
                    commands=[
                        "pip install -r requirements.txt",
                        "pip install -e .",
                    ],
                    confidence="medium",
                ),
            },
            "uv": {
                "title": "uv is not installed",
                "hint": "This repo expects uv, but uv is not available in the environment.",
                "details": "You can install uv, or fall back to venv and pip if the project allows it.",
                "suggested_commands": [
                    "python3 -m venv .venv",
                    "source .venv/bin/activate",
                    "pip install -r requirements.txt",
                ],
                "auto_fix": _build_auto_fix(
                    strategy="python_dependency_bootstrap",
                    summary="Try a standard venv + pip flow.",
                    commands=[
                        "python3 -m venv .venv",
                        "source .venv/bin/activate",
                        "pip install -r requirements.txt",
                    ],
                    confidence="medium",
                ),
            },
            "astro": {
                "title": "astro is not installed",
                "hint": "This repo expects Astro, but the astro command is not available yet.",
                "details": "Astro is usually installed as a project dependency. Install dependencies first, then retry the build.",
                "suggested_commands": [
                    "pnpm install",
                    "npm install",
                    "pnpm -r build",
                ],
                "auto_fix": _build_auto_fix(
                    strategy="node_dependency_bootstrap",
                    summary="Install dependencies first, then retry the build.",
                    commands=[
                        "pnpm install",
                        "pnpm -r build",
                    ],
                    confidence="medium",
                ),
            },
            "tsc": {
                "title": "tsc is not installed",
                "hint": "This repo expects TypeScript, but the tsc command is not available yet.",
                "details": "tsc is usually installed as a project dependency. Install dependencies first, then retry the build.",
                "suggested_commands": [
                    "pnpm install",
                    "npm install",
                    "pnpm -r build",
                ],
                "auto_fix": _build_auto_fix(
                    strategy="node_dependency_bootstrap",
                    summary="Install dependencies first, then retry the build.",
                    commands=[
                        "pnpm install",
                        "pnpm -r build",
                    ],
                    confidence="medium",
                ),
            },
        }

        if missing_cmd in known_command_fixes:
            info = known_command_fixes[missing_cmd]
            return {
                "category": "missing_command",
                "title": info["title"],
                "hint": info["hint"],
                "details": info["details"],
                "suggested_commands": info["suggested_commands"],
                "auto_fix": info["auto_fix"],
                "confidence": "high",
            }

        return {
            "category": "missing_command",
            "title": f"{missing_cmd} is not installed",
            "hint": f"The command '{missing_cmd}' was not found in the sandbox environment.",
            "details": "Install the missing tool first, or switch to an alternative command that is already available.",
            "suggested_commands": [],
            "auto_fix": _build_auto_fix(
                strategy="manual_install_or_replace",
                summary=f"Install '{missing_cmd}' or replace it with an available equivalent command.",
                commands=[],
                confidence="low",
            ),
            "confidence": "high",
        }

    # --------------------------------------------------
    # 2) Monorepo / workspace dependency hints
    # --------------------------------------------------
    if _contains_any(lower, ["workspace:", "workspaces", "workspace package", "unsupported url type \"workspace:\""]):
        return {
            "category": "monorepo_workspace",
            "title": "Monorepo workspace dependency detected",
            "hint": "This repo appears to use workspace-based dependencies, so a plain install may fail depending on the package manager and repo layout.",
            "details": "Many monorepos require running the package manager from the repo root and may depend on pnpm, yarn workspaces, turbo, or nx.",
            "suggested_commands": [
                "pwd",
                "find . -maxdepth 3 -name package.json",
                "cat package.json",
                "pnpm install",
                "yarn install",
                "npm install",
            ],
            "auto_fix": _build_auto_fix(
                strategy="run_from_repo_root_or_use_workspace_manager",
                summary="Verify you are at the repo root, then try the package manager that matches the workspace setup.",
                commands=[
                    "pwd",
                    "find . -maxdepth 3 -name package.json",
                    "pnpm install",
                ],
                confidence="high",
            ),
            "confidence": "high",
        }

    # --------------------------------------------------
    # 3) Node.js version mismatch
    # --------------------------------------------------
    if _contains_any(
        lower,
        [
            "unsupported engine",
            "not compatible with your version of node",
            "requires node",
            "engines \"node\"",
            "is not supported by astro",
            "please upgrade node.js to a supported version",
        ],
    ):
        required_node, current_node = _extract_node_versions(combined)

        details = (
            "The current command output suggests the repository's required Node.js version does not match "
            "the installed version. Check the package.json engines field in the repo root and in any nested "
            "workspace package.json files, because monorepos often define stricter version requirements in "
            "subprojects."
        )

        if required_node or current_node:
            version_bits = []
            if required_node:
                version_bits.append(f"Required Node.js: {required_node}.")
            if current_node:
                version_bits.append(f"Current Node.js: {current_node}.")
            details = " ".join(version_bits) + " " + details

        return {
            "category": "node_version_mismatch",
            "title": "Node.js version mismatch",
            "hint": "This repo requires a different Node.js version than the one currently available.",
            "details": details,
            "suggested_commands": [
                "node -v",
                "find . -maxdepth 3 -name package.json -print",
                "cat package.json",
            ],
            "auto_fix": _build_auto_fix(
                strategy="inspect_required_runtime",
                summary="Inspect the current Node.js version and compare it with root and nested package.json engine requirements.",
                commands=[
                    "node -v",
                    "find . -maxdepth 3 -name package.json -print",
                    "cat package.json",
                ],
                confidence="high",
            ),
            "confidence": "high",
        }

    # --------------------------------------------------
    # 4) Python version mismatch
    # --------------------------------------------------

    if _contains_any(
        lower,
        [
            "requires python",
            "requires a different python",
            "unsupported python version",
            "python version",
            "not supported on this python",
            "requires-python",
        ],
    ):
        required_python, current_python = _extract_python_versions(combined)

        details = (
            "The current command output suggests the repository requires a different Python version. "
            "Check pyproject.toml, setup.py, setup.cfg, or the error output to confirm the required version "
            "before creating a new virtual environment or reinstalling dependencies."
        )

        if required_python or current_python:
            version_bits = []
            if required_python:
                version_bits.append(f"Required Python: {required_python}.")
            if current_python:
                version_bits.append(f"Current Python: {current_python}.")
            details = " ".join(version_bits) + " " + details

        return {
            "category": "python_version_mismatch",
            "title": "Python version mismatch",
            "hint": "This repo appears to require a newer or different Python version than the one used in the current command.",
            "details": details,
            "suggested_commands": [
                "python3 --version",
                "python3.11 --version",
                "find . -maxdepth 2 \\( -name pyproject.toml -o -name setup.py -o -name setup.cfg \\) -print",
                "cat pyproject.toml",
            ],
            "auto_fix": _build_auto_fix(
                strategy="inspect_required_runtime",
                summary="Inspect the current Python version and compare it with the repository's declared Python requirement before rebuilding the environment.",
                commands=[
                    "python3 --version",
                    "python3.11 --version",
                    "find . -maxdepth 2 \\( -name pyproject.toml -o -name setup.py -o -name setup.cfg \\) -print",
                    "cat pyproject.toml",
                ],
                confidence="high",
            ),
            "confidence": "high",
        }

    # --------------------------------------------------
    # X) Missing config file (e.g. researchclaw)
    # --------------------------------------------------
    if _contains_any(lower, ["no config file found", "config.arc.yaml", "config.yaml"]) and _contains_any(lower, ["researchclaw"]):
        return {
            "category": "missing_config_file",
            "title": "Missing configuration file",
            "hint": "This tool requires a configuration file before it can run.",
            "details": (
                "The command failed because no config file (such as config.arc.yaml) was found. "
                "This is common for CLI tools that require initialization before first use. "
                "Run the init command to generate a default config file, then adjust it if needed."
            ),
            "suggested_commands": [
                "./.venv/bin/python -m researchclaw init",
                "ls -la",
                "cat config.arc.yaml",
                "./.venv/bin/python -m researchclaw doctor",
            ],
            "auto_fix": _build_auto_fix(
                strategy="initialize_project_config",
                summary="Create the required config file using the tool's init command.",
                commands=[
                    "./.venv/bin/python -m researchclaw init",
                ],
                confidence="high",
            ),
            "confidence": "high",
        }

    # --------------------------------------------------
    # X) LLM connectivity mismatch (API key valid but HTTP 401)
    # --------------------------------------------------
    if _contains_any(lower, ["llm endpoint http 401"]) and _contains_any(lower, ["api key accepted"]):
        return {
            "category": "llm_connectivity_mismatch",
            "title": "LLM connectivity check failed despite valid API key",
            "hint": "The API key is valid, but the repo's LLM connectivity check failed.",
            "details": (
                "The environment and API key appear to be correctly configured, but the repository's "
                "internal health check is returning an HTTP 401 error. This may indicate an issue with "
                "how the repo performs its LLM request (e.g. unsupported HTTP method, incorrect headers, "
                "or incompatible client logic), rather than a problem with your setup."
            ),
            "suggested_commands": [
                "echo 'Verify direct API connectivity with a simple request'",
                "echo 'Review repo LLM request implementation'",
            ],
            "auto_fix": _build_auto_fix(
                strategy="diagnostic_only",
                summary="No safe automatic fix is available for this issue because it requires reviewing the repository's internal LLM request logic.",
                commands=[],
                confidence="low",
            ),
            "no_auto_fix_reason": (
                "This issue appears to be inside the repository's own LLM connectivity check, "
                "so Tekarab should not guess a repair command automatically."
            ),
            "confidence": "high",
        }


    # --------------------------------------------------
    # 5) Missing Python module
    # --------------------------------------------------
    missing_module_patterns = [
        r"ModuleNotFoundError:\s+No module named\s+[\"']([^\"']+)[\"']",
        r"No module named\s+[\"']([^\"']+)[\"']",
    ]

    module_name = None
    for pattern in missing_module_patterns:
        match = re.search(pattern, combined, flags=re.IGNORECASE)
        if match:
            module_name = match.group(1).strip()
            break

    if module_name:
        return {
            "category": "missing_python_dependency",
            "title": f"Missing Python dependency: {module_name}",
            "hint": f"The Python module '{module_name}' is not installed in the current environment.",
            "details": "Install dependencies from requirements.txt, pyproject.toml, or install the missing module directly if appropriate.",
            "suggested_commands": [
                "pip3 install -r requirements.txt",
                "pip3 install -e .",
                f"pip3 install {module_name}",
            ],
            "auto_fix": _build_auto_fix(
                strategy="install_python_dependency",
                summary="Try the repo dependency install first, then install the missing module directly if needed.",
                commands=[
                    "pip3 install -r requirements.txt",
                    "pip3 install -e .",
                    f"pip3 install {module_name}",
                ],
                confidence="high",
            ),
            "confidence": "high",
        }

    # --------------------------------------------------
    # 6) Package manager / Corepack mismatch
    # --------------------------------------------------
    package_manager_patterns = [
        'defines "packagemanager": "yarn@pnpm@',
        "defines \"packagemanager\": \"yarn@pnpm@",
        "current global version of yarn is",
        "corepack must currently be enabled",
        "this project\'s package.json defines",
        "this project is configured to use pnpm because",
        'has a "packagemanager" field',
        "has a packageManager field".lower(),
        "configured to use pnpm",
    ]

    if _contains_any(lower, package_manager_patterns):
        return {
            "category": "package_manager_mismatch",
            "title": "Package manager mismatch",
            "hint": "The repository is configured for pnpm/Corepack, but the command used Yarn or an incompatible package manager.",
            "details": "This project should be run with the package manager declared in package.json, or with Corepack enabled first.",
            "suggested_commands": [
                "corepack enable",
                "pnpm install",
                "pnpm dev",
                "pnpm build",
            ],
            "auto_fix": _build_auto_fix(
                strategy="switch_package_manager",
                summary="Enable Corepack or install dependencies with pnpm before retrying the original command.",
                commands=[
                    "corepack enable",
                    "pnpm install",
                ],
                confidence="high",
            ),
            "confidence": "high",
        }


    # --------------------------------------------------
    # 6) Missing Node module
    # --------------------------------------------------
    node_module_match = re.search(
        r"Cannot find module ['\"]([^'\"]+)['\"]",
        combined,
        flags=re.IGNORECASE,
    )
    if node_module_match:
        module_name = node_module_match.group(1).strip()
        return {
            "category": "missing_node_dependency",
            "title": f"Missing Node dependency: {module_name}",
            "hint": f"The Node module '{module_name}' could not be resolved.",
            "details": "The repo dependencies may not be installed yet, or installation failed earlier.",
            "suggested_commands": [
                "npm install",
                "pnpm install",
                "yarn install",
            ],
            "auto_fix": _build_auto_fix(
                strategy="install_node_dependencies",
                summary="Install project dependencies before retrying the command.",
                commands=[
                    "npm install",
                ],
                confidence="medium",
            ),
            "confidence": "high",
        }

    # --------------------------------------------------
    # 7) Missing file / wrong working directory
    # --------------------------------------------------
    file_missing_patterns = [
        r"(?:can't open file)\s+[\"']([^\"']+)[\"']",
        r"(?:no such file or directory)\s*[: ]\s*[\"']?([^\"'\n]+)[\"']?",
    ]

    missing_path = None
    for pattern in file_missing_patterns:
        match = re.search(pattern, combined, flags=re.IGNORECASE)
        if match:
            missing_path = match.group(1).strip()
            break

    if missing_path:
        return {
            "category": "missing_file",
            "title": "Missing file or wrong working directory",
            "hint": f"The command is trying to access '{missing_path}', but that file was not found.",
            "details": "This usually means the command was run from the wrong directory, the filename is wrong, or the repo setup step did not complete.",
            "suggested_commands": [
                "pwd",
                "ls",
                "find . -maxdepth 3 -type f",
            ],
            "auto_fix": _build_auto_fix(
                strategy="inspect_paths_and_working_directory",
                summary="Check the current working directory and confirm the target file exists.",
                commands=[
                    "pwd",
                    "ls",
                    "find . -maxdepth 3 -type f",
                ],
                confidence="high",
            ),
            "confidence": "high",
        }

    # --------------------------------------------------
    # 8) Permission denied
    # --------------------------------------------------
    if "permission denied" in lower:
        return {
            "category": "permission_denied",
            "title": "Permission denied",
            "hint": "The command tried to access a file or execute something without the required permissions.",
            "details": "The file may need executable permission, or the command may be targeting a protected path.",
            "suggested_commands": [
                "ls -la",
                "chmod +x ./script.sh",
            ],
            "auto_fix": _build_auto_fix(
                strategy="inspect_permissions",
                summary="Inspect file permissions, then add execute permission if appropriate.",
                commands=[
                    "ls -la",
                    "chmod +x ./script.sh",
                ],
                confidence="medium",
            ),
            "confidence": "medium",
        }

    # --------------------------------------------------
    # 9) Port already in use
    # --------------------------------------------------
    if _contains_any(lower, ["address already in use", "port is already allocated", "eaddrinuse"]):
        return {
            "category": "port_in_use",
            "title": "Port already in use",
            "hint": "The app tried to bind to a port that is already being used inside the session.",
            "details": "Use a different port or stop the previous process first.",
            "suggested_commands": [
                "lsof -i -P -n | grep LISTEN",
                "ss -ltnp",
            ],
            "auto_fix": _build_auto_fix(
                strategy="inspect_listening_ports",
                summary="Inspect listening ports, then free the conflicting port or choose another one.",
                commands=[
                    "ss -ltnp",
                    "lsof -i -P -n | grep LISTEN",
                ],
                confidence="high",
            ),
            "confidence": "high",
        }

    # --------------------------------------------------
    # 10) Git / repo access problems
    # --------------------------------------------------
    if _contains_any(lower, ["repository not found", "authentication failed", "could not read from remote repository"]):
        return {
            "category": "git_access_error",
            "title": "Git repository access failed",
            "hint": "The repo could not be fetched or accessed successfully.",
            "details": "The repository may be private, the URL may be wrong, or credentials may be missing.",
            "suggested_commands": [],
            "auto_fix": _build_auto_fix(
                strategy="verify_repo_access",
                summary="Check the repository URL and credentials, then retry the clone or fetch operation.",
                commands=[],
                confidence="medium",
            ),
            "confidence": "high",
        }

    # --------------------------------------------------
    # 11) Generic fallback
    # --------------------------------------------------
    if exit_code not in (0, None):
        return {
            "category": "generic_command_failure",
            "title": "Command failed",
            "hint": "The command exited with a non-zero status, but no specific smart hint pattern matched.",
            "details": "Check stderr/stdout for the exact failure details.",
            "suggested_commands": [],
            "auto_fix": _build_auto_fix(
                strategy="inspect_logs",
                summary="Review stdout and stderr, then retry with a more specific diagnostic command.",
                commands=[],
                confidence="low",
            ),
            "confidence": "low",
        }

    return None
