from __future__ import annotations

from typing import Any, Dict, List, Optional


PRESENTATION_TYPE_WEB_APP = "web_app"
PRESENTATION_TYPE_API_SERVICE = "api_service"
PRESENTATION_TYPE_CLI_APP = "cli_app"
PRESENTATION_TYPE_OUTPUT_ARTIFACTS = "output_artifacts"
PRESENTATION_TYPE_JOB_RUNNER = "job_runner"


def _normalize_str_list(value: Any) -> List[str]:
    if not value:
        return []

    if isinstance(value, list):
        items = value
    else:
        items = [value]

    normalized: List[str] = []
    for item in items:
        text = str(item).strip()
        if text:
            normalized.append(text)
    return normalized


def _unique_keep_order(items: List[str]) -> List[str]:
    seen = set()
    result: List[str] = []

    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)

    return result


def _lower_blob(*parts: List[str]) -> str:
    merged: List[str] = []

    for part in parts:
        merged.extend(part)

    return " ".join(merged).lower()


def _has_any(blob: str, needles: List[str]) -> bool:
    return any(needle.lower() in blob for needle in needles)


def _first_matching_command(commands: List[str], keywords: List[str]) -> Optional[str]:
    skip_prefixes = [
        "git clone",
        "npx tiged",
        "npm create",
        "pnpm create",
        "yarn create",
        "create-next-app",
        "npm install",
        "pnpm install",
        "yarn install",
        "bun install",
        "npm ci",
        "pnpm i",
        "cd ",
    ]

    separators = ["&&", ";"]

    for command in commands:
        parts = [command]

        for separator in separators:
            expanded_parts: List[str] = []
            for part in parts:
                expanded_parts.extend(part.split(separator))
            parts = expanded_parts

        for part in parts:
            candidate = str(part).strip()
            lowered = candidate.lower()

            if not candidate:
                continue

            if any(lowered.startswith(prefix) for prefix in skip_prefixes):
                continue

            if any(keyword in lowered for keyword in keywords):
                return candidate

    return None


def _map_repo_type_to_presentation_type(repo_type: str, raw_analysis: Dict[str, Any]) -> str:
    repo_type = str(repo_type or "").strip().lower()

    key_files = _normalize_str_list(raw_analysis.get("key_files"))
    entry_candidates = _normalize_str_list(raw_analysis.get("entry_candidates"))
    readme_samples = _normalize_str_list(raw_analysis.get("readme_command_samples"))
    readme_preview = _normalize_str_list(raw_analysis.get("readme_preview"))
    blob = _lower_blob(key_files, entry_candidates, readme_samples, readme_preview)

    web_signals = [
        "next",
        "nextjs",
        "vite",
        "astro",
        "react",
        "vue",
        "svelte",
        "solidjs",
        "solid-start",
        "nuxt",
        "remix",
        "frontend",
        "web app",
        "index.html",
        "app router",
        "pages/",
        "src/app",
        "src/pages",
        "npm run dev",
        "pnpm dev",
        "yarn dev",
        "bun dev",
        "bun run dev",
        "npm start",
        "pnpm start",
        "yarn start",
        "bun start",
        "bun run start",
        "bun preview",
        "bun run preview",
        "streamlit",
        "gradio",
        "dash",
        "flask run",
        "templates/",
        "public/",
        "dist/",
        "design.md",
        "google stitch",
        "design system document",
        "design agents",
        "pixel-perfect ui",
    ]

    # NOTE: api_signals are kept for future web_api support implementation
    # Currently, repositories matching api_signals are classified as "library" in repo_decision.py
    api_signals = [
        "fastapi",
        "flask",
        "uvicorn",
        "gunicorn",
        "openapi",
        "swagger",
        "rest api",
        "api server",
        "backend",
        "express",
        "koa",
        "hono",
        "nestjs",
        "server.js",
        "server.ts",
        "app.py",
        "main.py",
        "manage.py",
        "wsgi.py",
        "asgi.py",
        "docker-compose",
        "compose.yml",
        "compose.yaml",
        "/health",
        "/docs",
    ]

    cli_signals = [
        "argparse",
        "click",
        "typer",
        "python -m",
        "cli",
        "__main__.py",
        "main()",
        "usage:",
        "install && run from terminal",
    ]

    if repo_type == "web_app":
        return PRESENTATION_TYPE_WEB_APP

    # NOTE: web_api type is not yet supported end-to-end, so this mapping is disabled
    # if repo_type == "web_api":
    #     return PRESENTATION_TYPE_API_SERVICE

    if repo_type in {"cli_app", "script_collection"}:
        return PRESENTATION_TYPE_CLI_APP

    if repo_type == "ml_experiment":
        if _has_any(blob, ["train.py", "inference.py", "evaluate", "benchmark", "batch"]):
            return PRESENTATION_TYPE_JOB_RUNNER
        if _has_any(blob, ["streamlit", "gradio", "dash", "app.py", "webui"]):
            return PRESENTATION_TYPE_WEB_APP
        return PRESENTATION_TYPE_OUTPUT_ARTIFACTS

    if _has_any(blob, web_signals):
        return PRESENTATION_TYPE_WEB_APP

    # NOTE: Repositories with api_signals are now classified as "library" in repo_decision.py
    # Since web_api support is not yet implemented, we do NOT map to API_SERVICE via heuristics.
    # This ensures no contradiction: if a repo has api signals, it will be "library" (unsupported)
    # and its presentation will correctly reflect "output_artifacts" (not a false API_SERVICE claim).
    # Do NOT uncomment the lines below until web_api support is fully implemented:
    # if _has_any(blob, api_signals):
    #     return PRESENTATION_TYPE_API_SERVICE

    if _has_any(blob, cli_signals):
        return PRESENTATION_TYPE_CLI_APP

    if _has_any(blob, ["package.json", "pnpm-workspace.yaml", "turbo.json", "vercel.json", "bun.lock", "bun.lockb"]):
        return PRESENTATION_TYPE_WEB_APP

    # NOTE: Do NOT match Python project patterns as API_SERVICE without explicit web_api classification.
    # This prevents false API_SERVICE claims for generic Python libraries and projects.
    # if _has_any(blob, ["requirements.txt", "pyproject.toml", "main.py", "app.py"]):
    #     return PRESENTATION_TYPE_API_SERVICE

    return PRESENTATION_TYPE_OUTPUT_ARTIFACTS


def _estimate_presentation_confidence(
    presentation_type: str,
    repo_type: str,
    raw_analysis: Dict[str, Any],
) -> float:
    key_files = _normalize_str_list(raw_analysis.get("key_files"))
    entry_candidates = _normalize_str_list(raw_analysis.get("entry_candidates"))
    readme_samples = _normalize_str_list(raw_analysis.get("readme_command_samples"))
    readme_preview = _normalize_str_list(raw_analysis.get("readme_preview"))
    blob = _lower_blob(key_files, entry_candidates, readme_samples, readme_preview)

    score = 0.45

    if repo_type:
        score += 0.15

    if presentation_type == PRESENTATION_TYPE_WEB_APP:
        if _has_any(blob, ["package.json", "vite", "next", "astro", "react", "vue", "svelte", "bun.lock", "bun.lockb"]):
            score += 0.20
        if _has_any(blob, ["build", "preview", "dist", "public"]):
            score += 0.10

    elif presentation_type == PRESENTATION_TYPE_API_SERVICE:
        if _has_any(blob, ["fastapi", "flask", "uvicorn", "gunicorn", "openapi", "swagger"]):
            score += 0.25

    elif presentation_type == PRESENTATION_TYPE_CLI_APP:
        if _has_any(blob, ["cli", "__main__.py", "argparse", "click", "typer", "python -m"]):
            score += 0.20

    elif presentation_type == PRESENTATION_TYPE_JOB_RUNNER:
        if _has_any(blob, ["train.py", "inference.py", "batch", "worker", "job"]):
            score += 0.20

    elif presentation_type == PRESENTATION_TYPE_OUTPUT_ARTIFACTS:
        if _has_any(blob, ["notebook", ".ipynb", "report", "output", "artifact"]):
            score += 0.20

    if entry_candidates:
        score += 0.05

    return max(0.0, min(0.99, round(score, 2)))


def _infer_package_manager(raw_analysis: Dict[str, Any], preferred_commands: List[str]) -> str:
    key_files = _normalize_str_list(raw_analysis.get("key_files"))
    readme_samples = _normalize_str_list(raw_analysis.get("readme_command_samples"))
    blob = _lower_blob(key_files, readme_samples, preferred_commands)

    if _has_any(
        blob,
        [
            'packagemanager": "bun@',
            'packagemanager":"bun@',
            "bun.lock",
            "bun.lockb",
            "bun start",
            "bun run",
            "bun dev",
            "bun preview",
            "bun build",
        ],
    ):
        return "bun"

    if _has_any(blob, ["pnpm-lock.yaml", "pnpm-workspace.yaml", "pnpm start", "pnpm dev", "pnpm build"]):
        return "pnpm"

    if _has_any(blob, ["yarn.lock", "yarn start", "yarn dev", "yarn build"]):
        return "yarn"

    return "npm"


def _get_web_candidate_launch_strategies(
    raw_analysis: Dict[str, Any],
    preferred_run_steps: List[str],
) -> List[str]:
    key_files = _normalize_str_list(raw_analysis.get("key_files"))
    readme_samples = _normalize_str_list(raw_analysis.get("readme_command_samples"))
    readme_preview = _normalize_str_list(raw_analysis.get("readme_preview"))
    blob = _lower_blob(key_files, readme_samples, readme_preview, preferred_run_steps)

    strategies: List[str] = []

    if _has_any(
        blob,
        [
            "preview",
            "vite preview",
            "next start",
            "astro preview",
            "bun run preview",
            "bun preview",
        ],
    ):
        strategies.append("preview")

    if _has_any(
        blob,
        [
            "npm start",
            "pnpm start",
            "yarn start",
            "bun start",
            "bun run start",
            "next start",
            "node server",
            "node app",
        ],
    ):
        strategies.append("start")

    if _has_any(
        blob,
        [
            "npm run dev",
            "pnpm dev",
            "yarn dev",
            "bun dev",
            "bun run dev",
            "vite",
            "astro dev",
            "next dev",
        ],
    ):
        strategies.append("dev")

    if _has_any(blob, ["dist", "build/", "out/", "public/", "static", "serve"]):
        strategies.append("static_serve")

    default_order = ["start", "preview", "dev", "static_serve"]
    strategies.extend(default_order)
    return _unique_keep_order(strategies)


def _choose_web_build_command(
    raw_analysis: Dict[str, Any],
    preferred_commands: List[str],
) -> Optional[str]:
    readme_samples = _normalize_str_list(raw_analysis.get("readme_command_samples"))
    package_manager = _infer_package_manager(raw_analysis, preferred_commands)

    preferred = _first_matching_command(
        preferred_commands + readme_samples,
        [
            "bun run build",
            "bun build",
            "pnpm build",
            "npm run build",
            "yarn build",
            "next build",
            "vite build",
            "astro build",
        ],
    )
    if preferred:
        return preferred

    blob = _lower_blob(readme_samples, _normalize_str_list(raw_analysis.get("key_files")), preferred_commands)
    if "package.json" in blob:
        if package_manager == "bun":
            return "bun run build"
        if package_manager == "pnpm":
            return "pnpm build"
        if package_manager == "yarn":
            return "yarn build"
        return "npm run build"

    return None


def _choose_web_run_command(
    raw_analysis: Dict[str, Any],
    launch_mode: str,
    preferred_run_steps: List[str],
) -> Optional[str]:
    readme_samples = _normalize_str_list(raw_analysis.get("readme_command_samples"))
    key_files = _normalize_str_list(raw_analysis.get("key_files"))
    package_manager = _infer_package_manager(raw_analysis, preferred_run_steps)
    preferred_source = preferred_run_steps + readme_samples

    if launch_mode == "static_serve":
        normalized_key_files = {str(item).strip().lower() for item in key_files}

        if "index.html" in normalized_key_files:
            return "python3 -m http.server $PORT"

        if "dist/index.html" in normalized_key_files:
            return "python3 -m http.server $PORT --directory dist"

        if "out/index.html" in normalized_key_files:
            return "python3 -m http.server $PORT --directory out"

        if "public/index.html" in normalized_key_files:
            return "python3 -m http.server $PORT --directory public"

        return "python3 -m http.server $PORT --directory dist"

    if launch_mode == "start":
        preferred = _first_matching_command(
            preferred_source,
            [
                "bun start",
                "bun run start",
                "pnpm start",
                "npm start",
                "npm run start",
                "yarn start",
                "next start",
                "node server",
                "node app",
            ],
        )
        if preferred:
            return preferred

        if package_manager == "bun":
            return "bun start"
        if package_manager == "pnpm":
            return "pnpm start"
        if package_manager == "yarn":
            return "yarn start"
        return "npm start"

    if launch_mode == "dev":
        preferred = _first_matching_command(
            preferred_source,
            [
                "bun dev",
                "bun run dev",
                "pnpm dev",
                "npm run dev",
                "yarn dev",
                "vite",
                "astro dev",
                "next dev",
            ],
        )
        if preferred:
            return preferred

        if package_manager == "bun":
            return "bun dev --host 0.0.0.0 --port $PORT"
        if package_manager == "pnpm":
            return "pnpm dev --host 0.0.0.0 --port $PORT"
        if package_manager == "yarn":
            return "yarn dev --host 0.0.0.0 --port $PORT"
        return "npm run dev -- --host 0.0.0.0 --port $PORT"

    return None


def _choose_web_fallback_command(
    raw_analysis: Dict[str, Any],
    launch_mode: str,
    preferred_run_steps: List[str],
) -> Optional[str]:
    package_manager = _infer_package_manager(raw_analysis, preferred_run_steps)

    if launch_mode == "preview":
        return "python3 -m http.server $PORT --directory dist"

    if launch_mode == "static_serve":
        if package_manager == "bun":
            return "bun start"
        if package_manager == "pnpm":
            return "pnpm start"
        if package_manager == "yarn":
            return "yarn start"
        return "npm start"

    if launch_mode == "start":
        if package_manager == "bun":
            return "bun dev --host 0.0.0.0 --port $PORT"
        if package_manager == "pnpm":
            return "pnpm dev --host 0.0.0.0 --port $PORT"
        if package_manager == "yarn":
            return "yarn dev --host 0.0.0.0 --port $PORT"
        return "npm run dev -- --host 0.0.0.0 --port $PORT"

    if launch_mode == "dev":
        return None

    return "python3 -m http.server $PORT --directory dist"


def _resolve_first_experience_name(presentation_type: str) -> str:
    if presentation_type == PRESENTATION_TYPE_WEB_APP:
        return "open_web_preview"
    if presentation_type == PRESENTATION_TYPE_API_SERVICE:
        return "open_api_endpoint"
    if presentation_type == PRESENTATION_TYPE_CLI_APP:
        return "run_cli_sample"
    if presentation_type == PRESENTATION_TYPE_OUTPUT_ARTIFACTS:
        return "view_generated_outputs"
    if presentation_type == PRESENTATION_TYPE_JOB_RUNNER:
        return "start_background_job"
    return "open_primary_experience"


def _build_reason(
    presentation_type: str,
    repo_type: str,
    launch_mode: Optional[str],
    raw_analysis: Dict[str, Any],
) -> str:
    key_files = _normalize_str_list(raw_analysis.get("key_files"))
    readme_samples = _normalize_str_list(raw_analysis.get("readme_command_samples"))
    readme_preview = _normalize_str_list(raw_analysis.get("readme_preview"))
    blob = _lower_blob(key_files, readme_samples, readme_preview)

    if presentation_type == PRESENTATION_TYPE_WEB_APP:
        if launch_mode == "preview":
            return "Buildable frontend detected; preview is preferred over dev mode for stability."
        if launch_mode == "static_serve":
            return "Static build output signals detected; static serving is preferred for a stable first experience."
        if launch_mode == "start":
            return "A production-style start command is available and was selected as the best web launch path."
        if launch_mode == "dev":
            return "Web app signals were detected, but only a dev-style launch path appears available."

    if presentation_type == PRESENTATION_TYPE_API_SERVICE:
        return "API/server signals were detected from the repository structure and README commands."

    if presentation_type == PRESENTATION_TYPE_CLI_APP:
        return "CLI-style entry points and command usage signals were detected."

    if presentation_type == PRESENTATION_TYPE_JOB_RUNNER:
        return "Batch/job-oriented scripts were detected in the repository."

    if presentation_type == PRESENTATION_TYPE_OUTPUT_ARTIFACTS:
        return "The repository appears focused on producing outputs or artifacts rather than an interactive service."

    if repo_type == "unclear" and "package.json" in blob:
        return "General runnable signals were detected, but the repository shape remains partially ambiguous."

    return "The recommended first experience was inferred from repository structure, key files, and README command samples."


def resolve_presentation(
    raw_analysis: Dict[str, Any],
    repo_decision: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    repo_decision = repo_decision or {}

    repo_type = str(
        repo_decision.get("repo_type_guess")
        or repo_decision.get("repo_type")
        or raw_analysis.get("repo_type")
        or ""
    ).strip().lower()

    presentation_type = _map_repo_type_to_presentation_type(repo_type, raw_analysis)
    execution_readiness = str(repo_decision.get("execution_readiness") or "").strip().lower()
    recommended_plan = repo_decision.get("recommended_plan") or {}
    preferred_run_steps = _normalize_str_list(recommended_plan.get("run_steps"))
    preferred_setup_steps = _normalize_str_list(recommended_plan.get("setup_steps"))

    if (
        presentation_type == PRESENTATION_TYPE_WEB_APP
        and not preferred_run_steps
        and execution_readiness != "ready"
    ):
        presentation_type = PRESENTATION_TYPE_OUTPUT_ARTIFACTS

    presentation_confidence = _estimate_presentation_confidence(
        presentation_type=presentation_type,
        repo_type=repo_type,
        raw_analysis=raw_analysis,
    )

    recommended_first_experience = _resolve_first_experience_name(presentation_type)

    recommended_launch_mode: Optional[str] = None
    recommended_build_command: Optional[str] = None
    recommended_run_command: Optional[str] = None
    fallback_command: Optional[str] = None
    candidate_launch_strategies: List[str] = []

    if presentation_type == PRESENTATION_TYPE_WEB_APP:
        candidate_launch_strategies = _get_web_candidate_launch_strategies(raw_analysis, preferred_run_steps)
        recommended_launch_mode = candidate_launch_strategies[0] if candidate_launch_strategies else "preview"
        recommended_build_command = _choose_web_build_command(
            raw_analysis,
            preferred_setup_steps + preferred_run_steps,
        )

        preferred_primary_run_command = preferred_run_steps[0] if preferred_run_steps else None
        if preferred_primary_run_command:
            recommended_run_command = preferred_primary_run_command

            normalized_primary_run = preferred_primary_run_command.strip().lower()
            if "http.server" in normalized_primary_run:
                recommended_launch_mode = "static_serve"
            elif "preview" in normalized_primary_run:
                recommended_launch_mode = "preview"
            elif "dev" in normalized_primary_run:
                recommended_launch_mode = "dev"
            elif any(token in normalized_primary_run for token in ["start", "uvicorn", "gunicorn", "flask run", "streamlit run"]):
                recommended_launch_mode = "start"
        else:
            recommended_run_command = _choose_web_run_command(
                raw_analysis,
                recommended_launch_mode,
                preferred_run_steps,
            )

        fallback_command = _choose_web_fallback_command(
            raw_analysis,
            recommended_launch_mode,
            preferred_run_steps,
        )

    why_this_choice = _build_reason(
        presentation_type=presentation_type,
        repo_type=repo_type,
        launch_mode=recommended_launch_mode,
        raw_analysis=raw_analysis,
    )

    return {
        "presentation_type": presentation_type,
        "presentation_confidence": presentation_confidence,
        "recommended_first_experience": recommended_first_experience,
        "recommended_launch_mode": recommended_launch_mode,
        "recommended_build_command": recommended_build_command,
        "recommended_run_command": recommended_run_command,
        "fallback_command": fallback_command,
        "why_this_choice": why_this_choice,
        "candidate_launch_strategies": candidate_launch_strategies,
    }
