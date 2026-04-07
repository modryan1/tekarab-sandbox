import re
import shlex
from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class TimeoutDecision:
    timeout_seconds: int
    profile: str
    reasons: List[str]
    matched_rules: List[str]


DEFAULT_TIMEOUT_SECONDS = 120
MAX_TIMEOUT_SECONDS = 1800
MIN_TIMEOUT_SECONDS = 15
QUICK_TIMEOUT_SECONDS = 30


def _normalize_command(command: str) -> str:
    return (command or "").strip()


def _safe_split(command: str) -> List[str]:
    try:
        return shlex.split(command)
    except Exception:
        return command.split()


def _contains_any(text: str, parts: List[str]) -> bool:
    lowered = text.lower()
    return any(part.lower() in lowered for part in parts)


def _regex_match(text: str, patterns: List[str]) -> List[str]:
    matches: List[str] = []
    for pattern in patterns:
        if re.search(pattern, text, re.IGNORECASE):
            matches.append(pattern)
    return matches


def _is_quick_only_command(
    quick_matches: List[str],
    install_matches: List[str],
    build_matches: List[str],
    test_matches: List[str],
    dev_matches: List[str],
) -> bool:
    return bool(quick_matches) and not any(
        [install_matches, build_matches, test_matches, dev_matches]
    )


def decide_command_timeout(
    command: str,
    repo_context: Optional[Dict] = None,
    user_requested_timeout: Optional[int] = None,
) -> TimeoutDecision:
    """
    Decide a smart timeout for a shell command based on:
    - the command text
    - optional repo context
    - optional user requested timeout

    repo_context can contain keys like:
    {
        "detected_language": "python" | "javascript" | ...
        "repo_type_guess": "cli_app" | "web_app" | ...
        "package_managers": ["npm", "yarn", "pnpm", "pip"]
        "has_docker": True/False
        "has_monorepo_signals": True/False
    }
    """

    cmd = _normalize_command(command)
    repo_context = repo_context or {}
    reasons: List[str] = []
    matched_rules: List[str] = []

    if not cmd:
        return TimeoutDecision(
            timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
            profile="default",
            reasons=["Empty command, using default timeout."],
            matched_rules=[],
        )

    lowered = cmd.lower()
    _ = _safe_split(cmd)

    timeout_seconds = DEFAULT_TIMEOUT_SECONDS
    profile = "default"

    ultra_fast_patterns = [
        r"^(pwd|whoami|id|env|printenv)$",
        r"^(ls|ls\s.+)$",
        r"^(tree|tree\s.+)$",
        r"^(find\s.+)$",
        r"^(cat\s.+|head\s.+|tail\s.+)$",
        r"^(python3?\s+--version|python3?\s+-V|pip3?\s+--version|node\s+-v|npm\s+-v|yarn\s+-v|pnpm\s+-v)$",
        r"^(git\s+status|git\s+branch|git\s+log.*|git\s+rev-parse.*)$",
    ]

    install_patterns = [
        r"\bnpm\s+install\b",
        r"\bnpm\s+ci\b",
        r"\byarn(\s+install)?\b",
        r"\bpnpm\s+install\b",
        r"\bpip3?\s+install\b",
        r"\bpoetry\s+install\b",
        r"\bpipenv\s+install\b",
        r"\bbundle\s+install\b",
        r"\bcomposer\s+install\b",
        r"\bgo\s+mod\s+download\b",
        r"\bapt(-get)?\s+install\b",
        r"\bpython3?(?:\.\d+)?\s+-m\s+venv\b",
        r"\bpython\s+-m\s+venv\b",
    ]

    build_patterns = [
        r"\bnpm\s+run\s+build\b",
        r"\byarn\s+build\b",
        r"\bpnpm\s+build\b",
        r"\bnext\s+build\b",
        r"\bvite\s+build\b",
        r"\bwebpack\b",
        r"\btsc\b",
        r"\bpython3?\s+-m\s+build\b",
        r"\bmake\b",
        r"\bcmake\b",
        r"\bdocker\s+build\b",
        r"\bcargo\s+build\b",
        r"\bcargo\s+check\b",
        r"\bgo\s+build\b",
        r"\bgo\s+run\b",
    ]

    test_patterns = [
        r"\bpytest\b",
        r"\bpython3?\s+-m\s+pytest\b",
        r"\bnpm\s+test\b",
        r"\byarn\s+test\b",
        r"\bpnpm\s+test\b",
        r"\bjest\b",
        r"\bvitest\b",
        r"\bplaywright\b",
        r"\bcypress\b",
        r"\bgo\s+test\b",
        r"\bcargo\s+test\b",
    ]

    dev_server_patterns = [
        r"\bnpm\s+run\s+dev\b",
        r"\byarn\s+dev\b",
        r"\bpnpm\s+dev\b",
        r"\bnpm\s+start\b",
        r"\byarn\s+start\b",
        r"\bpnpm\s+start\b",
        r"\bflask\s+run\b",
        r"\bpython3?\s+app\.py\b",
        r"\bpython3?\s+-m\s+http\.server\b",
        r"\buvicorn\b",
        r"\bgunicorn\b",
        r"\bdjango-admin\s+runserver\b",
        r"\bpython3?\s+manage\.py\s+runserver\b",
    ]

    quick_matches = _regex_match(lowered, ultra_fast_patterns)
    install_matches = _regex_match(lowered, install_patterns)
    build_matches = _regex_match(lowered, build_patterns)
    test_matches = _regex_match(lowered, test_patterns)
    dev_matches = _regex_match(lowered, dev_server_patterns)

    quick_only = _is_quick_only_command(
        quick_matches=quick_matches,
        install_matches=install_matches,
        build_matches=build_matches,
        test_matches=test_matches,
        dev_matches=dev_matches,
    )

    if quick_only:
        timeout_seconds = QUICK_TIMEOUT_SECONDS
        profile = "quick"
        matched_rules.extend(quick_matches)
        reasons.append("Detected a quick inspection/version command.")

    if install_matches:
        timeout_seconds = max(timeout_seconds, 900)
        profile = "install"
        matched_rules.extend(install_matches)
        reasons.append("Detected dependency installation command.")

    if build_matches:
        timeout_seconds = max(timeout_seconds, 1200)
        profile = "build"
        matched_rules.extend(build_matches)
        reasons.append("Detected build command.")

    if test_matches:
        timeout_seconds = max(timeout_seconds, 900)
        profile = "test"
        matched_rules.extend(test_matches)
        reasons.append("Detected test command.")

    if dev_matches:
        timeout_seconds = max(timeout_seconds, 1800)
        profile = "server"
        matched_rules.extend(dev_matches)
        reasons.append("Detected long-running dev/server command.")

    if _contains_any(lowered, ["workspace:*", "turbo", "nx", "lerna", "workspaces"]):
        if not quick_only:
            timeout_seconds = max(timeout_seconds, 1200)
        matched_rules.append("monorepo-signal")
        reasons.append("Monorepo/workspace signals detected in command.")

    detected_language = (repo_context.get("detected_language") or "").lower()
    has_monorepo_signals = bool(repo_context.get("has_monorepo_signals"))
    package_managers = [str(x).lower() for x in repo_context.get("package_managers", [])]
    has_docker = bool(repo_context.get("has_docker"))

    if not quick_only:
        if detected_language in {"javascript", "typescript", "node"}:
            if install_matches or build_matches or test_matches:
                timeout_seconds = max(timeout_seconds, 1200)
                matched_rules.append("repo-language-node")
                reasons.append("Node/JS repo context increases timeout expectation.")

        if has_monorepo_signals:
            if install_matches or build_matches or test_matches or dev_matches:
                timeout_seconds = max(timeout_seconds, 1200)
                matched_rules.append("repo-monorepo")
                reasons.append("Repo context says this is likely a monorepo.")

        if has_docker and ("docker" in lowered or "compose" in lowered):
            timeout_seconds = max(timeout_seconds, 1800)
            matched_rules.append("repo-docker")
            reasons.append("Docker-related work can take longer.")

    if "yarn" in package_managers and re.search(r"\bnpm\s+install\b", lowered):
        matched_rules.append("hint-yarn-preferred")
        reasons.append("Repo context suggests yarn may be preferred over npm install.")

    if user_requested_timeout is not None:
        clamped = max(MIN_TIMEOUT_SECONDS, min(int(user_requested_timeout), MAX_TIMEOUT_SECONDS))
        if clamped > timeout_seconds:
            reasons.append(
                f"User requested timeout override applied: {clamped} seconds."
            )
            timeout_seconds = clamped
            profile = f"{profile}+user_override"
        else:
            reasons.append(
                f"User requested timeout ({clamped}s) was lower than smart timeout; smart timeout kept."
            )

    timeout_seconds = max(MIN_TIMEOUT_SECONDS, min(timeout_seconds, MAX_TIMEOUT_SECONDS))

    if not reasons:
        reasons.append("No special rule matched, using default timeout.")

    return TimeoutDecision(
        timeout_seconds=timeout_seconds,
        profile=profile,
        reasons=reasons,
        matched_rules=matched_rules,
    )


def build_timeout_metadata(
    command: str,
    repo_context: Optional[Dict] = None,
    user_requested_timeout: Optional[int] = None,
) -> Dict:
    decision = decide_command_timeout(
        command=command,
        repo_context=repo_context,
        user_requested_timeout=user_requested_timeout,
    )
    return {
        "command": command,
        "timeout_seconds": decision.timeout_seconds,
        "timeout_profile": decision.profile,
        "timeout_reasons": decision.reasons,
        "timeout_matched_rules": decision.matched_rules,
    }


if __name__ == "__main__":
    samples = [
        "node -v",
        "yarn install",
        "npm install",
        "npm run build",
        "pytest -q",
        "python app.py",
        "uvicorn main:app --reload",
    ]

    sample_context = {
        "detected_language": "javascript",
        "package_managers": ["yarn"],
        "has_monorepo_signals": True,
        "has_docker": False,
    }

    for sample in samples:
        result = build_timeout_metadata(sample, repo_context=sample_context)
        print("=" * 80)
        print(sample)
        print(result)
