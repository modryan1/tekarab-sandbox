# Demo Cases

This document summarizes the current verified demo coverage for Tekarab Sandbox.

The goal of this demo is not to claim universal repository support.
The goal is to show a small set of truthful, repeatable outcomes.

---

## Verified demo matrix

### 1) Static / simple web app
**Status:** PASS

**Outcome:**
- repository analyzed successfully
- runnable plan inferred
- web preview became available
- primary experience resolved to a real preview link

**Why it matters:**
This proves the sandbox can serve simple web-facing repositories end-to-end.

---

### 2) Streamlit app
**Status:** PASS

**Outcome:**
- repository analyzed successfully
- Python setup path worked
- Streamlit launch succeeded
- reachable service was detected
- public preview became ready

**Why it matters:**
This proves the sandbox can support a real Python app flow, not just static content.

---

### 3) Node / Vite web app
**Status:** PASS

**Outcome:**
- repository analyzed successfully
- install/build/preview strategy was inferred
- preview server was detected
- public preview became ready
- primary experience resolved to web preview

**Why it matters:**
This proves the sandbox can support a real modern frontend app flow.

---

### 4) API-like or template repository with unclear execution path
**Status:** Truthful deferred

**Outcome:**
- repository was analyzed
- automatic execution was not claimed
- the system did not fake a successful launch
- fallback behavior remained honest and safe

**Why it matters:**
This is a core product behavior.
Tekarab Sandbox should defer unclear repos honestly instead of inventing fake success.

---

## What "truthful fallback" means

When Tekarab Sandbox cannot safely infer a real runnable path, it should not pretend the repository worked.

Instead, it should:
- avoid fake success states
- avoid misleading preview claims
- expose blockers clearly
- fall back to terminal-oriented exploration when appropriate

This behavior is intentional.

---

## Current supported demo categories

The current community demo is intentionally narrow.

### Supported and verified
- Static/simple web apps
- Streamlit apps
- Node/Vite web apps

### Deferred by design when unclear
- template repositories
- API-like repositories with unclear execution flow
- repos that need manual setup decisions before safe launch

---

## Why the scope is narrow

A small truthful matrix is more valuable than broad fake support.

This project currently prioritizes:
- truthful execution status
- stable preview behavior
- clear fallback behavior
- a reliable demo that can be shown publicly

Support breadth can expand later.
Trust has to come first.

---

## Current demo message

Tekarab Sandbox can already demonstrate two important capabilities:

1. It can launch supported repositories end-to-end.
2. It can defer unsupported or unclear repositories honestly.

That combination is the current value of the demo.
