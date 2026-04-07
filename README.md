# Tekarab Sandbox

Tekarab Sandbox is a repository execution and preview system for GitHub repositories.

It analyzes a repository, decides whether it is safely runnable, rewrites risky or misleading command plans when needed, and then chooses the most truthful user experience:
- launch a real web preview
- launch a real terminal flow
- or fall back safely when execution is unclear

The goal is not to pretend every repository works.
The goal is to be truthful, stable, and useful.

## Current demo scope

This community demo currently focuses on a small number of reliable cases:

- Static or simple web app repos
- Streamlit apps
- Node/Vite web apps
- Truthful fallback for template or unclear API-like repos

## What makes it different

Most repo runners fail in one of two ways:
1. they are too strict and reject everything
2. they fake success even when the repo is not truly runnable

Tekarab Sandbox tries to avoid both problems.

It aims to:
- infer a runnable plan from repository structure
- detect when setup or execution is unclear
- avoid unsafe or misleading launches
- expose a real public preview when the app is actually reachable
- show terminal fallback when a real preview is not ready
- report blockers honestly instead of inventing fake success

## Core flow

Tekarab Sandbox follows a practical pipeline:

1. Analyze the repository
2. Decide repo type, readiness, and launch strategy
3. Rewrite or skip unsafe or misleading commands when needed
4. Start a session
5. Run setup and launch commands
6. Detect a reachable service if one becomes available
7. Return the best truthful experience:
   - web preview
   - terminal fallback
   - or deferred/manual-review outcome

## Current verified demo cases

The current working demo has already verified these categories:

- Static/simple web preview case: PASS
- Streamlit app case: PASS
- Node/Vite web app case: PASS
- API-like or template repo with unclear execution path: truthfully deferred

This means the system already demonstrates both:
- successful end-to-end launches
- and honest fallback behavior when execution is not safely inferred

## Key principles

- Truthful status over fake success
- Stable preview over flashy but unreliable launch behavior
- Small verified support matrix before expanding scope
- Terminal fallback is acceptable when preview is not ready
- Unsupported or unclear repos should be deferred honestly

## Main files

- `api.py` - API entrypoint
- `repo_analyzer.py` - repository inspection and signal extraction
- `repo_decision.py` - repo classification, readiness, and execution strategy
- `session_runtime.py` - session lifecycle, command execution, preview detection
- `presentation_resolver.py` - user-facing launch experience resolution
- `command_rewriter.py` - command rewrite safety layer
- `smart_error_hints.py` - structured failure hints
- `smart_timeout.py` - timeout heuristics
- `interactive_detection.py` - interactive command detection
- `strategy_insights.py` - strategy reasoning helpers
- `run_command_policy.py` - command safety policy
- `test_repo_matrix.py` - repo matrix verification

## Current status

This repository is being prepared as a community-facing demo build.

Current focus:
- keep the sandbox truthful
- keep the supported matrix small and reliable
- improve preview and terminal UX
- document what works now before expanding support

## Not yet the goal

This demo does not aim to fully support every repository type yet.

For now, the priority is:
- a stable and honest sandbox
- a small showcase of working repo types
- clear behavior for non-runnable or unclear repos

## Planned next steps

- clean remaining auxiliary files before public publishing
- add example requests and demo screenshots
- document supported vs deferred repo patterns
- polish GitHub presentation for community sharing

## License

Not specified yet.
