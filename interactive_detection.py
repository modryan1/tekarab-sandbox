import re
from typing import Any, Dict, List, Optional


INTERACTIVE_INIT_PATTERNS = [
    r"(^|\s)pipenv\s+install\b",
    r"(^|\s)poetry\s+init\b",
    r"(^|\s)npm\s+init\b",
    r"(^|\s)yarn\s+init\b",
    r"(^|\s)pnpm\s+init\b",
    r"(^|\s)researchclaw\s+init\b",
    r"(^|\s)django-admin\s+startproject\b",
    r"(^|\s)flask\s+--app\b.*\binit\b",
    r"(^|\s)alembic\s+init\b",
    r"(^|\s)git\s+init\b",
]

INTERACTIVE_RUNTIME_PATTERNS = [
    r"\binput\s*\(",
    r"\bgetpass\s*\(",
    r"\bprompt\s*\(",
    r"\binquirer\b",
    r"\bquestionary\b",
    r"\bclick\.prompt\b",
    r"\bclick\.confirm\b",
    r"\btyper\.prompt\b",
    r"\bread\s+-p\b",
    r"\bselect\b",
]

INTERACTIVE_COMMAND_PATTERNS = [
    r"(^|\s)python(\d+(\.\d+)?)?\s*$",
    r"(^|\s)bash\s*$",
    r"(^|\s)sh\s*$",
    r"(^|\s)zsh\s*$",
    r"(^|\s)fish\s*$",
    r"(^|\s)node\s*$",
    r"(^|\s)mysql\s*$",
    r"(^|\s)psql\s*$",
    r"(^|\s)sqlite3\s*$",
    r"(^|\s)rails\s+console\b",
    r"(^|\s)python(\d+(\.\d+)?)?\s+-i\b",
]

PROMPT_FLAG_PATTERNS = [
    r"\b--interactive\b",
    r"\b--yes/no\b",
    r"\bpress any key\b",
    r"\bcontinue\?\b",
    r"\bare you sure\b",
    r"\benter your\b",
    r"\bchoose an option\b",
    r"\bselect an option\b",
]

SAFE_NON_INTERACTIVE_HINTS = [
    r"\b--yes\b",
    r"\b-y\b",
    r"\b--no-input\b",
    r"\b--non-interactive\b",
    r"\b--force\b",
    r"\bCI=1\b",
]


def _normalize_command(step: Any) -> str:
    if step is None:
        return ""

    if isinstance(step, str):
        return step.strip()

    if isinstance(step, dict):
        for key in ("command", "cmd", "shell_command", "run"):
            value = step.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    return str(step).strip()


def _find_matches(command: str, patterns: List[str]) -> List[str]:
    matches: List[str] = []
    for pattern in patterns:
        if re.search(pattern, command, flags=re.IGNORECASE):
            matches.append(pattern)
    return matches


def _safe_hints_present(command: str) -> List[str]:
    return _find_matches(command, SAFE_NON_INTERACTIVE_HINTS)


def detect_interactive_risk_for_step(step: Any) -> Dict[str, Any]:
    command = _normalize_command(step)

    if not command:
        return {
            "detected": False,
            "type": None,
            "confidence": "low",
            "command": command,
            "reasons": [],
            "matched_patterns": [],
            "safe_hints": [],
            "rewrite_candidate": False,
            "suggested_strategy": "allow",
        }

    init_matches = _find_matches(command, INTERACTIVE_INIT_PATTERNS)
    runtime_matches = _find_matches(command, INTERACTIVE_RUNTIME_PATTERNS)
    shell_matches = _find_matches(command, INTERACTIVE_COMMAND_PATTERNS)
    prompt_matches = _find_matches(command, PROMPT_FLAG_PATTERNS)
    safe_hints = _safe_hints_present(command)

    detected = False
    risk_type: Optional[str] = None
    confidence = "low"
    reasons: List[str] = []
    matched_patterns: List[str] = []
    rewrite_candidate = False
    suggested_strategy = "allow"

    if init_matches:
        detected = True
        risk_type = "init_command"
        confidence = "high"
        reasons.append("Command looks like an initialization/setup command that is often interactive.")
        matched_patterns.extend(init_matches)
        rewrite_candidate = True
        suggested_strategy = "rewrite_or_skip"

    elif shell_matches:
        detected = True
        risk_type = "interactive_shell"
        confidence = "high"
        reasons.append("Command looks like an interactive shell or REPL.")
        matched_patterns.extend(shell_matches)
        rewrite_candidate = False
        suggested_strategy = "reject"

    elif runtime_matches:
        detected = True
        risk_type = "runtime_prompt"
        confidence = "medium"
        reasons.append("Command or code looks like it may request user input at runtime.")
        matched_patterns.extend(runtime_matches)
        rewrite_candidate = False
        suggested_strategy = "review_or_reject"

    elif prompt_matches:
        detected = True
        risk_type = "prompt_language"
        confidence = "medium"
        reasons.append("Command text contains language commonly associated with prompts or confirmations.")
        matched_patterns.extend(prompt_matches)
        rewrite_candidate = True
        suggested_strategy = "rewrite_or_review"

    if detected and safe_hints:
        reasons.append("Non-interactive flags or CI hints were also detected.")
        if risk_type == "init_command":
            confidence = "medium"
            suggested_strategy = "review_then_allow"
        elif risk_type == "prompt_language":
            confidence = "low"
            suggested_strategy = "allow_with_review"

    return {
        "detected": detected,
        "type": risk_type,
        "confidence": confidence,
        "command": command,
        "reasons": reasons,
        "matched_patterns": matched_patterns,
        "safe_hints": safe_hints,
        "rewrite_candidate": rewrite_candidate,
        "suggested_strategy": suggested_strategy,
    }


def detect_interactive_risks(steps: List[Any]) -> Dict[str, Any]:
    results: List[Dict[str, Any]] = []
    detected_count = 0
    highest_confidence = "low"

    confidence_rank = {"low": 1, "medium": 2, "high": 3}

    for index, step in enumerate(steps or [], start=1):
        item = detect_interactive_risk_for_step(step)
        item["step_index"] = index
        results.append(item)

        if item["detected"]:
            detected_count += 1
            if confidence_rank[item["confidence"]] > confidence_rank[highest_confidence]:
                highest_confidence = item["confidence"]

    return {
        "detected": detected_count > 0,
        "count": detected_count,
        "highest_confidence": highest_confidence,
        "items": results,
    }


def summarize_interactive_risks(steps: List[Any]) -> Dict[str, Any]:
    report = detect_interactive_risks(steps)

    summary_types: Dict[str, int] = {}
    rewrite_candidates = 0

    for item in report["items"]:
        item_type = item.get("type")
        if item_type:
            summary_types[item_type] = summary_types.get(item_type, 0) + 1
        if item.get("rewrite_candidate"):
            rewrite_candidates += 1

    report["summary"] = {
        "types": summary_types,
        "rewrite_candidates": rewrite_candidates,
    }

    return report
