# /home/alaa/sandbox-demo/command_rewriter.py
from __future__ import annotations

from typing import Any, Dict, List, Optional
import re


KNOWN_INTERACTIVE_INIT_RULES = [
    {
        "pattern": r"(^|\s)researchclaw\s+init\b",
        "match_type": "regex",
        "action": "skip",
        "strategy": "skip_interactive_init",
        "rewritten_command": None,
        "reason": "Known interactive init command with no confirmed safe non-interactive flags yet.",
        "confidence": "high",
    },
    {
        "pattern": r"(^|\s)npm\s+init\b",
        "match_type": "regex",
        "action": "rewrite",
        "strategy": "force_yes_flag",
        "rewritten_command_template": "npm init -y",
        "reason": "npm init is commonly interactive but can usually be made non-interactive with -y.",
        "confidence": "high",
    },
    {
        "pattern": r"(^|\s)yarn\s+init\b",
        "match_type": "regex",
        "action": "rewrite",
        "strategy": "force_yes_flag",
        "rewritten_command_template": "yarn init -y",
        "reason": "yarn init is commonly interactive but can usually be made non-interactive with -y.",
        "confidence": "high",
    },
    {
        "pattern": r"(^|\s)pnpm\s+init\b",
        "match_type": "regex",
        "action": "rewrite",
        "strategy": "force_yes_flag",
        "rewritten_command_template": "pnpm init",
        "reason": "pnpm init may still need package metadata review; placeholder rewrite rule is conservative.",
        "confidence": "medium",
    },
    {
        "pattern": r"(^|\s)poetry\s+init\b",
        "match_type": "regex",
        "action": "rewrite",
        "strategy": "no_interaction_flag",
        "rewritten_command_template": "poetry init --no-interaction",
        "reason": "poetry init can typically be made non-interactive with --no-interaction.",
        "confidence": "high",
    },
]

COMMENT_SPLIT_PATTERN = r"\s+#"


def _normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip()).strip()


def strip_inline_comment(command: str) -> str:
    command = (command or "").rstrip()
    if not command:
        return ""

    parts = re.split(COMMENT_SPLIT_PATTERN, command, maxsplit=1)
    cleaned = parts[0].rstrip()
    return cleaned


def normalize_command(command: Any) -> str:
    if command is None:
        return ""

    if not isinstance(command, str):
        command = str(command)

    command = command.strip()
    command = strip_inline_comment(command)
    command = _normalize_whitespace(command)
    return command


def _match_rule(normalized_command: str) -> Optional[Dict[str, Any]]:
    for rule in KNOWN_INTERACTIVE_INIT_RULES:
        pattern = str(rule.get("pattern") or "").strip()
        match_type = str(rule.get("match_type") or "regex").strip().lower()

        if not pattern:
            continue

        if match_type == "regex":
            if re.search(pattern, normalized_command, flags=re.IGNORECASE):
                return rule

        elif match_type == "exact":
            if normalized_command.lower() == pattern.lower():
                return rule

    return None


def rewrite_command(command: Any) -> Dict[str, Any]:
    original_command = "" if command is None else str(command)
    normalized_command = normalize_command(original_command)

    if not normalized_command:
        return {
            "original_command": original_command,
            "normalized_command": normalized_command,
            "matched": False,
            "action": "allow",
            "strategy": "none",
            "rewritten_command": None,
            "reason": "Empty command after normalization.",
            "confidence": "low",
        }

    matched_rule = _match_rule(normalized_command)
    if not matched_rule:
        return {
            "original_command": original_command,
            "normalized_command": normalized_command,
            "matched": False,
            "action": "allow",
            "strategy": "none",
            "rewritten_command": None,
            "reason": "No rewrite rule matched; command should remain unchanged.",
            "confidence": "low",
        }

    action = str(matched_rule.get("action") or "allow").strip().lower()
    strategy = str(matched_rule.get("strategy") or "none").strip()
    reason = str(matched_rule.get("reason") or "").strip()
    confidence = str(matched_rule.get("confidence") or "low").strip().lower()

    rewritten_command = matched_rule.get("rewritten_command")
    if rewritten_command is None:
        template = matched_rule.get("rewritten_command_template")
        if isinstance(template, str) and template.strip():
            rewritten_command = template.strip()

    return {
        "original_command": original_command,
        "normalized_command": normalized_command,
        "matched": True,
        "action": action,
        "strategy": strategy,
        "rewritten_command": rewritten_command,
        "reason": reason,
        "confidence": confidence,
    }


def rewrite_commands(commands: List[Any]) -> Dict[str, Any]:
    items: List[Dict[str, Any]] = []
    changed_count = 0
    skipped_count = 0
    unresolved_count = 0

    for index, command in enumerate(commands or [], start=1):
        result = rewrite_command(command)
        result["step_index"] = index
        items.append(result)

        action = result.get("action")
        if action == "rewrite":
            changed_count += 1
        elif action == "skip":
            skipped_count += 1
        elif action not in {"allow", "rewrite", "skip"}:
            unresolved_count += 1

    return {
        "items": items,
        "summary": {
            "total": len(items),
            "rewritten": changed_count,
            "skipped": skipped_count,
            "unresolved": unresolved_count,
        },
    }


def apply_rewrite_actions(commands: List[Any]) -> Dict[str, Any]:
    rewrite_report = rewrite_commands(commands or [])
    final_commands: List[str] = []
    skipped_commands: List[str] = []

    for item in rewrite_report["items"]:
        action = item.get("action")
        normalized_command = str(item.get("normalized_command") or "").strip()
        rewritten_command = item.get("rewritten_command")

        if action == "skip":
            if normalized_command:
                skipped_commands.append(normalized_command)
            continue

        if action == "rewrite":
            if isinstance(rewritten_command, str) and rewritten_command.strip():
                final_commands.append(rewritten_command.strip())
            elif normalized_command:
                final_commands.append(normalized_command)
            continue

        if normalized_command:
            final_commands.append(normalized_command)

    return {
        "final_commands": final_commands,
        "skipped_commands": skipped_commands,
        "rewrite_report": rewrite_report,
    }
