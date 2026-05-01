# Copilot Instructions for DevocionalesAPI — Python Pipeline Repository

## Project Identity

- **Repo:** `develop4god/DevocionalesAPI`
- **Stack:** Python 3, FastAPI, Google Gemini 2.5 Flash, SQLite, Ruff, Pytest
- **Entry points:** `seed_generation/main_batch_pipeline.py`, `API_Server_Seed.py`, `client_generate_from_seed.py`
- **Virtual env:** `.venv/` at project root — always activate before running any command
- **Architecture:** Modular pipeline — each stage (seed, generate, validate, patch) is a standalone module with a clear input/output contract

## COPILOT VALIDATION & SYNCHRONIZATION MANDATE

- ✅ On **every interaction**, read `.github/copilot-instructions.md` (this file)
- ✅ On **every interaction**, read `.github/SKILL.md` — the Python coding execution rules
- ✅ **CONFIRM READING BOTH FILES** at the start of each response before proceeding
- Purpose: Ensure you and the user are synchronized on standards, patterns, and quality gates

---

## Code Standards

### Always validate code after changes
- Run `ruff check . --fix && ruff format .` after every change
- Run `pytest tests/` for the affected module before reporting done
- Fix any errors immediately — do not report done with failing checks

### Test-driven development
- Every new function or class gets a test
- Run `pytest tests/<module>_test.py -v` before starting any task and after finishing
- Fix failing tests before submitting changes

### Keep code clean and formatted
- Use `ruff format .` to enforce consistent style
- Use `ruff check . --fix` to resolve lint issues automatically
- Use `ruff check .` to verify zero remaining issues

### Production code must remain functional
- Do not modify production modules unless the task explicitly requires it
- Justify any production change in the commit message
- Never break the checkpoint/resume contract — existing `.json` output files must remain loadable

---

## Architecture Guidelines

### Module Responsibilities (Single Responsibility)

| Module | Owns |
|---|---|
| `seed_generation/` | Seed file creation and batch orchestration |
| `generation/` | Gemini API calls, retry logic, checkpoint management |
| `validation/` | JSON schema validation, field checks, GUI validator |
| `patching/` | Content corrections, field-level patches |
| `bible/` | SQLite verse lookup, book name resolution, version mapping |
| `tests/` | All test files — mirrors the module structure above |

### SOLID Rules — HARD

**S — Single Responsibility:**
- Each function does one thing. A function that fetches AND validates AND writes is a violation — split it.
- Pipeline stages must not bleed into each other (generation code must not validate; validation code must not patch).

**O — Open/Closed:**
- New language support → add a new config/mapping entry, do NOT edit existing language logic
- New Bible version → add to the version registry, do NOT add `if version == "X"` chains in existing code

**I — Interface Segregation:**
- Do not add test-only parameters to production functions (`debug=False`, `dry_run=True` in signatures)
- Keep pipeline stage interfaces narrow — each stage takes its input contract and returns its output contract

**D — Dependency Inversion:**
- Functions depend on injected collaborators, not on hardcoded paths or global state
- File paths, model names, and API endpoints are parameters — never hardcoded inside functions

---

## Dependency Injection Rules

### Where hardcoded values are FORBIDDEN

| Location | Rule |
|---|---|
| Inside generation functions | File paths must be parameters |
| Inside validation functions | Schema definitions must be injected or imported from a single constants module |
| Inside Bible lookup functions | DB path must be a parameter |
| Inside API call functions | Model name must be a parameter — never hardcoded |

### Allowed pattern
```python
# ✅ Correct — path is a parameter
def load_checkpoint(output_path: Path) -> list[dict]:
    ...

# ❌ Hard block — path hardcoded inside function
def load_checkpoint() -> list[dict]:
    path = Path("~/python/DevocionalesAPI/output/checkpoint.json")
    ...
```

---

## 🎯 SKILL.md — The Execution Framework

Before applying any change, implementing any feature, or fixing any bug, read `.github/SKILL.md`.

It defines the six-step execution process:
- **Step 0:** What files to read before touching code
- **Step 1:** How to apply changes exactly as specified — no improvisation
- **Step 2:** Mandatory quality gates (ruff check → ruff format → pytest)
- **Step 3:** SOLID compliance verification
- **Step 4:** DI compliance (no hardcoded paths, models, or global state)
- **Step 5:** Test infrastructure reuse + coverage requirements
- **Step 6:** Report format

**SKILL.md is not optional.** Every code change follows its structure.

---

## Mandatory Quality Gates (Step 2)

Run these in order after **every change**, before reporting done. All three must pass:

1. **Lint + Fix** — `ruff check . --fix` (Zero tolerance — unfixed lint is rejected)
2. **Format** — `ruff format .` (Zero tolerance — unformatted code is rejected)
3. **Tests** — `pytest tests/<affected_module>_test.py -v` (All focused tests must pass)

```bash
# Full gate — run this after every change
source .venv/bin/activate && ruff check . --fix && ruff format . && echo "✅ Lint+Format OK"
```

---

## Development Workflow

```bash
# Activate environment
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Lint and fix
ruff check . --fix

# Format
ruff format .

# Run focused tests
pytest tests/<module>_test.py -v

# Run all tests
pytest tests/ -v
```

---

## Pipeline-Specific Rules

### Checkpoint/Resume Contract
- Never change the output JSON schema in a way that breaks existing checkpoint files
- Always test load + resume after any change to checkpoint logic
- Checkpoint keys are stable identifiers — do not rename them

### Gemini API Calls
- Retry logic: `3s → 15s → 30s → 60s` backoff on 503
- Model name is always a parameter — never hardcoded
- Prompts are constructed in a dedicated builder function — never inline

### Bible Verse Lookup
- DB path is always a parameter
- Three-tier book name resolver — do not collapse into one tier
- Always test with a known verse after any change to the resolver

### Validation
- `generic_reflection` check is DISABLED — do not re-enable
- Schema validation runs before content validation — never skip schema
- GUI validator (`validate_devocional_gui.py`) is a separate entry point — do not merge with CLI

---

## Guidelines

1. Keep the existing module structure and pipeline stage separation
2. Write tests for every new function or bugfix
3. Document public functions with docstrings (one-line summary + params)
4. Update `README.md` or `docs/` if changes impact usage, CLI args, or output schema

---

**Note for Copilot:**
Follow these instructions to maintain quality, consistency, and reliability in this pipeline repository.
