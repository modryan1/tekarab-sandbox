# Tekarab Sandbox

Tekarab Sandbox is a truthful GitHub repository sandbox.

It analyzes repositories, decides whether they are safely runnable, launches supported apps with a real preview when possible, and falls back safely when execution is unclear.

It is built around one core idea:

**Do not fake success.**
If a repository is runnable, launch it.
If it is unclear, say so honestly and provide a safe terminal fallback.

---

## Quick links

- [Demo cases](docs/demo-cases.md)

---

## Why this exists

Most repository runners fail in one of two ways:

1. They reject too many repositories and become useless.
2. They pretend a repository worked even when it did not truly run.

Tekarab Sandbox is designed to avoid both problems.

It tries to:
- analyze repository structure and infer a runnable plan
- choose a launch strategy based on real signals
- rewrite or skip misleading commands when needed
- expose a real public preview only when the app is actually reachable
- fall back to terminal exploration when a truthful preview is not ready
- report blockers honestly instead of inventing fake green status

---

## What it does

Tekarab Sandbox follows a practical execution pipeline:

1. Analyze a GitHub repository
2. Detect language, repo type, and runnable signals
3. Infer setup steps and run steps
4. Apply command safety and rewrite logic when needed
5. Start an isolated session
6. Run setup and launch commands
7. Detect whether a real service becomes reachable
8. Return the most truthful user experience:
   - web preview
   - terminal flow
   - or deferred/manual-review outcome

---

## Current verified demo cases

This community demo currently focuses on a **small, verified support matrix**.

### Supported and verified
- Static/simple web app repositories
- Streamlit app repositories
- Node/Vite web app repositories

### Truthfully deferred
- Template repositories that require manual setup choices
- API-like repositories with unclear execution paths
- Repositories where safe execution cannot be inferred yet

### Verified outcomes so far
- Static/simple web preview case: **PASS**
- Streamlit app case: **PASS**
- Node/Vite web app case: **PASS**
- API-like/template repo with unclear execution path: **Truthful deferred**

This means the system already demonstrates both:
- successful end-to-end launches
- and honest fallback behavior when execution is not safely inferred

See the full matrix here:
- [docs/demo-cases.md](docs/demo-cases.md)

---

## What makes it different

Tekarab Sandbox is not trying to claim universal repository support.

Instead, it optimizes for:
- truthful status over fake success
- stable preview over flashy but unreliable behavior
- a small verified matrix before expanding scope
- clear fallback behavior when a launch path is uncertain

The goal is not to "run everything."
The goal is to become a reliable sandbox that tells the truth.

---

## Current demo scope

This repository is being prepared as a community-facing demo build.

Current priorities:
- keep the sandbox truthful
- keep the supported matrix small and reliable
- improve preview and terminal UX
- document what works now before expanding support

Not the current goal:
- full support for every repository type
- aggressive support claims
- pretending template or unclear repos are production-ready

---

## High-level architecture

Core components:

- `api.py`  
  Main API entrypoint

- `repo_analyzer.py`  
  Repository inspection and signal extraction

- `repo_decision.py`  
  Repo classification, readiness, and launch strategy selection

- `session_runtime.py`  
  Session lifecycle, command execution, server detection, preview readiness

- `presentation_resolver.py`  
  User-facing experience resolution for preview vs terminal fallback

- `command_rewriter.py`  
  Safety and rewrite layer for risky or misleading command plans

- `smart_error_hints.py`  
  Structured failure interpretation and next-step hints

- `smart_timeout.py`  
  Timeout heuristics based on repo and command context

- `interactive_detection.py`  
  Detection of interactive commands that should be avoided or rewritten

- `strategy_insights.py`  
  Strategy reasoning helpers

- `run_command_policy.py`  
  Execution policy and command safety controls

- `test_repo_matrix.py`  
  Matrix verification for supported repo categories

---

## Truthful behavior examples

### When a repo is supported
Tekarab Sandbox can:
- infer setup steps
- launch the app
- detect the reachable service
- expose a real public preview URL
- mark the primary experience as ready

### When a repo is unclear
Tekarab Sandbox does **not** pretend the app launched.

Instead, it can:
- reject unsafe automatic execution
- mark the result as unclear or deferred
- create a terminal-oriented exploratory session
- report the blockers honestly

This is a deliberate product choice, not a missing message.

---

## Example user experience

Depending on what the system finds, the primary experience may become:

- **Open Web App**  
  when a real preview is reachable

- **Open Terminal**  
  when the repository needs exploration or truthful fallback

This keeps the system honest and avoids fake "success" states.

---

## Current limitations

This is still a focused demo, not a finished universal runner.

Known limitations include:
- limited support matrix by design
- some template repos still classify as deferred
- some API-like repos still require manual review
- GitHub presentation and documentation are still being polished
- auxiliary tooling and cleanup are still in progress

---

## Near-term roadmap

Near-term priorities are intentionally narrow:

1. Keep the current support matrix stable
2. Improve terminal and preview clarity
3. Add better demo documentation and screenshots
4. Expand support only after current flows are consistently reliable

---

## Project status

Current state: **community demo in active refinement**

What is already real:
- repository analysis
- execution planning
- truthful fallback behavior
- preview detection
- verified working cases across multiple repo categories

What still needs polish:
- GitHub documentation
- showcase examples
- cleanup of remaining auxiliary/internal files
- broader repo coverage over time

---

## Philosophy

Tekarab Sandbox would rather tell the truth than impress you with fake success.

That sounds obvious, but apparently it needs to be said out loud on the internet.

---

## License

Not specified yet.
