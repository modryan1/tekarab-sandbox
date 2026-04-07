# /home/alaa/sandbox-demo/repo_analyzer.py
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import json
from typing import List, Tuple, Dict, Any

from interactive_detection import summarize_interactive_risks
from strategy_insights import analyze_execution_signals


# ----------------------------
# Limits
# ----------------------------
MAX_README_BYTES = 250 * 1024
GIT_CLONE_TIMEOUT = 45
GIT_SHOW_TIMEOUT = 10
GIT_LS_TREE_TIMEOUT = 12
MAX_FILE_LIST = 5000


# ----------------------------
# Small helpers
# ----------------------------
def truncate_text(text: str, limit: int) -> str:
    if not isinstance(text, str):
        text = str(text)
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]"


def dedupe_keep_order(items: List[str], limit: int | None = None) -> List[str]:
    seen: List[str] = []
    for item in items:
        item = (item or "").strip()
        if not item:
            continue
        if item not in seen:
            seen.append(item)

    if limit is not None:
        return seen[:limit]
    return seen


def parse_repo_url(url: str) -> Tuple[str | None, str | None]:
    if not isinstance(url, str):
        return None, None

    url = url.strip()
    pattern = r"^https://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$"
    match = re.match(pattern, url)

    if not match:
        return None, None

    owner = match.group(1).strip()
    repo = match.group(2).strip()

    if not owner or not repo:
        return None, None

    return owner, repo


# ----------------------------
# Repo analyzer internals
# ----------------------------
def run_git_command(repo_dir: str, args: List[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git"] + args,
        cwd=repo_dir,
        text=True,
        capture_output=True,
        timeout=timeout,
    )


def clone_repo_shallow(repo_url: str) -> Tuple[str | None, str | None]:
    temp_dir = tempfile.mkdtemp(prefix="tekarab_repo_")
    repo_dir = os.path.join(temp_dir, "repo")

    try:
        result = subprocess.run(
            [
                "git", "clone",
                "--depth", "1",
                "--single-branch",
                "--filter=blob:none",
                repo_url,
                repo_dir,
            ],
            text=True,
            capture_output=True,
            timeout=GIT_CLONE_TIMEOUT,
        )

        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            stdout = (result.stdout or "").strip()
            msg = stderr or stdout or "git clone failed"
            shutil.rmtree(temp_dir, ignore_errors=True)
            return None, f"Repository clone failed: {truncate_text(msg, 500)}"

        return repo_dir, None

    except subprocess.TimeoutExpired:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return None, "Repository clone timed out"
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return None, f"Repository clone failed: {str(e)}"

def list_repo_files_fast(repo_dir: str) -> List[str]:
    try:
        result = run_git_command(
            repo_dir,
            ["ls-tree", "-r", "--name-only", "HEAD"],
            timeout=GIT_LS_TREE_TIMEOUT,
        )
        if result.returncode != 0:
            return []

        files = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
        if len(files) <= MAX_FILE_LIST:
            return files

        root_files = [path for path in files if "/" not in path]
        remaining_slots = max(0, MAX_FILE_LIST - len(root_files))

        truncated = root_files + [path for path in files if "/" in path][:remaining_slots]
        return truncated[:MAX_FILE_LIST]
    except Exception:
        return []


def read_readme_fast(repo_dir: str) -> str:
    candidates = ["README.md", "README.rst", "README.txt", "readme.md"]

    for name in candidates:
        try:
            result = run_git_command(repo_dir, ["show", f"HEAD:{name}"], timeout=GIT_SHOW_TIMEOUT)
            if result.returncode == 0 and result.stdout:
                return result.stdout
        except Exception:
            continue

    return ""



def detect_language(file_list: List[str]) -> str:
    counts = {
        "python": 0,
        "javascript": 0,
        "typescript": 0,
        "go": 0,
        "rust": 0,
        "java": 0,
        "php": 0,
        "ruby": 0,
        "csharp": 0,
    }

    for path in file_list:
        lower = path.lower()
        if lower.endswith(".py"):
            counts["python"] += 1
        elif lower.endswith(".js"):
            counts["javascript"] += 1
        elif lower.endswith(".ts"):
            counts["typescript"] += 1
        elif lower.endswith(".go"):
            counts["go"] += 1
        elif lower.endswith(".rs"):
            counts["rust"] += 1
        elif lower.endswith(".java"):
            counts["java"] += 1
        elif lower.endswith(".php"):
            counts["php"] += 1
        elif lower.endswith(".rb"):
            counts["ruby"] += 1
        elif lower.endswith(".cs"):
            counts["csharp"] += 1

    best_lang = "unknown"
    best_count = 0

    for lang, count in counts.items():
        if count > best_count:
            best_lang = lang
            best_count = count

    return best_lang


def detect_key_files(file_list: List[str]) -> List[str]:
    wanted = [
        "requirements.txt",
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "Pipfile",
        "environment.yml",
        "package.json",
        "bun.lock",
        "bun.lockb",
        "pnpm-lock.yaml",
        "yarn.lock",
        "package-lock.json",
        "index.html",
        "dist/index.html",
        "out/index.html",
        "public/index.html",
        "vite.config.js",
        "vite.config.ts",
        "astro.config.mjs",
        "astro.config.js",
        "next.config.js",
        "next.config.mjs",
        "go.mod",
        "go.sum",
        "Dockerfile",
        "docker-compose.yml",
        "docker-compose.yaml",
        "Makefile",
        "README.md",
        "README.rst",
        "README.txt",
        ".claude-plugin/plugin.json",
        ".claude-plugin/marketplace.json",
    ]
    lower_map = {p.lower(): p for p in file_list}
    found = []

    for name in wanted:
        exact = lower_map.get(name.lower())
        if exact:
            found.append(exact)

    return found


def detect_entry_candidates(file_list: List[str]) -> List[str]:
    priority_names = [
        "main.py",
        "app.py",
        "run.py",
        "cli.py",
        "server.py",
        "manage.py",
        "__main__.py",
        "hello.py",
        "main.go",
    ]

    candidates: List[str] = []

    for name in priority_names:
        for path in file_list:
            lower = path.lower()

            if lower.endswith(".py"):
                if "/tests/" in lower or "/test/" in lower or "/docs/" in lower or "/examples/" in lower:
                    continue
                if os.path.basename(path).lower() == name.lower():
                    candidates.append(path)
                continue

            if lower.endswith(".go"):
                if "/tests/" in lower or "/test/" in lower or "/docs/" in lower or "/examples/" in lower:
                    continue
                if lower.endswith("_test.go"):
                    continue
                if os.path.basename(path).lower() == name.lower():
                    candidates.append(path)

    for path in file_list:
        lower = path.lower()

        if lower.endswith(".py"):
            if "/tests/" in lower or "/test/" in lower or "/docs/" in lower or "/examples/" in lower:
                continue

            base = os.path.basename(lower)
            if (
                lower.startswith("src/")
                or lower.startswith("app/")
                or lower.startswith("cli/")
                or lower.startswith("scripts/")
                or lower.startswith("inference/")
                or "/" not in lower
                or base.endswith("_app.py")
                or base.endswith("_server.py")
                or base.endswith("_demo.py")
                or base.endswith("_hello.py")
            ):
                if path not in candidates:
                    candidates.append(path)
            continue

        if lower.endswith(".go"):
            if "/tests/" in lower or "/test/" in lower or "/docs/" in lower or "/examples/" in lower:
                continue
            if lower.endswith("_test.go"):
                continue
            if (
                lower.startswith("cmd/")
                or lower.startswith("cli/")
                or lower.startswith("app/")
                or lower == "main.go"
            ):
                if path not in candidates:
                    candidates.append(path)

    return candidates[:12]



def clean_command_line(line: str) -> str:
    line = (line or "").strip()
    if not line:
        return ""

    line = re.sub(r"^\$\s*", "", line)
    line = re.sub(r"^\-\s*", "", line)
    line = line.strip()
    line = line.strip("`").strip()

    return line


def split_shell_chain(line: str) -> List[str]:
    if not line:
        return []

    parts: List[str] = []
    current: List[str] = []
    in_single_quote = False
    in_double_quote = False
    i = 0
    length = len(line)

    while i < length:
        char = line[i]

        if char == "'" and not in_double_quote:
            in_single_quote = not in_single_quote
            current.append(char)
            i += 1
            continue

        if char == '"' and not in_single_quote:
            in_double_quote = not in_double_quote
            current.append(char)
            i += 1
            continue

        if not in_single_quote and not in_double_quote:
            if line[i:i + 2] == "&&":
                part = clean_command_line("".join(current))
                if part:
                    parts.append(part)
                current = []
                i += 2
                continue

            if char == ";":
                part = clean_command_line("".join(current))
                if part:
                    parts.append(part)
                current = []
                i += 1
                continue

        current.append(char)
        i += 1

    part = clean_command_line("".join(current))
    if part:
        parts.append(part)

    return parts

def looks_like_command(line: str) -> bool:
    if not line:
        return False

    lower = line.lower()

    if len(line) > 220:
        return False

    if lower.startswith(("note:", "tip:", "warning:", "example:", "output:", "result:")):
        return False

    starters = (
        "python ", "python3 ", "python -m ", "python3 -m ",
        "pip ", "pip3 ", "poetry ", "uv ",
        "npm ", "npx ", "yarn ", "pnpm ",
        "node ", "go ", "cargo ", "make",
        "./configure", "cmake ", "source ",
        "export ", "flask ", "uvicorn ", "fastapi ",
        "streamlit ", "gunicorn ", "torchrun ", "researchclaw ",
        "gum ", "sudo ", "pytest", "tox", "nosetests",
    )

    return lower.startswith(starters)


def extract_readme_commands(readme_text: str) -> List[str]:
    commands: List[str] = []

    fenced_blocks = re.findall(
        r"```(?:[A-Za-z0-9_+-]+)?\n(.*?)```",
        readme_text or "",
        flags=re.DOTALL | re.IGNORECASE,
    )

    for block in fenced_blocks:
        for raw_line in block.splitlines():
            line = clean_command_line(raw_line)
            if not line or line.startswith("#"):
                continue

            if line.startswith(("╭", "╰", "│", "─", "--->")):
                continue

            for part in split_shell_chain(line):
                if looks_like_command(part):
                    commands.append(part)

    for raw_line in (readme_text or "").splitlines():
        if not raw_line.startswith("    "):
            continue

        line = clean_command_line(raw_line)
        if not line:
            continue

        for part in split_shell_chain(line):
            if looks_like_command(part):
                commands.append(part)

    return dedupe_keep_order(commands, limit=60)


def extract_env_vars(text: str) -> List[str]:
    text = text or ""
    matches: List[str] = []

    patterns = [
        r"export\s+([A-Z][A-Z0-9_]{2,})\s*=",
        r"os\.getenv\(\s*[\"']([A-Z][A-Z0-9_]{2,})[\"']\s*\)",
        r"os\.environ\.get\(\s*[\"']([A-Z][A-Z0-9_]{2,})[\"']\s*\)",
        r"os\.environ\[\s*[\"']([A-Z][A-Z0-9_]{2,})[\"']\s*\]",
        r"process\.env\.([A-Z][A-Z0-9_]{2,})",
        r"\$\{([A-Z][A-Z0-9_]{2,})\}",
        r"\$([A-Z][A-Z0-9_]{2,})\b",
    ]

    for pattern in patterns:
        found = re.findall(pattern, text)
        for item in found:
            value = item[0] if isinstance(item, tuple) else item
            if value:
                matches.append(value.upper())

    blacklist = {
        "PATH", "HOME", "USER", "PWD", "SHELL",
        "EDITOR", "HISTFILE", "SESSION", "VARIABLE",
        "TYPE", "SCOPE", "SUMMARY", "DESCRIPTION",
        "ENVIRONMENT_VARIABLES", "CARD", "LOVE",
        "BUBBLE", "GUM", "I_LOVE", "BUBBLE_GUM",
    }

    strong_prefixes = (
        "OPENAI_",
        "ANTHROPIC_",
        "GOOGLE_",
        "GEMINI_",
        "AWS_",
        "AZURE_",
        "GCP_",
        "DATABASE_",
        "POSTGRES_",
        "MYSQL_",
        "REDIS_",
        "MONGO_",
        "SUPABASE_",
        "STRIPE_",
        "TWILIO_",
        "SLACK_",
        "GITHUB_",
        "HF_",
    )

    results = []
    for item in matches:
        if item in blacklist:
            continue

        if item.startswith("GUM_"):
            continue

        if item.startswith(strong_prefixes):
            if item not in results:
                results.append(item)
            continue

    return results[:25]


def is_setup_command(cmd: str) -> bool:
    lower = cmd.lower()
    markers = [
        "python -m venv",
        "python3 -m venv",
        "pip install",
        "pip3 install",
        "poetry install",
        "uv sync",
        "uv pip install",
        "npm install",
        "npm ci",
        "yarn install",
        "pnpm install",
        "bun install",
        "./configure",
        "cmake ",
        "make",
        "make install",
        "source .venv",
    ]
    return any(marker in lower for marker in markers)


def is_test_command(cmd: str) -> bool:
    lower = cmd.lower()
    markers = [
        "pytest",
        "python -m pytest",
        "python3 -m pytest",
        "make test",
        "ctest",
        "tox",
        "nosetests",
        "python -m unittest",
        "python3 -m unittest",
    ]
    return any(marker in lower for marker in markers)


def is_run_command(cmd: str) -> bool:
    cmd = (cmd or "").strip()
    if not cmd:
        return False

    lower = cmd.lower()

    disallowed_prefixes = (
        "python -c ",
        "python3 -c ",
        "pip ",
        "pip3 ",
        "poetry add ",
        "poetry install",
        "uv pip ",
        "uv sync",
        "npm install",
        "npm ci",
        "yarn install",
        "pnpm install",
        "make install",
        "docker compose",
        "docker-compose",
        "bun install",
    )
    if lower.startswith(disallowed_prefixes):
        return False

    markers = [
        "python -m ",
        "python3 -m ",
        "uvicorn ",
        "flask run",
        "streamlit run",
        "streamlit hello",
        "gunicorn ",
        "researchclaw ",
        "fastapi ",
        "gum ",
        "npm run",
        "npm start",
        "node ",
        "npx ",
        "cargo run",
        "go run ",
        "torchrun ",
        "pnpm dev",
        "pnpm start",
        "pnpm preview",
        "yarn dev",
        "yarn start",
        "yarn preview",
        "bun run",
        "bun start",
        "bun dev",
        "bun x",
        "bunx ",
    ]
    return any(marker in lower for marker in markers)


def detect_setup_commands(file_list: List[str], readme_commands: List[str]) -> List[str]:
    suggestions: List[str] = []

    lower_files = {p.lower() for p in file_list}

    if "pyproject.toml" in lower_files:
        suggestions.append("python3 -m venv .venv && source .venv/bin/activate && pip install -e .")
    elif "requirements.txt" in lower_files:
        suggestions.append("python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt")
    elif "setup.py" in lower_files or "setup.cfg" in lower_files:
        suggestions.append("python3 -m venv .venv && source .venv/bin/activate && pip install -e .")
    elif "bun.lock" in lower_files or "bun.lockb" in lower_files:
        suggestions.append("bun install")
    elif "pnpm-lock.yaml" in lower_files:
        suggestions.append("pnpm install")
    elif "yarn.lock" in lower_files:
        suggestions.append("yarn install")
    elif "package.json" in lower_files:
        suggestions.append("npm install")
    elif "makefile" in lower_files:
        suggestions.append("./configure")
        suggestions.append("make")

    for cmd in readme_commands:
        lower_cmd = cmd.lower()

        if not is_setup_command(cmd) or is_test_command(cmd):
            continue

        if lower_cmd.startswith(("pip install ", "pip3 install ", "poetry install", "uv pip install")):
            if not any(token in lower_cmd for token in [" -r ", "requirements.txt", " -e ", " .", "./"]):
                continue

        suggestions.append(cmd)

    return dedupe_keep_order(suggestions, limit=10)


def detect_test_commands(readme_commands: List[str]) -> List[str]:
    suggestions: List[str] = []

    for cmd in readme_commands:
        if is_test_command(cmd):
            suggestions.append(cmd)

    return dedupe_keep_order(suggestions, limit=10)



def should_use_entry_fallback(
    language: str,
    key_files: List[str],
    readme_commands: List[str],
    run_commands: List[str],
) -> bool:
    if run_commands:
        return False

    if readme_commands:
        return False

    if language != "python":
        return False

    lower_key_files = {item.lower() for item in key_files}
    if any(name in lower_key_files for name in {"makefile", "dockerfile", "docker-compose.yml", "docker-compose.yaml"}):
        return False

    return True


def detect_package_scripts(repo_dir: str, file_list: List[str]) -> Dict[str, Any]:
    package_scripts: Dict[str, Any] = {}

    try:
        package_json_candidates = [
            path for path in file_list if path.lower().endswith("package.json")
        ]

        package_json_path = None

        if "package.json" in package_json_candidates:
            package_json_path = "package.json"
        elif package_json_candidates:
            package_json_path = min(
                package_json_candidates,
                key=lambda p: (p.count("/"), len(p)),
            )

        if package_json_path:
            full_path = os.path.join(repo_dir, package_json_path)
            with open(full_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                package_scripts = data.get("scripts", {}) or {}

                package_manager = data.get("packageManager")
                if isinstance(package_manager, str) and package_manager.strip():
                    package_scripts["_detected_package_manager"] = package_manager.strip()
    except Exception:
        package_scripts = {}

    if not isinstance(package_scripts, dict):
        return {}

    return package_scripts


def detect_python_scripts(repo_dir: str, file_list: List[str]) -> Dict[str, str]:
    python_scripts: Dict[str, str] = {}

    try:
        pyproject_candidates = [
            path for path in file_list if path.lower().endswith("pyproject.toml")
        ]

        pyproject_path = None

        if "pyproject.toml" in pyproject_candidates:
            pyproject_path = "pyproject.toml"
        elif pyproject_candidates:
            pyproject_path = min(
                pyproject_candidates,
                key=lambda p: (p.count("/"), len(p)),
            )

        if not pyproject_path:
            return {}

        full_path = os.path.join(repo_dir, pyproject_path)
        with open(full_path, "r", encoding="utf-8") as f:
            content = f.read()

        def extract_toml_section(section_name: str) -> str:
            pattern = (
                r"(?ms)^\["
                + re.escape(section_name)
                + r"\]\s*\n"
                + r"(.*?)(?=^\[[^\]]+\]\s*$|\Z)"
            )
            match = re.search(pattern, content)
            if not match:
                return ""
            return match.group(1)

        sections = [
            extract_toml_section("project.scripts"),
            extract_toml_section("tool.poetry.scripts"),
        ]

        for section_body in sections:
            if not section_body:
                continue

            for raw_line in section_body.splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue

                match = re.match(r'^([A-Za-z0-9._-]+)\s*=\s*["\']([^"\']+)["\']\s*$', line)
                if not match:
                    continue

                script_name = match.group(1).strip()
                script_target = match.group(2).strip()

                if script_name and script_name not in python_scripts:
                    python_scripts[script_name] = script_target

    except Exception:
        return {}

    return python_scripts


def detect_js_package_manager(file_list: List[str], package_scripts: Dict[str, Any] | None = None) -> str:
    lower_files = {p.lower() for p in file_list}

    if isinstance(package_scripts, dict):
        detected_package_manager = package_scripts.get("_detected_package_manager")
        if isinstance(detected_package_manager, str):
            lower_detected_package_manager = detected_package_manager.strip().lower()
            if lower_detected_package_manager.startswith("bun@") or lower_detected_package_manager == "bun":
                return "bun"
            if lower_detected_package_manager.startswith("pnpm@") or lower_detected_package_manager == "pnpm":
                return "pnpm"
            if lower_detected_package_manager.startswith("yarn@") or lower_detected_package_manager == "yarn":
                return "yarn"
            if lower_detected_package_manager.startswith("npm@") or lower_detected_package_manager == "npm":
                return "npm"

    if "bun.lock" in lower_files or "bun.lockb" in lower_files:
        return "bun"
    if "pnpm-lock.yaml" in lower_files:
        return "pnpm"
    if "yarn.lock" in lower_files:
        return "yarn"
    return "npm"


def detect_run_commands(
    file_list: List[str],
    key_files: List[str],
    language: str,
    entry_candidates: List[str],
    readme_commands: List[str],
    package_scripts: Dict[str, Any],
    python_scripts: Dict[str, str],
) -> List[str]:
    suggestions: List[str] = []

    lower_key_files = {item.lower() for item in key_files}
    lower_entry_candidates = [entry.lower() for entry in entry_candidates]
    lower_readme_commands_blob = " ".join(str(cmd).lower() for cmd in readme_commands)

    if language == "python":
        has_fastapi_signal = (
            "tests/main.py" in lower_key_files
            or "tests/main.py" in lower_entry_candidates
        ) and (
            "fastapi" in lower_readme_commands_blob
            or "uvicorn" in lower_readme_commands_blob
            or "fastapi" in " ".join(str(v).lower() for v in python_scripts.values())
            or "uvicorn" in " ".join(str(v).lower() for v in python_scripts.values())
        )

        if has_fastapi_signal:
            suggestions.append("uvicorn tests.main:app --host 0.0.0.0 --port 8000")

    for cmd in readme_commands:
        lower_cmd = cmd.lower()

        if " deploy" in lower_cmd or lower_cmd.startswith("deploy "):
            continue

        if is_run_command(cmd) and not is_setup_command(cmd) and not is_test_command(cmd):
            suggestions.append(cmd)

    python_backend_entries = [
        entry for entry in entry_candidates
        if entry.lower().endswith(".py") and entry.lower().startswith("backend/")
    ]

    js_frontend_only_signal = False
    if isinstance(package_scripts, dict):
        for script_value in package_scripts.values():
            if not isinstance(script_value, str):
                continue
            lower_script_value = script_value.lower()
            if "--filter frontend" in lower_script_value or " frontend " in f" {lower_script_value} ":
                js_frontend_only_signal = True
                break

    allow_js_package_scripts = "package.json" in lower_key_files
    if language == "python" and python_backend_entries and js_frontend_only_signal:
        allow_js_package_scripts = False

    if allow_js_package_scripts:
        package_manager = detect_js_package_manager(file_list, package_scripts)
        if not isinstance(package_scripts, dict):
            package_scripts = {}

        def add_js_command(script_name: str, npm_cmd: str, pnpm_cmd: str, yarn_cmd: str, bun_cmd: str) -> None:
            if script_name not in package_scripts:
                return

            if package_manager == "bun":
                suggestions.append(bun_cmd)
            elif package_manager == "pnpm":
                suggestions.append(pnpm_cmd)
            elif package_manager == "yarn":
                suggestions.append(yarn_cmd)
            else:
                suggestions.append(npm_cmd)


        add_js_command("dev", "npm run dev", "pnpm dev", "yarn dev", "bun run dev")
        add_js_command("start", "npm start", "pnpm start", "yarn start", "bun start")
        add_js_command("preview", "npm run preview", "pnpm preview", "yarn preview", "bun run preview")

        dev_script = package_scripts.get("dev")
        start_script = package_scripts.get("start")

        for script_value in [dev_script, start_script]:
            if not isinstance(script_value, str):
                continue

            lower_script = script_value.lower()

            if "turbo dev" in lower_script:
                if package_manager == "pnpm":
                    suggestions.append("pnpm turbo dev")
                suggestions.append("npx turbo dev")

            if "astro dev" in lower_script or "astro" in lower_script:
                suggestions.append("npx astro dev")

            if "vite" in lower_script and "dev" in lower_script:
                suggestions.append("npx vite")

            if "nx serve" in lower_script:
                suggestions.append("npx nx serve")

            if "nx dev" in lower_script:
                suggestions.append("npx nx dev")

    has_js_dev_signal = any(
        script_name in package_scripts
        for script_name in ["dev", "start", "preview"]
    )

    if language == "python" and isinstance(python_scripts, dict) and python_scripts:
        priority_names: List[str] = []
        secondary_names: List[str] = []

        for script_name, script_target in python_scripts.items():
            script_name_lower = script_name.lower()
            script_target_lower = script_target.lower()
            combined = f"{script_name} {script_target}".lower()

            if script_name_lower == "flask":
                continue

            if any(keyword in script_name_lower for keyword in ["upload", "theme", "test", "example", "demo", "deploy"]):
                continue

            if any(keyword in script_target_lower for keyword in ["upload_theme", "tests", "examples", "demo", "deploy"]):
                continue

            if has_js_dev_signal and any(keyword in combined for keyword in ["cli", "app"]):
                continue

            if any(
                existing.lower().startswith(f"{script_name_lower} ")
                for existing in suggestions
                if existing.lower() != script_name_lower
            ):
                continue

            if any(keyword in combined for keyword in ["proxy", "server", "serve", "run", "api"]):
                priority_names.append(script_name)
            elif any(keyword in combined for keyword in ["cli", "app"]):
                secondary_names.append(script_name)

        for script_name in priority_names + secondary_names:
            suggestions.append(script_name)

        if should_use_entry_fallback(language, key_files, readme_commands, suggestions):
            lower_entry_candidates = [entry.lower() for entry in entry_candidates]
            is_flask_repo = any(
                entry in {
                    "src/flask/app.py",
                    "src/flask/cli.py",
                    "src/flask/__main__.py",
                    "src/flask/__init__.py",
                }
                for entry in lower_entry_candidates
            )

            if language == "python" and is_flask_repo:
                pass
            else:
                for entry in entry_candidates:
                    lower = entry.lower()
                    if lower.endswith(".py") and "/scripts/" not in lower:
                        suggestions.append(f"python3 {entry}")

            if language == "python" and not suggestions:
                if "pyproject.toml" in lower_key_files or "requirements.txt" in lower_key_files:
                    textual_signals = [
                        entry for entry in entry_candidates
                        if "textual" in entry.lower()
                    ]
                    if textual_signals:
                        suggestions.append("python3 -m textual")
                    elif not is_flask_repo and "package.json" not in lower_key_files:
                        suggestions.append("python3 -m <module_name>")

    if language == "python" and any(
        cmd == "uvicorn tests.main:app --host 0.0.0.0 --port 8000"
        for cmd in suggestions
    ):
        suggestions = [
            cmd for cmd in suggestions
            if cmd.strip().lower() != "fastapi dev"
        ]
    return dedupe_keep_order(suggestions, limit=12)


def build_interactive_analysis(
    setup_commands: List[str],
    test_commands: List[str],
    run_commands: List[str],
    readme_commands: List[str],
) -> Dict[str, Any]:
    combined_primary_steps: List[str] = []
    combined_primary_steps.extend(setup_commands or [])
    combined_primary_steps.extend(run_commands or [])

    setup_risks = summarize_interactive_risks(setup_commands or [])
    run_risks = summarize_interactive_risks(run_commands or [])
    test_risks = summarize_interactive_risks(test_commands or [])
    readme_risks = summarize_interactive_risks(readme_commands or [])

    primary_risks = summarize_interactive_risks(combined_primary_steps)

    return {
        "detected": (
            setup_risks.get("detected", False)
            or run_risks.get("detected", False)
            or test_risks.get("detected", False)
            or readme_risks.get("detected", False)
        ),
        "setup_commands": setup_risks,
        "run_commands": run_risks,
        "test_commands": test_risks,
        "readme_commands": readme_risks,
        "primary_execution_path": primary_risks,
    }


def analyze_repo_contents(repo_url: str, repo_dir: str) -> Dict[str, Any]:
    file_list = list_repo_files_fast(repo_dir)
    language = detect_language(file_list)
    key_files = detect_key_files(file_list)
    entry_candidates = detect_entry_candidates(file_list)
    readme_text = read_readme_fast(repo_dir)

    readme_commands = extract_readme_commands(readme_text)
    package_scripts = detect_package_scripts(repo_dir, file_list)
    python_scripts = detect_python_scripts(repo_dir, file_list)
    setup_commands = detect_setup_commands(file_list, readme_commands)
    test_commands = detect_test_commands(readme_commands)
    run_commands = detect_run_commands(
        file_list,
        key_files,
        language,
        entry_candidates,
        readme_commands,
        package_scripts,
        python_scripts,
    )
    env_vars = extract_env_vars(readme_text)
    interactive_analysis = build_interactive_analysis(
        setup_commands=setup_commands,
        test_commands=test_commands,
        run_commands=run_commands,
        readme_commands=readme_commands,
    )
    execution_signals = analyze_execution_signals(
        repo_dir=repo_dir,
        file_list=file_list,
        key_files=key_files,
        detected_language=language,
        readme_text=readme_text,
        readme_commands=readme_commands,
        package_scripts=package_scripts,
        run_commands=run_commands,
    )

    warnings: List[str] = []

    if language == "unknown":
        warnings.append("Could not confidently detect project language")

    if not setup_commands:
        warnings.append("No clear setup command detected yet")

    if not run_commands:
        warnings.append("No clear run command detected yet")

    if any(name in key_files for name in ["Dockerfile", "docker-compose.yml", "docker-compose.yaml"]):
        warnings.append("Repository includes Docker-related files, which may require a fuller runtime later")

    if env_vars:
        warnings.append("Repository may require environment variables or API keys")

    if interactive_analysis.get("detected"):
        warnings.append("Potential interactive commands detected in setup, run, test, or README command samples")

    summary = {
        "repo_url": repo_url,
        "detected_language": language,
        "key_files": key_files[:20],
        "entry_candidates": entry_candidates[:12],
        "setup_commands": setup_commands,
        "test_commands": test_commands,
        "run_commands": run_commands,
        "env_vars": env_vars,
        "readme_command_samples": readme_commands[:12],
        "interactive_risks": interactive_analysis,
        "warnings": warnings,
        "readme_preview": truncate_text(readme_text, 1800) if readme_text else "",
        "package_scripts": package_scripts,
        "python_scripts": python_scripts,
        "package_managers_detected": execution_signals.get("package_managers_detected", []),
        "preferred_package_manager": execution_signals.get("preferred_package_manager", {}),
        "install_strategy_candidates": execution_signals.get("install_strategy_candidates", []),
        "run_strategy_candidates": execution_signals.get("run_strategy_candidates", []),
        "build_strategy_candidates": execution_signals.get("build_strategy_candidates", []),
        "preview_strategy_candidates": execution_signals.get("preview_strategy_candidates", []),
        "framework_signals": execution_signals.get("framework_signals", []),
        "runtime_signals": execution_signals.get("runtime_signals", []),
        "lockfile_signals": execution_signals.get("lockfile_signals", {}),
        "workspace_signals": execution_signals.get("workspace_signals", {}),
        "subpath_preview_risk": execution_signals.get("subpath_preview_risk", {}),
        "dev_preview_risk": execution_signals.get("dev_preview_risk", {}),
        "production_preview_readiness": execution_signals.get("production_preview_readiness", {}),
        "repo_execution_profile": execution_signals.get("repo_execution_profile", {}),
        "config_package_mismatches": execution_signals.get("config_package_mismatches", []),
    }

    return summary


def analyze_repo(repo_url: str) -> Dict[str, Any]:
    if not repo_url:
        raise ValueError("repo_url is required")

    owner, repo = parse_repo_url(repo_url)
    if not owner or not repo:
        raise ValueError("Invalid GitHub repository URL")

    normalized_repo_url = f"https://github.com/{owner}/{repo}"

    repo_dir, clone_error = clone_repo_shallow(normalized_repo_url)
    if clone_error:
        raise RuntimeError(clone_error)

    try:
        return analyze_repo_contents(normalized_repo_url, repo_dir)
    finally:
        temp_root = os.path.dirname(repo_dir)
        shutil.rmtree(temp_root, ignore_errors=True)
