from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List
import re


ALLOWED_REPO_TYPES = {
    "cli_app",
    "web_api",
    "web_app",
    "library",
    "framework_source",
    "plugin_content",
    "template_repo",
    "script_collection",
    "ml_experiment",
}

ALLOWED_EXECUTION_READINESS = {
    "ready",
    "needs_env",
    "needs_command_rewrite",
    "no_run",
    "unclear",
    "unsupported",
}

ALLOWED_SUPPORT_TIERS = {
    "fully_supported",
    "partially_supported",
    "unsupported",
}

ALLOWED_RISK_LEVELS = {
    "low",
    "medium",
    "high",
}

ALLOWED_CONFIDENCE = {
    "low",
    "medium",
    "high",
}

IGNORED_ENV_PATTERNS = [
    r"^CI$",
    r"^GITHUB_",
    r"^GITLAB_",
    r"^PYTEST_",
    r"^TEST",
    r"^DEBUG$",
    r"^ENV$",
    r"^HOME$",
    r"^PATH$",
    r"^PWD$",
    r"^SHELL$",
    r"^USER$",
    r"^USERNAME$",
    r"^TZ$",
    r"^LANG$",
    r"^LC_",
    r"^TERM$",
    r"^PORT$",
    r"^HOST$",
    r"^GUM_",
]

EXTERNAL_SERVICE_PATTERNS = {
    "openai": [r"OPENAI_API_KEY", r"\bOPENAI\b"],
    "anthropic": [r"ANTHROPIC_API_KEY", r"\bANTHROPIC\b"],
    "google_ai": [r"GEMINI_API_KEY", r"GOOGLE_API_KEY", r"VERTEX", r"GOOGLE_GENAI"],
    "openrouter": [r"OPENROUTER_API_KEY", r"\bOPENROUTER\b"],
    "deepseek": [r"DEEPSEEK_API_KEY", r"\bDEEPSEEK\b"],
    "aws": [r"AWS_", r"\bS3\b", r"\bDYNAMODB\b", r"\bSES\b", r"\bSQS\b", r"\bBEDROCK\b"],
    "azure": [r"AZURE_"],
    "gcp": [r"\bGCP\b", r"GOOGLE_CLOUD", r"FIREBASE"],
    "database": [r"DATABASE_URL", r"\bPOSTGRES\b", r"\bMYSQL\b", r"\bMONGO\b", r"\bREDIS\b", r"DB_"],
    "stripe": [r"STRIPE_API_KEY", r"\bSTRIPE\b"],
    "twilio": [r"TWILIO_ACCOUNT_SID", r"TWILIO_AUTH_TOKEN", r"\bTWILIO\b"],
    "slack": [r"SLACK_BOT_TOKEN", r"SLACK_APP_TOKEN", r"\bSLACK\b"],
    "github": [r"GITHUB_TOKEN"],
    "huggingface": [r"HUGGINGFACE", r"HF_TOKEN"],
}


@dataclass
class RepoDecision:
    repo_url: str
    detected_language: str
    repo_type_guess: str
    execution_readiness: str
    support_tier: str
    risk_level: str
    confidence_overall: str
    required_env_vars: List[str]
    external_services_detected: List[str]
    entry_candidates: List[str]
    pending_requirements: List[str]
    needs_env_vars: bool
    needs_command_rewrite: bool
    rewrite_candidates: List[str]
    recommended_plan: Dict[str, List[str]]
    selected_package_manager: str
    selected_install_command: str
    selected_build_command: str
    selected_run_command: str
    launch_strategy_order: List[str]
    fallback_plan: List[Dict[str, Any]]
    strategy_rationale: List[str]
    warnings: List[str]
    confidence: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "repo_url": self.repo_url,
            "detected_language": self.detected_language,
            "repo_type_guess": self.repo_type_guess,
            "execution_readiness": self.execution_readiness,
            "support_tier": self.support_tier,
            "risk_level": self.risk_level,
            "confidence_overall": self.confidence_overall,
            "required_env_vars": self.required_env_vars,
            "external_services_detected": self.external_services_detected,
            "entry_candidates": self.entry_candidates,
            "pending_requirements": self.pending_requirements,
            "needs_env_vars": self.needs_env_vars,
            "needs_command_rewrite": self.needs_command_rewrite,
            "rewrite_candidates": self.rewrite_candidates,
            "recommended_plan": self.recommended_plan,
            "selected_package_manager": self.selected_package_manager,
            "selected_install_command": self.selected_install_command,
            "selected_build_command": self.selected_build_command,
            "selected_run_command": self.selected_run_command,
            "launch_strategy_order": self.launch_strategy_order,
            "fallback_plan": self.fallback_plan,
            "strategy_rationale": self.strategy_rationale,
            "warnings": self.warnings,
            "confidence": self.confidence,
        }


def _normalize_str_list(value: Any) -> List[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _unique_keep_order(items: List[str]) -> List[str]:
    seen = set()
    out = []
    for item in items:
        if item not in seen:
            out.append(item)
            seen.add(item)
    return out


def filter_env_vars(env_vars: List[str]) -> List[str]:
    filtered = []
    for env in _normalize_str_list(env_vars):
        ignore = False
        for pattern in IGNORED_ENV_PATTERNS:
            if re.search(pattern, env, flags=re.IGNORECASE):
                ignore = True
                break
        if not ignore:
            filtered.append(env)
    return _unique_keep_order(filtered)


def detect_external_services(env_vars: List[str], readme_samples: List[str], key_files: List[str]) -> List[str]:
    env_haystack = " ".join(_normalize_str_list(env_vars)).upper()
    text_haystack = " ".join(
        _normalize_str_list(readme_samples) +
        _normalize_str_list(key_files)
    ).upper()

    detected = []
    for service, patterns in EXTERNAL_SERVICE_PATTERNS.items():
        if any(re.search(pattern, env_haystack, flags=re.IGNORECASE) for pattern in patterns):
            detected.append(service)
            continue

        strong_text_patterns = [
            pattern for pattern in patterns
            if "_" in pattern or "TOKEN" in pattern or "KEY" in pattern or "URL" in pattern
        ]
        if strong_text_patterns and any(re.search(pattern, text_haystack, flags=re.IGNORECASE) for pattern in strong_text_patterns):
            detected.append(service)

    return detected


def _has_any(texts: List[str] | str, needles: List[str]) -> bool:
    if isinstance(texts, str):
        blob = texts.lower()
    else:
        blob = " ".join(_normalize_str_list(texts)).lower()
    return any(needle.lower() in blob for needle in needles)


def _count_matches(texts: List[str], needles: List[str]) -> int:
    blob = " ".join(_normalize_str_list(texts)).lower()
    count = 0
    for needle in needles:
        if needle.lower() in blob:
            count += 1
    return count


def _looks_like_setup_command(cmd: str) -> bool:
    cmd = (cmd or "").strip().lower()
    setup_prefixes = (
        "pip install",
        "python -m venv",
        "python3 -m venv",
        "source .venv/bin/activate",
        "poetry install",
        "npm install",
        "npm ci",
        "pnpm install",
        "yarn install",
        "go mod download",
        "go mod tidy",
    )
    return cmd.startswith(setup_prefixes)


def _extract_run_commands(readme_samples: List[str]) -> List[str]:
    commands = []
    for cmd in _normalize_str_list(readme_samples):
        if not _looks_like_setup_command(cmd):
            commands.append(cmd)
    return _unique_keep_order(commands)


def _normalize_run_commands(raw: Dict[str, Any]) -> List[str]:
    commands = _unique_keep_order(_normalize_str_list(raw.get("run_commands")))
    filtered: List[str] = []

    for cmd in commands:
        lower = cmd.strip().lower()
        if not lower:
            continue
        if _looks_like_setup_command(lower):
            continue
        filtered.append(cmd.strip())

    return _unique_keep_order(filtered)


def _get_interactive_analysis(raw: Dict[str, Any]) -> Dict[str, Any]:
    value = raw.get("interactive_risks")
    if isinstance(value, dict):
        return value
    return {}


def _get_primary_interactive_items(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
    interactive = _get_interactive_analysis(raw)
    primary = interactive.get("primary_execution_path", {})
    items = primary.get("items", [])
    if isinstance(items, list):
        return [item for item in items if isinstance(item, dict)]
    return []


def _has_rewriteable_interactive_init(raw: Dict[str, Any]) -> bool:
    for item in _get_primary_interactive_items(raw):
        if not item.get("detected"):
            continue
        if item.get("type") != "init_command":
            continue
        if item.get("rewrite_candidate") is True:
            return True
    return False


def _has_hard_interactive_blocker(raw: Dict[str, Any]) -> bool:
    for item in _get_primary_interactive_items(raw):
        if not item.get("detected"):
            continue

        item_type = str(item.get("type") or "").strip().lower()
        strategy = str(item.get("suggested_strategy") or "").strip().lower()

        if item_type == "interactive_shell":
            return True

        if strategy == "reject":
            return True

    return False


def _collect_interactive_rewrite_commands(raw: Dict[str, Any]) -> List[str]:
    commands: List[str] = []
    for item in _get_primary_interactive_items(raw):
        if not item.get("detected"):
            continue
        if item.get("rewrite_candidate") is not True:
            continue

        cmd = str(item.get("command") or "").strip()
        if cmd:
            commands.append(cmd)

    return _unique_keep_order(commands)


def _looks_like_static_web_app(raw: Dict[str, Any]) -> bool:
    key_files = [str(x).strip().replace("\\", "/").lower() for x in _normalize_str_list(raw.get("key_files"))]
    entry_candidates = [str(x).strip().replace("\\", "/").lower() for x in _normalize_str_list(raw.get("entry_candidates"))]
    readme_samples = [str(x).strip().lower() for x in _normalize_str_list(raw.get("readme_command_samples"))]
    readme_preview = str(raw.get("readme_preview") or "").strip().lower()
    run_commands = [str(x).strip().lower() for x in _normalize_run_commands(raw)]
    detected_language = str(raw.get("detected_language", "")).strip().lower()

    all_paths = key_files + entry_candidates
    all_text = " ".join(all_paths + readme_samples + run_commands + [readme_preview])

    has_node_manifest = any(path.endswith("package.json") for path in key_files)
    has_python_manifest = any(path.endswith(name) for path in key_files for name in ("pyproject.toml", "requirements.txt", "setup.py"))
    has_go_manifest = any(path.endswith(name) for path in key_files for name in ("go.mod", "go.sum"))

    has_static_entry = any(
        path.endswith(name)
        for path in all_paths
        for name in (
            "index.html",
            "home.html",
            "public/index.html",
            "dist/index.html",
        )
    )

    has_static_assets = any(
        marker in all_text
        for marker in (
            ".html",
            "assets/",
            "static/",
            "css/",
            "js/",
            "images/",
            "img/",
            "fonts/",
        )
    )

    has_static_text_signal = any(
        marker in all_text
        for marker in (
            "static site",
            "static web",
            "frontend",
            "web app",
            "website",
            "web ui",
            "html",
            "css",
            "javascript",
            "design.md",
            "google stitch",
            "design system document",
            "design agents",
            "pixel-perfect ui",
        )
    )

    has_http_server_run = any(
        cmd.startswith("python -m http.server")
        or cmd.startswith("python3 -m http.server")
        for cmd in run_commands
    )

    has_design_md_repo_signal = "design.md" in readme_preview and any(
        marker in readme_preview
        for marker in (
            "google stitch",
            "design system document",
            "design agents",
            "website",
            "ui",
        )
    )

    if has_node_manifest or has_python_manifest or has_go_manifest:
        return False

    if has_http_server_run and detected_language in {"javascript", "typescript", "html"}:
        return True

    if has_http_server_run and (has_static_entry or has_static_assets or has_static_text_signal):
        return True

    if detected_language in {"javascript", "typescript"} and has_static_entry and has_static_assets and not run_commands:
        return True

    if detected_language in {"unknown", "html", ""} and has_design_md_repo_signal:
        return True

    if detected_language in {"unknown", "html", ""} and has_static_text_signal and not run_commands:
        return True

    return False


def guess_repo_type(raw: Dict[str, Any]) -> str:
    language = str(raw.get("detected_language", "")).lower()
    key_files = _normalize_str_list(raw.get("key_files"))
    entry_candidates = _normalize_str_list(raw.get("entry_candidates"))
    readme_samples = _normalize_str_list(raw.get("readme_command_samples"))
    run_commands = _normalize_run_commands(raw)
    python_scripts = _normalize_str_list(list((raw.get("python_scripts") or {}).keys()))
    repo_name = str(raw.get("repo_name", "")).lower()
    repo_url = str(raw.get("repo_url", "")).lower()

    file_blob = " ".join(key_files + entry_candidates).lower()
    readme_blob = " ".join(readme_samples).lower()
    run_blob = " ".join(run_commands + python_scripts).lower()
    combined = f"{language} {repo_name} {repo_url} {file_blob} {readme_blob} {run_blob}"

    framework_markers = [
        "cpython", "django", "flask", "fastapi", "pytorch", "tensorflow",
        "transformers", "scikit-learn", "numpy", "pandas",
    ]

    plugin_content_markers = [
        ".claude-plugin/",
        ".claude-plugin/plugin.json",
        ".claude-plugin/marketplace.json",
        "plugin.json",
        "marketplace.json",
        "slash command",
        "slash commands",
        "claude plugin",
        "claude-code",
        "claude code",
        "plugin marketplace",
        "skills",
    ]

    template_markers = [
        "template",
        "starter",
        "boilerplate",
        "scaffold",
        "cookiecutter",
        "starter kit",
        "starter-kit",
        "use this template",
        "template repository",
        "create a new project",
        "customize this template",
        "rename this project",
    ]

    has_plugin_manifest = _has_any(key_files + entry_candidates, [
        ".claude-plugin/",
        ".claude-plugin/plugin.json",
        ".claude-plugin/marketplace.json",
        "plugin.json",
        "marketplace.json",
    ])
    has_plugin_text_signal = _has_any(key_files + entry_candidates + readme_samples + run_commands, plugin_content_markers)
    has_runtime_manifest = _has_any(key_files, [
        "package.json",
        "pyproject.toml",
        "requirements.txt",
        "setup.py",
        "dockerfile",
        "docker-compose.yml",
        "go.mod",
        "go.sum",
    ])

    has_template_name_signal = _has_any([repo_name, repo_url], [
        "template",
        "starter",
        "boilerplate",
        "scaffold",
        "cookiecutter",
    ])
    has_template_text_signal = _has_any(
        key_files + entry_candidates + readme_samples + run_commands,
        template_markers,
    )

    if has_plugin_manifest and has_plugin_text_signal and not has_runtime_manifest and not entry_candidates and not run_commands:
        return "plugin_content"

    if (has_template_name_signal or has_template_text_signal) and (has_runtime_manifest or entry_candidates or run_commands or python_scripts):
        return "template_repo"

    if "streamlit" in combined:
        return "web_app"

    if _looks_like_static_web_app(raw):
        return "web_app"

    if any(marker in combined for marker in framework_markers):
        if "cpython" in combined:
            return "framework_source"

    if _has_any(key_files, ["package.json"]) and _has_any(
        key_files + readme_samples + run_commands,
        [
            "next",
            "react",
            "vite",
            "frontend",
            "web app",
            "astro",
            "svelte",
            "vue",
            "homepage",
            "app/",
            "src/app",
            "src/pages",
            "pages/",
            "pnpm",
            "workspace",
            "turbo",
            "npm run dev",
            "npm start",
            "pnpm dev",
            "pnpm start",
            "yarn dev",
            "yarn start",
            "bun.lock",
            "bun.lockb",
            "bun dev",
            "bun start",
            "bun run dev",
            "bun run preview",
        ]
    ):
        return "web_app"

    cli_score = 0
    web_api_score = 0

    cli_file_markers = [
        "cli.py",
        "__main__.py",
        "main.go",
        "cmd/",
    ]
    cli_text_markers = [
        "argparse",
        "click",
        "typer",
        "console_scripts",
        "python -m",
        "go run",
        "command line",
        "cli",
        "--help",
        "litellm",
    ]

    web_api_markers = [
        "fastapi",
        "flask",
        "uvicorn",
        "gunicorn",
        "swagger",
        "openapi",
        "rest api",
        "api server",
        "src/api/",
        "api/",
        "routes.py",
        "schemas.py",
        "services.py",
        "repositories.py",
        "main.py",
        "app.py",
        "proxy",
        "server",
    ]

    cli_score += _count_matches(entry_candidates, cli_file_markers) * 3
    cli_score += _count_matches(entry_candidates + readme_samples + key_files + run_commands + python_scripts, cli_text_markers)

    if any(cmd.strip().startswith(("python ", "python3 ", "python -m", "python3 -m", "go run ")) for cmd in readme_samples + run_commands):
        cli_score += 1

    web_api_score += _count_matches(entry_candidates + readme_samples + key_files + run_commands + python_scripts, web_api_markers)

    if _has_any(entry_candidates, ["main.py", "app.py", "server.py"]) and _has_any(readme_samples + key_files + run_commands, ["uvicorn", "gunicorn", "fastapi", "flask"]):
        web_api_score += 2

    if _has_any(entry_candidates, ["src/api/", "api/", "routes.py", "schemas.py", "services.py", "repositories.py"]):
        web_api_score += 2

    if _has_any(entry_candidates, ["src/main.py", "main.py", "app.py"]) and _has_any(entry_candidates, ["src/api/", "api/"]):
        web_api_score += 2

    if cli_score >= 3 and cli_score >= web_api_score:
        return "cli_app"

    if web_api_score >= 2:
        return "web_api"

    if _has_any(
        key_files + readme_samples + entry_candidates,
        ["notebook", ".ipynb", "experiment", "train.py", "inference.py", "inference/", "generate.py", "checkpoint", "model weights", "torchrun"],
    ):
        return "ml_experiment"

    if _has_any(key_files + readme_samples, ["requirements.txt", "pyproject.toml", "setup.py"]) and _has_any(readme_samples + run_commands, ["pip install", "import ", "library"]):
        if not entry_candidates and not run_commands:
            return "library"

    if len(entry_candidates) >= 3 and _has_any(entry_candidates, ["scripts/", ".sh", ".py", ".go"]) and web_api_score < 2:
        return "script_collection"

    if entry_candidates or run_commands or python_scripts:
        return "cli_app"

    return "unclear"


def _build_pending_requirements(
    repo_type: str,
    env_vars: List[str],
    raw_analysis: Dict[str, Any],
) -> List[str]:
    pending: List[str] = []

    if repo_type in {"framework_source", "library", "plugin_content"}:
        pending.append("no_run")

    if repo_type in {"unclear", "template_repo"}:
        pending.append("manual_review")

    if repo_type not in ALLOWED_REPO_TYPES and repo_type != "unclear":
        pending.append("unsupported_repo_type")

    if _has_hard_interactive_blocker(raw_analysis):
        pending.append("hard_interactive_blocker")

    if env_vars:
        pending.append("env_vars")

    if _has_rewriteable_interactive_init(raw_analysis):
        pending.append("command_rewrite")

    return _unique_keep_order(pending)


def determine_execution_readiness(
    repo_type: str,
    env_vars: List[str],
    entry_candidates: List[str],
    raw_analysis: Dict[str, Any],
) -> str:
    key_files = [str(x).strip().lower() for x in _normalize_str_list(raw_analysis.get("key_files"))]
    readme_samples = [str(x).strip().lower() for x in _normalize_str_list(raw_analysis.get("readme_command_samples"))]
    raw_run_commands = [str(x).strip().lower() for x in _normalize_run_commands(raw_analysis)]
    detected_language = str(raw_analysis.get("detected_language", "")).strip().lower()
    python_scripts = raw_analysis.get("python_scripts", {}) or {}
    has_python_script_entries = isinstance(python_scripts, dict) and bool(python_scripts)

    has_node_manifest = any(path.endswith("package.json") for path in key_files)
    has_python_manifest = any(
        path.endswith(name)
        for path in key_files
        for name in ("pyproject.toml", "requirements.txt", "setup.py")
    )
    has_go_manifest = any(
        path.endswith(name)
        for path in key_files
        for name in ("go.mod", "go.sum")
    )
    has_run_signal_in_readme = any(
        token in sample
        for sample in readme_samples
        for token in (
            "pnpm ",
            "npm run",
            "yarn ",
            "python ",
            "python3 ",
            "uv run",
            "streamlit run",
            "streamlit hello",
            "flask run",
            "uvicorn ",
            "gunicorn ",
            "node ",
            "go run",
            "go build",
            "go install",
        )
    )
    has_run_commands = bool(raw_run_commands)

    if repo_type in {"framework_source", "library", "plugin_content"}:
        return "no_run"

    if _has_hard_interactive_blocker(raw_analysis):
        return "unsupported"

    if env_vars:
        return "needs_env"

    if _has_rewriteable_interactive_init(raw_analysis):
        return "needs_command_rewrite"

    if repo_type == "template_repo":
        return "unclear"

    if repo_type == "unclear":
        if detected_language in {"python", "javascript", "typescript", "go"} and (
            has_run_commands
            or has_run_signal_in_readme
            or has_node_manifest
            or has_python_manifest
            or has_go_manifest
            or has_python_script_entries
            or entry_candidates
        ):
            return "ready"
        return "unclear"

    if repo_type not in ALLOWED_REPO_TYPES:
        return "unsupported"

    if repo_type in {"cli_app", "web_api", "script_collection", "ml_experiment"}:
        if has_run_commands or entry_candidates or has_python_script_entries:
            return "ready"
        if detected_language == "go" and has_go_manifest:
            return "ready"

    if repo_type == "web_app":
        if has_run_commands or entry_candidates or has_run_signal_in_readme or has_node_manifest or _looks_like_static_web_app(raw_analysis):
            return "ready"

    if repo_type in {"script_collection", "ml_experiment"} and not entry_candidates and not has_run_commands:
        if has_run_signal_in_readme or has_node_manifest or has_python_manifest or has_go_manifest or has_python_script_entries:
            return "ready"
        return "unclear"

    return "unclear"


def determine_support_tier(
    repo_type: str,
    language: str,
    execution_readiness: str = "unclear",
    raw_analysis: Dict[str, Any] | None = None,
) -> str:
    language = (language or "").lower()
    raw_analysis = raw_analysis or {}
    inferred_run_steps = _default_run_steps(raw_analysis, repo_type) if raw_analysis else []
    has_run_steps = bool(inferred_run_steps)

    if repo_type in {"cli_app", "web_api", "script_collection"} and language in {"python", "javascript", "typescript"}:
        return "fully_supported"

    if repo_type in {"cli_app", "script_collection"} and language == "go":
        if execution_readiness == "ready" and has_run_steps:
            return "fully_supported"
        return "partially_supported"

    if repo_type == "web_api" and language == "go":
        if execution_readiness == "ready" and has_run_steps:
            return "partially_supported"
        return "partially_supported"

    if repo_type == "web_app" and language in {"python", "javascript", "typescript"}:
        if execution_readiness == "ready" and has_run_steps:
            return "fully_supported"
        if language == "python":
            return "fully_supported"
        return "partially_supported"

    if repo_type == "ml_experiment" and language in {"python", "javascript", "typescript", "go"}:
        return "partially_supported"

    if repo_type == "template_repo":
        return "partially_supported"

    if repo_type in {"library", "framework_source", "plugin_content"}:
        return "unsupported"

    return "partially_supported"


def determine_risk_level(
    repo_type: str,
    env_vars: List[str],
    external_services: List[str],
    raw_analysis: Dict[str, Any],
) -> str:
    if repo_type in {"framework_source", "library", "plugin_content"}:
        return "low"

    if _has_hard_interactive_blocker(raw_analysis):
        return "high"

    if _has_rewriteable_interactive_init(raw_analysis):
        return "medium"

    if any(service in external_services for service in ["aws", "database", "stripe"]):
        return "high"

    if env_vars or external_services:
        return "medium"

    return "low"


def determine_confidence(repo_type: str, entry_candidates: List[str], key_files: List[str], readme_samples: List[str]) -> str:
    score = 0

    if repo_type in ALLOWED_REPO_TYPES:
        score += 2
    if entry_candidates:
        score += 1
    if key_files:
        score += 1
    if readme_samples:
        score += 1

    if score >= 4:
        return "high"
    if score >= 2:
        return "medium"
    return "low"


def _python_setup_steps(raw: Dict[str, Any]) -> List[str]:
    key_files = _normalize_str_list(raw.get("key_files"))
    steps = [
        "python3 -m venv .venv",
        "source .venv/bin/activate",
        "pip install -U pip",
    ]

    normalized_key_files = {str(path).strip().replace("\\", "/").lower() for path in key_files}

    if "requirements.txt" in normalized_key_files:
        steps.append("pip install -r requirements.txt")

    if "pyproject.toml" in normalized_key_files or "setup.py" in normalized_key_files:
        steps.append("pip install -e .")
    elif "requirements.txt" not in normalized_key_files:
        steps.append("pip install -e .")

    return steps

def _javascript_setup_steps(raw: Dict[str, Any]) -> List[str]:
    key_files = [str(x).strip().replace("\\", "/").lower() for x in _normalize_str_list(raw.get("key_files"))]
    package_scripts = raw.get("package_scripts", {}) or {}

    if _looks_like_static_web_app(raw) and not any(path.endswith("package.json") for path in key_files):
        return []

    detected_package_manager = str(package_scripts.get("_detected_package_manager") or "").strip().lower()

    if detected_package_manager.startswith("bun@") or detected_package_manager == "bun":
        return ["bun install"]
    if detected_package_manager.startswith("pnpm@") or detected_package_manager == "pnpm":
        return ["pnpm install"]
    if detected_package_manager.startswith("yarn@") or detected_package_manager == "yarn":
        return ["yarn install"]
    if detected_package_manager.startswith("npm@") or detected_package_manager == "npm":
        return ["npm install"]

    if "bun.lock" in key_files or "bun.lockb" in key_files:
        return ["bun install"]
    if "package-lock.json" in key_files:
        return ["npm ci"]
    if "pnpm-lock.yaml" in key_files:
        return ["pnpm install"]
    if "yarn.lock" in key_files:
        return ["yarn install"]

    if any(path.endswith("package.json") for path in key_files):
        return ["npm install"]

    return []


def _go_setup_steps(raw: Dict[str, Any]) -> List[str]:
    key_files = [str(x).strip().replace("\\", "/").lower() for x in _normalize_str_list(raw.get("key_files"))]

    if "go.mod" in key_files:
        return ["go mod download"]

    if "go.sum" in key_files:
        return ["go mod download"]

    return ["# Inspect Go dependency installation manually"]


def _candidate_commands(raw: Dict[str, Any], field_name: str) -> List[Dict[str, Any]]:
    value = raw.get(field_name)
    if not isinstance(value, list):
        return []

    candidates: List[Dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        command = str(item.get("command") or "").strip()
        if not command:
            continue
        candidates.append({
            "command": command,
            "confidence": str(item.get("confidence") or "low").strip().lower(),
            "reason": str(item.get("reason") or "").strip(),
        })
    return candidates


def _build_strategy_bundle(
    raw: Dict[str, Any],
    repo_type: str,
    execution_readiness: str,
) -> Dict[str, Any]:
    detected_language = str(raw.get("detected_language") or "").strip().lower()
    preferred_pm = raw.get("preferred_package_manager") or {}
    default_pm = _detect_js_package_manager_for_decision(raw) if detected_language in {"javascript", "typescript"} else ""
    selected_package_manager = str(preferred_pm.get("name") or default_pm).strip().lower()
    install_candidates = _candidate_commands(raw, "install_strategy_candidates")
    build_candidates = _candidate_commands(raw, "build_strategy_candidates")
    run_candidates = _candidate_commands(raw, "run_strategy_candidates")
    preview_candidates = _candidate_commands(raw, "preview_strategy_candidates")
    production_preview = raw.get("production_preview_readiness") or {}
    mismatch_items = raw.get("config_package_mismatches") or []

    warnings: List[str] = []
    for item in mismatch_items:
        if not isinstance(item, dict):
            continue
        message = str(item.get("message") or "").strip()
        if message:
            warnings.append(message)

    strategy_rationale = list(_unique_keep_order(_normalize_str_list(preferred_pm.get("reasons"))))
    preview_level = str(production_preview.get("level") or "low").strip().lower()
    selected_install_command = install_candidates[0]["command"] if install_candidates else ""
    selected_build_command = build_candidates[0]["command"] if build_candidates else ""
    selected_preview_command = preview_candidates[0]["command"] if preview_candidates else ""
    selected_run_candidate = run_candidates[0]["command"] if run_candidates else ""

    prefer_build_preview = (
        repo_type == "web_app"
        and execution_readiness == "ready"
        and preview_level == "high"
        and bool(selected_build_command)
        and bool(selected_preview_command or selected_run_candidate)
    )

    if prefer_build_preview:
        selected_run_command = selected_preview_command or selected_run_candidate
        launch_strategy_order = ["build_preview", "dev_fallback", "explore"]
        strategy_rationale.append("Production preview readiness is high, so build+preview is preferred.")
    elif selected_run_candidate:
        selected_run_command = selected_run_candidate
        launch_strategy_order = ["direct_run", "build_preview", "explore"]
        strategy_rationale.append("A direct run candidate is available and safer than exploratory fallback.")
    elif selected_preview_command:
        selected_run_command = selected_preview_command
        launch_strategy_order = ["preview_only", "explore"]
        strategy_rationale.append("Preview strategy is available even though a direct run candidate is weaker.")
    else:
        selected_run_command = ""
        launch_strategy_order = ["explore"]
        strategy_rationale.append("No strong launch strategy was available; keep the repo in exploratory mode.")

    fallback_plan: List[Dict[str, Any]] = []
    for item in install_candidates[1:3]:
        fallback_plan.append({
            "trigger": "install_failure",
            "command": item["command"],
            "reason": item["reason"] or "Safer install fallback.",
        })

    if selected_build_command and selected_run_candidate and prefer_build_preview:
        fallback_plan.append({
            "trigger": "build_failure",
            "command": selected_run_candidate,
            "reason": "Fallback to dev/start command if production build or preview fails.",
        })

    if selected_preview_command and selected_run_candidate and selected_preview_command != selected_run_candidate:
        fallback_plan.append({
            "trigger": "preview_unavailable",
            "command": selected_run_candidate,
            "reason": "Fallback to the best available direct run command.",
        })

    confidence_overall = "high"
    if repo_type == "unclear" or preview_level == "low":
        confidence_overall = "medium"
    if warnings or execution_readiness in {"unclear", "needs_command_rewrite"}:
        confidence_overall = "medium"
    if repo_type == "unclear" and not selected_run_command:
        confidence_overall = "low"

    return {
        "selected_package_manager": selected_package_manager,
        "selected_install_command": selected_install_command,
        "selected_build_command": selected_build_command,
        "selected_run_command": selected_run_command,
        "launch_strategy_order": launch_strategy_order,
        "fallback_plan": fallback_plan,
        "strategy_rationale": _unique_keep_order(strategy_rationale),
        "warnings": _unique_keep_order(warnings),
        "confidence": {
            "overall": confidence_overall,
            "package_manager": str(preferred_pm.get("confidence") or "low").strip().lower(),
            "production_preview": preview_level or "low",
        },
    }


def _detect_js_package_manager_for_decision(raw: Dict[str, Any]) -> str:
    key_files = [str(x).strip().replace("\\", "/").lower() for x in _normalize_str_list(raw.get("key_files"))]
    package_scripts = raw.get("package_scripts", {}) or {}

    detected_package_manager = str(package_scripts.get("_detected_package_manager") or "").strip().lower()
    if detected_package_manager.startswith("bun@") or detected_package_manager == "bun":
        return "bun"
    if detected_package_manager.startswith("pnpm@") or detected_package_manager == "pnpm":
        return "pnpm"
    if detected_package_manager.startswith("yarn@") or detected_package_manager == "yarn":
        return "yarn"
    if detected_package_manager.startswith("npm@") or detected_package_manager == "npm":
        return "npm"

    if "bun.lock" in key_files or "bun.lockb" in key_files:
        return "bun"
    if "pnpm-lock.yaml" in key_files:
        return "pnpm"
    if "yarn.lock" in key_files:
        return "yarn"
    return "npm"


def _build_package_manager_run_command(
    package_manager: str,
    script_name: str,
    forwarded_args: str = "",
) -> str:
    normalized_package_manager = str(package_manager or "").strip().lower()
    normalized_script_name = str(script_name or "").strip()
    normalized_forwarded_args = str(forwarded_args or "").strip()

    if normalized_package_manager == "bun":
        if normalized_script_name == "start":
            base_command = "bun start"
        else:
            base_command = f"bun run {normalized_script_name}"
    elif normalized_package_manager == "pnpm":
        base_command = f"pnpm {normalized_script_name}"
    elif normalized_package_manager == "yarn":
        base_command = f"yarn {normalized_script_name}"
    else:
        if normalized_script_name == "start":
            base_command = "npm start"
        else:
            base_command = f"npm run {normalized_script_name}"

    if not normalized_forwarded_args:
        return base_command

    if normalized_package_manager in {"npm", "pnpm", "bun"}:
        return f"{base_command} -- {normalized_forwarded_args}"

    return f"{base_command} {normalized_forwarded_args}"


def _package_manager_list_scripts_command(package_manager: str) -> str:
    normalized_package_manager = str(package_manager or "").strip().lower()

    if normalized_package_manager == "bun":
        return "bun run"
    if normalized_package_manager == "pnpm":
        return "pnpm run"
    if normalized_package_manager == "yarn":
        return "yarn run"
    return "npm run"


def _looks_like_vite_web_app(raw: Dict[str, Any], package_scripts: Dict[str, Any]) -> bool:
    key_files = [str(x).strip().lower() for x in _normalize_str_list(raw.get("key_files"))]
    readme_samples = [str(x).strip().lower() for x in _normalize_str_list(raw.get("readme_command_samples"))]
    raw_run_commands = [str(x).strip().lower() for x in _normalize_run_commands(raw)]
    script_names = [str(x).strip().lower() for x in package_scripts.keys()]

    vite_markers = [
        "vite",
        "vite.config",
        "@vitejs/plugin-react",
        "@vitejs/plugin-vue",
        "npm run preview",
        "pnpm preview",
        "yarn preview",
        "bun run preview",
        "vite preview",
    ]

    if any("preview" == name for name in script_names) and any("build" == name for name in script_names):
        if any(marker in " ".join(key_files + readme_samples + raw_run_commands + script_names) for marker in vite_markers):
            return True

    return any(marker in " ".join(key_files + readme_samples + raw_run_commands + script_names) for marker in vite_markers)


def _safe_exploratory_run_steps(raw: Dict[str, Any]) -> List[str]:
    detected_language = str(raw.get("detected_language") or "").strip().lower()
    key_files = _normalize_str_list(raw.get("key_files"))
    entry_candidates = _normalize_str_list(raw.get("entry_candidates"))
    readme_samples = _normalize_str_list(raw.get("readme_command_samples"))
    package_scripts = raw.get("package_scripts", {}) or {}
    python_scripts = raw.get("python_scripts", {}) or {}
    repo_name = str(raw.get("repo_name") or "").strip().lower()
    repo_url = str(raw.get("repo_url") or "").strip().lower()

    normalized_key_files = [str(x).strip().replace("\\", "/").lower() for x in key_files]
    normalized_entries = [str(x).strip().replace("\\", "/") for x in entry_candidates]
    normalized_entries_lower = [x.lower() for x in normalized_entries]

    combined_blob = " ".join(
        normalized_key_files
        + normalized_entries_lower
        + [str(x).strip().lower() for x in readme_samples]
        + [str(x).strip().lower() for x in package_scripts.keys()]
        + [str(x).strip().lower() for x in python_scripts.keys()]
        + [repo_name, repo_url]
    )

    commands: List[str] = []

    static_preview_candidates = [
        path
        for path in normalized_entries + key_files
        if str(path).strip().replace("\\", "/").lower().endswith(".html")
    ]
    preferred_static_preview = ""
    for candidate in static_preview_candidates:
        normalized_candidate = str(candidate).strip().replace("\\", "/")
        lower = normalized_candidate.lower()
        if lower.endswith("/index.html") or lower == "index.html":
            preferred_static_preview = normalized_candidate
            break
        if "preview" in lower:
            preferred_static_preview = normalized_candidate
            break
    if not preferred_static_preview and static_preview_candidates:
        preferred_static_preview = str(static_preview_candidates[0]).strip().replace("\\", "/")

    static_artifact_markers = [
        "design.md",
        "index.html",
        "preview.html",
        "preview/index.html",
        "dist/index.html",
        "build/index.html",
        "site/index.html",
        "public/index.html",
    ]
    looks_like_static_artifacts = (
        bool(preferred_static_preview)
        or any(marker in combined_blob for marker in static_artifact_markers)
    )

    if looks_like_static_artifacts:
        commands.append('find . -maxdepth 3 \\( -name "DESIGN.md" -o -name "preview*.html" -o -name "index.html" \\)')
        commands.append("python3 -m http.server 8080")

    if detected_language in {"javascript", "typescript"}:
        package_manager = _detect_js_package_manager_for_decision(raw)
        is_nx_repo = "nx" in combined_blob
        is_turbo_repo = "turbo" in combined_blob

        if is_nx_repo:
            commands.extend([
                "npx nx --help",
                "npx nx show projects",
            ])

        if is_turbo_repo:
            commands.extend([
                "npx turbo --help",
            ])

        if any(path.endswith("package.json") or path == "package.json" for path in normalized_key_files):
            if not is_nx_repo:
                commands.append(_package_manager_list_scripts_command(package_manager))

        if "dev" in package_scripts:
            commands.append(
                _build_package_manager_run_command(
                    package_manager,
                    "dev",
                    "--host 0.0.0.0",
                )
            )

        if "start" in package_scripts and not is_nx_repo:
            commands.append(_build_package_manager_run_command(package_manager, "start"))

    if detected_language == "python":
        for script_name in python_scripts.keys():
            normalized_script_name = str(script_name).strip()
            if normalized_script_name:
                commands.append(f"{normalized_script_name} --help")

        for candidate in normalized_entries:
            lower = candidate.lower()
            if not lower.endswith(".py"):
                continue
            if lower.startswith("tests/") or "/tests/" in lower or lower.startswith("docs/") or lower.startswith("docs_src/"):
                continue

            if lower.endswith("/cli.py") or lower == "cli.py":
                commands.append(f"python {candidate} --help")
                break

            if lower.endswith("/main.py") or lower == "main.py":
                commands.append(f"python {candidate} --help")
                break

        if not commands and any(path.endswith(name) for path in normalized_key_files for name in ("pyproject.toml", "requirements.txt", "setup.py")):
            commands.append("python -m pip --version")

    if detected_language == "go":
        if "main.go" in normalized_entries_lower or "main.go" in normalized_key_files:
            commands.append("go run . --help")

        if not commands:
            cmd_targets: List[str] = []
            for candidate in normalized_entries:
                lower = candidate.lower()
                if not lower.endswith(".go"):
                    continue
                if not lower.startswith("cmd/"):
                    continue
                parts = lower.split("/")
                if len(parts) >= 3:
                    cmd_targets.append(f"./cmd/{parts[1]}")

            unique_cmd_targets = _unique_keep_order(cmd_targets)
            if unique_cmd_targets:
                commands.append(f"go run {unique_cmd_targets[0]} --help")

    if not commands:
        return []

    return _unique_keep_order(commands)


def _default_run_steps(raw: Dict[str, Any], repo_type: str) -> List[str]:
    raw_run_commands = _normalize_run_commands(raw)
    if raw_run_commands:
        return raw_run_commands

    readme_samples = [str(x).strip().lower() for x in _normalize_str_list(raw.get("readme_command_samples"))]
    package_scripts = raw.get("package_scripts", {}) or {}
    entry_candidates = _normalize_str_list(raw.get("entry_candidates"))
    key_files = _normalize_str_list(raw.get("key_files"))
    detected_language = str(raw.get("detected_language") or "").strip().lower()
    repo_name = str(raw.get("repo_name") or "").strip().lower()
    blob = " ".join(entry_candidates + key_files + readme_samples).lower()

    normalized_key_files = [str(x).strip().replace("\\", "/").lower() for x in key_files]
    normalized_entries = [str(x).strip().replace("\\", "/") for x in entry_candidates]
    normalized_entries_lower = [x.lower() for x in normalized_entries]
    has_package_json = any(path.endswith("package.json") or path == "package.json" for path in normalized_key_files)
    node_like_language = detected_language in {"javascript", "typescript"}
    allow_node_web_run_fallback = node_like_language or _looks_like_vite_web_app(raw, package_scripts)
    package_manager = _detect_js_package_manager_for_decision(raw)

    if repo_type == "web_api":
        uvicorn_markers = ["fastapi", "uvicorn", "src/api/", "api/", "routes.py", "schemas.py", "services.py"]
        flask_markers = ["flask", "app.py", "wsgi.py"]

        if _has_any(blob, uvicorn_markers):
            if "src/main.py" in entry_candidates or "src/main.py" in key_files:
                return ["uvicorn src.main:app --host 0.0.0.0 --port 8000"]
            if "main.py" in entry_candidates or "main.py" in key_files:
                return ["uvicorn main:app --host 0.0.0.0 --port 8000"]
            if "src/app.py" in entry_candidates or "src/app.py" in key_files:
                return ["uvicorn src.app:app --host 0.0.0.0 --port 8000"]
            if "app.py" in entry_candidates or "app.py" in key_files:
                return ["uvicorn app:app --host 0.0.0.0 --port 8000"]
            if "tests/main.py" in entry_candidates or "tests/main.py" in key_files:
                return ["uvicorn tests.main:app --host 0.0.0.0 --port 8000"]

        if _has_any(blob, flask_markers):
            if "src/app.py" in entry_candidates or "src/app.py" in key_files:
                return ["flask --app src.app run --host 0.0.0.0 --port 8000"]
            if "app.py" in entry_candidates or "app.py" in key_files:
                return ["flask --app app run --host 0.0.0.0 --port 8000"]

    if repo_type == "cli_app":
        if detected_language == "go":
            for sample in readme_samples:
                if sample.startswith("go run "):
                    return [sample]
                if sample.startswith(f"{repo_name} ") or sample == repo_name:
                    return [sample]

            if "main.go" in normalized_entries_lower or "main.go" in normalized_key_files:
                return ["go run . --help"]

            cmd_targets: List[str] = []
            for candidate in normalized_entries:
                lower = candidate.lower()
                if not lower.endswith(".go"):
                    continue
                if not lower.startswith("cmd/"):
                    continue
                parts = lower.split("/")
                if len(parts) >= 3:
                    cmd_targets.append(f"./cmd/{parts[1]}")

            unique_cmd_targets = _unique_keep_order(cmd_targets)
            if unique_cmd_targets:
                return [f"go run {unique_cmd_targets[0]} --help"]

            if "go.mod" in normalized_key_files or "go.sum" in normalized_key_files:
                return ["go run . --help"]

        if "typer/__main__.py" in entry_candidates or "typer/__main__.py" in key_files:
            return ["python -m typer --help"]

        if "typer/cli.py" in entry_candidates or "typer/cli.py" in key_files:
            return ["python -m typer.cli --help"]

        flask_repo_markers = [
            "src/flask/app.py",
            "src/flask/__init__.py",
            "src/flask/cli.py",
            "src/flask/__main__.py",
        ]
        if _has_any(entry_candidates + key_files, flask_repo_markers):
            return ["flask --app tests/test_apps/cliapp/app.py:testapp routes"]

        if "__main__.py" in blob:
            for candidate in entry_candidates + key_files:
                normalized = str(candidate).strip().replace("\\", "/")
                if normalized.endswith("/__main__.py"):
                    module_name = normalized[:-12].replace("/", ".").strip(".")
                    if module_name:
                        return [f"python -m {module_name} --help"]
                if normalized == "__main__.py":
                    return ["python __main__.py --help"]

        for candidate in entry_candidates:
            normalized = str(candidate).strip().replace("\\", "/")
            if normalized.endswith("/cli.py"):
                module_name = normalized[:-3].replace("/", ".").strip(".")
                if module_name:
                    return [f"python -m {module_name} --help"]
                return [f"python {normalized} --help"]

        for candidate in entry_candidates:
            normalized = str(candidate).strip().replace("\\", "/")
            if normalized.endswith("/main.py"):
                if normalized.startswith("docs_src/") or "/tests/" in normalized or normalized.startswith("tests/"):
                    continue
                module_name = normalized[:-3].replace("/", ".").strip(".")
                if module_name:
                    return [f"python -m {module_name} --help"]
                return [f"python {normalized} --help"]

    if repo_type == "web_app" and "streamlit" in blob:
        for sample in readme_samples:
            if sample == "streamlit hello":
                return ["STREAMLIT_SERVER_ADDRESS=0.0.0.0 STREAMLIT_SERVER_PORT=8501 streamlit hello"]

        for candidate in entry_candidates + key_files:
            normalized = str(candidate).strip().replace("\\", "/")
            if normalized.endswith(".py") and (
                "streamlit" in normalized.lower()
                or normalized.lower().endswith("/app.py")
                or normalized.lower().endswith("/hello.py")
            ):
                return [
                    f"STREAMLIT_SERVER_ADDRESS=0.0.0.0 STREAMLIT_SERVER_PORT=8501 streamlit run {normalized}"
                ]

    if repo_type == "web_app" and _looks_like_static_web_app(raw):
        for cmd in raw_run_commands:
            lower = cmd.strip().lower()
            if lower.startswith("python -m http.server") or lower.startswith("python3 -m http.server"):
                return [cmd]

        return ["python3 -m http.server 8080"]

    if repo_type == "web_app" and _looks_like_vite_web_app(raw, package_scripts):
        if "build" in package_scripts and "preview" in package_scripts:
            return [
                _build_package_manager_run_command(package_manager, "build"),
                _build_package_manager_run_command(
                    package_manager,
                    "preview",
                    "--host 0.0.0.0 --port 5173",
                ),
            ]

    if allow_node_web_run_fallback:
        for script_name in package_scripts.keys():
            if script_name.startswith("dev:"):
                return [_build_package_manager_run_command(package_manager, script_name)]

        if "dev" in package_scripts:
            return [_build_package_manager_run_command(package_manager, "dev")]

        if "start" in package_scripts:
            return [_build_package_manager_run_command(package_manager, "start")]

        for sample in readme_samples:
            if "npm run dev:" in sample:
                parts = sample.split()
                for token in parts:
                    if token.startswith("dev:"):
                        return [_build_package_manager_run_command(package_manager, token)]

        if any("npm run dev" in s for s in readme_samples):
            return [_build_package_manager_run_command(package_manager, "dev")]

        if repo_type == "web_app" and "preview" in package_scripts and "build" in package_scripts:
            return [
                _build_package_manager_run_command(package_manager, "build"),
                _build_package_manager_run_command(
                    package_manager,
                    "preview",
                    "--host 0.0.0.0 --port 5173",
                ),
            ]

        if repo_type == "web_app" and has_package_json:
            return [_build_package_manager_run_command(package_manager, "start")]

    return []

def build_recommended_plan(
    raw: Dict[str, Any],
    repo_type: str,
    execution_readiness: str,
    required_env_vars: List[str],
    external_services: List[str],
) -> Dict[str, List[str]]:
    language = str(raw.get("detected_language", "")).lower()
    rewrite_candidates = _collect_interactive_rewrite_commands(raw)
    strategy_bundle = _build_strategy_bundle(raw, repo_type, execution_readiness)

    plan = {
        "setup_steps": [],
        "run_steps": [],
        "notes": [],
        "blockers": [],
    }

    if repo_type in {"library", "framework_source"}:
        plan["notes"].append("Repository appears to be source/framework/library code, not an end-user runnable app.")
        plan["blockers"].append(f"Repo type '{repo_type}' is classified as non-runnable for direct execution.")
        return plan

    if repo_type == "plugin_content":
        plan["notes"].append("Repository appears to contain plugin metadata, skills, or content rather than an end-user runnable app.")
        plan["blockers"].append("Repo type 'plugin_content' is classified as non-runnable for direct shell execution.")
        return plan

    if repo_type == "template_repo":
        plan["notes"].append("Repository appears to be a starter/template repo that likely requires project-specific customization before execution.")
        plan["blockers"].append("Template repos require manual setup choices, customization, and app-specific configuration before a safe run path can be inferred.")
        return plan

    selected_install_command = str(strategy_bundle.get("selected_install_command") or "").strip()
    selected_build_command = str(strategy_bundle.get("selected_build_command") or "").strip()
    selected_run_command = str(strategy_bundle.get("selected_run_command") or "").strip()
    launch_strategy_order = strategy_bundle.get("launch_strategy_order") or []

    normalized_key_files = [str(x).strip().replace("\\", "/").lower() for x in _normalize_str_list(raw.get("key_files"))]
    has_package_json = any(path.endswith("package.json") or path == "package.json" for path in normalized_key_files)

    if _looks_like_static_web_app(raw):
        plan["setup_steps"] = []
    elif selected_install_command and has_package_json:
        plan["setup_steps"] = [selected_install_command]
    elif language == "python":
        plan["setup_steps"] = _python_setup_steps(raw)
    elif language in {"javascript", "typescript"}:
        plan["setup_steps"] = _javascript_setup_steps(raw)
    elif language == "go":
        plan["setup_steps"] = _go_setup_steps(raw)
    else:
        plan["setup_steps"] = []

    if launch_strategy_order and launch_strategy_order[0] == "build_preview" and selected_build_command and selected_run_command:
        plan["run_steps"] = [selected_build_command, selected_run_command]
    elif selected_run_command:
        plan["run_steps"] = [selected_run_command]
    else:
        plan["run_steps"] = _default_run_steps(raw, repo_type)

    if repo_type == "unclear" and not plan["run_steps"]:
        plan["run_steps"] = _default_run_steps(raw, "web_app")

    if repo_type == "unclear" and not plan["run_steps"]:
        plan["run_steps"] = _default_run_steps(raw, "cli_app")

    if repo_type == "unclear":
        exploratory_run_steps = _safe_exploratory_run_steps(raw)
        generic_unclear_fallbacks = {
            "npm start",
            "npm run start",
            "npm run dev",
            "npm run preview",
            "pnpm start",
            "pnpm dev",
            "pnpm preview",
            "yarn start",
            "yarn dev",
            "yarn preview",
            "bun start",
            "bun run dev",
            "bun run preview",
            "python -m pip --version",
            "go run . --help",
        }

        if not plan["run_steps"]:
            plan["run_steps"] = exploratory_run_steps
        elif exploratory_run_steps and any(step.strip().lower() in generic_unclear_fallbacks for step in plan["run_steps"]):
            plan["run_steps"] = exploratory_run_steps


    if repo_type == "unclear":
        plan["notes"].append("Repository type could not be confidently determined.")
        if plan["run_steps"]:
            plan["notes"].append("Using an exploratory fallback execution plan based on detected repo signals.")
        else:
            plan["run_steps"] = [
                "ls",
                'find . -maxdepth 3 \( -name "DESIGN.md" -o -name "preview*.html" -o -name "index.html" \)',
            ]
            plan["notes"].append("Using safe exploratory commands to inspect repository contents and preview artifacts.")

    plan["notes"].append(f"Repo appears to be a '{repo_type}'.")

    if external_services:
        plan["notes"].append("External services detected: " + ", ".join(external_services))

    if required_env_vars:
        plan["blockers"].append("Missing required environment variables: " + ", ".join(required_env_vars))

    if rewrite_candidates:
        plan["notes"].append("Interactive init-like commands were detected and appear rewriteable or skippable.")
        plan["blockers"].append("Execution requires command rewrite or skip strategy for: " + " | ".join(rewrite_candidates))

    if execution_readiness == "ready" and plan["run_steps"]:
        plan["notes"].append("Repo appears runnable with current inferred setup assumptions.")

    if execution_readiness == "unclear":
        plan["blockers"].append("Execution path is unclear and requires manual review.")

    if execution_readiness == "unsupported":
        plan["blockers"].append("Repo is outside current supported execution policy.")

    return plan


def validate_decision_shape(data: Dict[str, Any]) -> None:
    repo_type = data.get("repo_type_guess")
    readiness = data.get("execution_readiness")
    support_tier = data.get("support_tier")
    risk_level = data.get("risk_level")
    confidence = data.get("confidence_overall")
    plan = data.get("recommended_plan")
    pending_requirements = data.get("pending_requirements")
    needs_env_vars = data.get("needs_env_vars")
    needs_command_rewrite = data.get("needs_command_rewrite")
    rewrite_candidates = data.get("rewrite_candidates")

    if repo_type not in ALLOWED_REPO_TYPES and repo_type != "unclear":
        raise ValueError(f"Invalid repo_type_guess: {repo_type}")

    if readiness not in ALLOWED_EXECUTION_READINESS:
        raise ValueError(f"Invalid execution_readiness: {readiness}")

    if support_tier not in ALLOWED_SUPPORT_TIERS:
        raise ValueError(f"Invalid support_tier: {support_tier}")

    if risk_level not in ALLOWED_RISK_LEVELS:
        raise ValueError(f"Invalid risk_level: {risk_level}")

    if confidence not in ALLOWED_CONFIDENCE:
        raise ValueError(f"Invalid confidence_overall: {confidence}")

    if not isinstance(plan, dict):
        raise ValueError("recommended_plan must be an object")

    for field in ["setup_steps", "run_steps", "notes", "blockers"]:
        if field not in plan:
            raise ValueError(f"recommended_plan missing '{field}'")
        if not isinstance(plan[field], list):
            raise ValueError(f"recommended_plan['{field}'] must be a list")

    if not isinstance(pending_requirements, list):
        raise ValueError("pending_requirements must be a list")

    if not isinstance(needs_env_vars, bool):
        raise ValueError("needs_env_vars must be a boolean")

    if not isinstance(needs_command_rewrite, bool):
        raise ValueError("needs_command_rewrite must be a boolean")

    if not isinstance(rewrite_candidates, list):
        raise ValueError("rewrite_candidates must be a list")

    if readiness == "no_run" and plan["run_steps"]:
        raise ValueError("run_steps must be empty when execution_readiness=no_run")


def build_repo_decision(raw_analysis: Dict[str, Any]) -> Dict[str, Any]:
    repo_url = str(raw_analysis.get("repo_url", "")).strip()
    detected_language = str(raw_analysis.get("detected_language", "unknown")).strip().lower()
    key_files = _normalize_str_list(raw_analysis.get("key_files"))
    entry_candidates = _unique_keep_order(_normalize_str_list(raw_analysis.get("entry_candidates")))
    readme_samples = _unique_keep_order(_normalize_str_list(raw_analysis.get("readme_command_samples")))
    required_env_vars = filter_env_vars(_normalize_str_list(raw_analysis.get("env_vars")))

    repo_type_guess = guess_repo_type(raw_analysis)
    external_services = detect_external_services(required_env_vars, readme_samples, key_files)
    execution_readiness = determine_execution_readiness(
        repo_type_guess,
        required_env_vars,
        entry_candidates,
        raw_analysis,
    )
    support_tier = determine_support_tier(
        repo_type_guess,
        detected_language,
        execution_readiness,
        raw_analysis,
    )
    risk_level = determine_risk_level(repo_type_guess, required_env_vars, external_services, raw_analysis)
    confidence_overall = determine_confidence(repo_type_guess, entry_candidates, key_files, readme_samples)

    pending_requirements = _build_pending_requirements(
        repo_type=repo_type_guess,
        env_vars=required_env_vars,
        raw_analysis=raw_analysis,
    )
    needs_env_vars = "env_vars" in pending_requirements
    needs_command_rewrite = "command_rewrite" in pending_requirements
    rewrite_candidates = _collect_interactive_rewrite_commands(raw_analysis)

    recommended_plan = build_recommended_plan(
        raw=raw_analysis,
        repo_type=repo_type_guess,
        execution_readiness=execution_readiness,
        required_env_vars=required_env_vars,
        external_services=external_services,
    )
    strategy_bundle = _build_strategy_bundle(raw_analysis, repo_type_guess, execution_readiness)
    for warning in _normalize_str_list(strategy_bundle.get("warnings")):
        note_text = f"Strategy warning: {warning}"
        if note_text not in recommended_plan["notes"]:
            recommended_plan["notes"].append(note_text)

    if execution_readiness == "ready" and not recommended_plan["run_steps"]:
        execution_readiness = "unclear"
        if support_tier == "fully_supported":
            support_tier = "partially_supported"
        blocker = "No runnable steps could be inferred from the current repo signals."
        if blocker not in recommended_plan["blockers"]:
            recommended_plan["blockers"].append(blocker)

    if repo_type_guess in {"library", "framework_source", "plugin_content"}:
        execution_readiness = "no_run"
        recommended_plan["run_steps"] = []
        if not recommended_plan["blockers"]:
            recommended_plan["blockers"].append("Direct execution is not supported for this repo type.")

    decision = RepoDecision(
        repo_url=repo_url,
        detected_language=detected_language or "unknown",
        repo_type_guess=repo_type_guess if repo_type_guess in ALLOWED_REPO_TYPES else "unclear",
        execution_readiness=execution_readiness,
        support_tier=support_tier,
        risk_level=risk_level,
        confidence_overall=confidence_overall,
        required_env_vars=required_env_vars,
        external_services_detected=external_services,
        entry_candidates=entry_candidates,
        pending_requirements=pending_requirements,
        needs_env_vars=needs_env_vars,
        needs_command_rewrite=needs_command_rewrite,
        rewrite_candidates=rewrite_candidates,
        recommended_plan=recommended_plan,
        selected_package_manager=str(strategy_bundle.get("selected_package_manager") or ""),
        selected_install_command=str(strategy_bundle.get("selected_install_command") or ""),
        selected_build_command=str(strategy_bundle.get("selected_build_command") or ""),
        selected_run_command=str(strategy_bundle.get("selected_run_command") or ""),
        launch_strategy_order=list(strategy_bundle.get("launch_strategy_order") or []),
        fallback_plan=list(strategy_bundle.get("fallback_plan") or []),
        strategy_rationale=list(strategy_bundle.get("strategy_rationale") or []),
        warnings=list(strategy_bundle.get("warnings") or []),
        confidence=dict(strategy_bundle.get("confidence") or {}),
    ).to_dict()

    validate_decision_shape(decision)
    return decision
