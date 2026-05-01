---
name: python-pipeline-coding-agent
description: Python coding agent execution rules for DevocionalesAPI (FastAPI + Gemini + SQLite pipeline). Load this skill before applying any delegation block, implementing any feature, or making any code change in the DevocionalesAPI repo. Enforces mandatory quality gates (ruff check, ruff format, pytest), SOLID + DI compliance, no hardcoded paths or model names, and test coverage on every iteration. Use when the user says "apply this", "implement this", "make this change", "fix this bug", "add this feature", or hands you any delegation block targeting Python pipeline code.
---

# Python Pipeline Coding Agent — Execution Rules

You are a coding agent executing tasks in the `DevocionalesAPI` Python pipeline. Your job is to apply changes exactly as specified, leave the codebase cleaner than you found it, and verify your own work before declaring done.

You do not design architecture. You do not decide between pipeline stages. You apply what is given, stay in scope, and report back.

---

## Project Identity

- **Repo:** `develop4god/DevocionalesAPI`
- **Stack:** Python 3, FastAPI, Google Gemini 2.5 Flash, SQLite, Ruff, Pytest
- **Virtual env:** `.venv/` at project root
- **Pipeline stages:** seed → generate → validate → patch (each is an independent module)
- **Test directory:** `tests/` — mirrors module structure

---

## Step 0 — Read Before Touching

Before writing a single line:

1. Read every file named in the task
2. Read the direct imports of each changed file
3. If the task adds or changes a pipeline stage — read its input/output contract and existing tests
4. If the task changes Bible lookup logic — read `bible/` module and the SQLite resolver

**Never apply a diff to a file you haven't read.** Stale assumptions produce broken pipelines.

### Think Before Coding

Before starting implementation:
- **State your assumptions explicitly.** If uncertain about scope or intent, say so.
- **If multiple valid interpretations exist, present them** — don't pick one silently.
- **If a simpler approach exists that still meets all quality gates, say so.** Push back when warranted.
- **If something is unclear, stop.** Name what's confusing. Ask. Do not guess.

---

## Step 1 — Apply the Task

Follow the delegation block exactly:

- Apply Before/After Python blocks verbatim — do not paraphrase or reinterpret
- Do NOT refactor anything outside the changed functions
- Do NOT add new files, classes, or dependencies not listed in the task
- Do NOT change function signatures beyond what is specified
- If anything is ambiguous or contradicts what you see in the file — **stop and flag it**

### Surgical Changes — Touch Only What You Must

- Do NOT improve adjacent comments or style in unchanged functions
- Do NOT refactor pre-existing issues — mention them in **Flags for Architect** instead
- **Remove** imports/variables that **YOUR** changes made unused
- Do NOT remove pre-existing dead code unless the task explicitly asks for it
- **Test:** every changed line must trace directly to the user's request. If it doesn't, remove it.

### Simplicity Rule

- Minimum code that solves the problem — no speculative features
- No abstractions for code used only once — unless required for testability
- Ask yourself: *"Would a senior engineer say this is overcomplicated?"* If yes, simplify.
- **Exception — do not simplify away:** retry logic, checkpoint contracts, three-tier book resolver. These are intentional, not over-engineering.

---

## Step 2 — Mandatory Quality Gates

Run these in order after every change, before reporting done. All three must pass.

### Gate 1 — Lint + Fix
```bash
source .venv/bin/activate && ruff check . --fix
```
Fix all issues. Zero tolerance — unfixed lint is rejected.

### Gate 2 — Format
```bash
ruff format .
```
Zero tolerance — unformatted code is rejected.

### Gate 3 — Tests (FOCUSED TESTING ONLY)

**CRITICAL: Never run the full test suite blindly.** Run only focused tests on:
1. The specific test file for the module you changed
2. Existing tests directly related to the changed code

```bash
# Example: changed bible/resolver.py → run only:
pytest tests/bible/test_resolver.py -v

# Example: changed generation/checkpoint.py → run only:
pytest tests/generation/test_checkpoint.py -v
```

**Test coverage is mandatory.** If no tests exist for your changes → ADD them per Step 5.

All focused tests must pass. If a test breaks due to your change — fix it. If it was already broken — flag it in your report, do not silently skip.

---

## Step 3 — SOLID Compliance Check

Verify your changes before reporting done.

### S — Single Responsibility
- Does each function you touched do exactly one thing?
- Did you mix fetching + validating + writing in the same function? → Split it.

### O — Open/Closed
- New language support → new config entry, not an `if lang == "X"` chain in existing code
- New Bible version → new registry entry, not new conditionals inside existing lookup

### L — Liskov Substitution
- If you modified a function signature — do all callers still work without changes?
- If you added a test stub — does it behave identically to the real function for the tested paths?

### I — Interface Segregation
- Did you add test-only parameters to production functions (`debug=False`, `mock=None`)?
  → **Hard block** — use dependency injection instead
- Did you add unrelated functionality to an existing module?

### D — Dependency Inversion
- Are file paths, model names, and DB paths parameters — not hardcoded?
- Does the function accept collaborators rather than creating them internally?

---

## Step 4 — DI Compliance Check

Hard rule set. Violations are blockers — fix before reporting done.

### Hardcoded values — FORBIDDEN inside functions

| What | Rule |
|---|---|
| File paths (`~/python/...`, `./output/...`) | Must be parameters — never hardcoded |
| Model name (`gemini-2.5-flash`) | Must be a parameter |
| API endpoint URLs | Must come from config/constants |
| SQLite DB path | Must be a parameter |
| Language codes inside logic | Must come from a registry/config |

### Allowed pattern

```python
# ✅ Correct
def run_generation(output_path: Path, model: str, checkpoint: list[dict]) -> list[dict]:
    ...

# ❌ Hard block
def run_generation() -> list[dict]:
    output_path = Path("~/python/DevocionalesAPI/output")
    model = "gemini-2.5-flash"
    ...
```

### Global state — FORBIDDEN

| Antipattern | Consequence |
|---|---|
| Module-level mutable state (`CACHE = {}` mutated at runtime) | Hard block |
| `global` keyword inside functions | Hard block |
| Singleton pattern outside a dedicated factory | Hard block |
| `os.environ` read inside a function (not at startup) | Flag — move to config init |

---

## Antipatterns — Hard Blocks

Never introduce these. If you see them already, do not replicate and flag in your report.

| Antipattern | Why blocked |
|---|---|
| Hardcoded path inside function body | Untestable, breaks on environment change |
| `print()` as the only error reporting | Use `logging` — print is for CLI output only |
| Bare `except:` or `except Exception: pass` | Swallows real errors silently |
| `time.sleep()` in production logic without configurable duration | Untestable |
| Mutating input arguments | Causes hidden side effects across pipeline stages |
| `# type: ignore` without a documented reason | Silent suppression of real issues |
| Test logic inside production modules (`if __name__ == "__main__": test_...`) | Separate test files only |

---

## Step 5 — Test Infrastructure: Reuse Before You Write

**Before writing a single test line — check `tests/helpers/` for existing fixtures.**

### Test file structure

```
tests/
├── helpers/
│   ├── fixtures.py        # Shared sample dicts, mock verse data, fake checkpoint lists
│   ├── db_helpers.py      # In-memory SQLite setup for Bible lookup tests
│   └── api_helpers.py     # Fake Gemini response builder
├── bible/
│   └── test_resolver.py
├── generation/
│   └── test_checkpoint.py
├── validation/
│   └── test_schema.py
├── patching/
│   └── test_patcher.py
└── seed_generation/
    └── test_main_pipeline.py
```

### Test coverage requirements

| New code | Required tests |
|---|---|
| New pipeline function | Unit test: happy path + error path |
| New Bible resolver tier | Unit test: known verse → correct output + unknown book → graceful fallback |
| New checkpoint logic | Unit test: write → load round-trip + corrupted file → recovers |
| New Gemini call wrapper | Unit test with fake response: parses correctly + 503 → retries |
| New validation rule | Unit test: valid entry passes + invalid entry fails with correct message |
| New CLI argument | Unit test: arg parsed correctly + missing required arg → clear error |
| New patch function | Unit test: field updated correctly + no mutation of other fields |

### Test quality rules

**Test real pipeline behavior — not implementation details.**
- ✅ `"Given a 503 response, the generator retries with backoff and eventually returns the result"`
- ❌ `"When _retry_count reaches 3, _sleep_duration is 30"`

**Use `tmp_path` (pytest fixture) for file I/O tests** — never write to real project directories in tests.

**Tests must be deterministic:**
- Patch `time.sleep` in retry tests — never actually wait
- Use `unittest.mock.patch` for Gemini API calls — never hit the real API in tests
- Use in-memory SQLite for Bible lookup tests — never depend on a file on disk

**Error paths are not optional:**
- Every function that can raise must have a test for the failure case
- A test suite with only happy paths is incomplete — flag it

**No useless assertions:**
- `assert result is not None` with no further check → delete or strengthen
- Commented-out assertions → restore or delete

---

## Step 6 — Report Format

Always end your response in this exact structure:

```
✅ Changes Applied
[File name] — what was changed (1 line per file)

🔬 Quality Gates
- ruff check --fix: ✅ clean / ❌ [issue]
- ruff format: ✅ clean / ❌ [issue]
- pytest (focused): ✅ [N] passed / ❌ [N failed — list them]

🧱 SOLID + DI Check
✅ No violations found
— OR —
⚠️ [Violation title] — [file:line] — [why it's a problem]

🧪 Tests Added
[Test file] — [what is covered]
— OR —
⚠️ No new tests added — [reason]

🚫 Flags for Architect
[Anything ambiguous, pre-existing violations found, scope questions, or blockers]
— OR —
None
```

---

## Non-Negotiable Rules Summary

| Rule | Consequence of violation |
|---|---|
| `ruff check` not clean | Do not report done |
| `ruff format` not clean | Do not report done |
| Focused `pytest` has failures | Do not report done — fix or flag |
| Hardcoded path inside function | Hard block — fix before done |
| Hardcoded model name inside function | Hard block — fix before done |
| Global mutable state | Hard block — fix before done |
| New code without tests | Hard block — add tests |
| Improvising beyond task scope | Not allowed — flag and ask |
| `generic_reflection` check re-enabled | Hard block — this was intentionally disabled (264 false positives) |
| Checkpoint schema broken | Hard block — existing output files must remain loadable |
