
# 📖 DevocionalesAPI Batch Pipeline (2026)

This pipeline generates yearly devotionals in multiple languages using Anthropic Claude's Message Batches API. It is robust to JSON errors and automates the full process from seed to validated output.

## 🚦 Pipeline Overview

1. 🌱 **Seed Preparation**
   - Seeds are JSON files with all required verse data for a year (see `seed_generation/2025/seeds/` and `2026/seeds/`).

2. 📤 **Batch Submission**
   - Use `batch_claude_submit.py` to submit all seed entries as a single batch to Anthropic.
   - CLI: `python batch_claude_submit.py --seed <seed.json> --lang <code> --version <code> --output <dir>`
   - This creates a `batch_state_*.json` file with batch ID and metadata.

3. 📥 **Batch Collection**
   - Use `batch_claude_collect.py` to poll the batch, download results, and build devotional records.
   - CLI: `python batch_claude_collect.py --state <batch_state_*.json>`
   - Now includes robust JSON repair to handle malformed Claude output.
   - Outputs:
     - 📄 `raw_<lang>_<version>_batch_<ts>.json` (all successful entries)
     - ❌ `batch_errors_<lang>_<version>_<ts>.json` (any failures)
     - ⚠️ `batch_val_warnings_<lang>_<version>_<ts>.json` (validation warnings)

4. 🛠️ **Repair (if needed)**
   - If any entries fail, use `batch_repair_failed.py` to re-fetch, repair, and (if needed) regenerate via API.
   - CLI: `python batch_repair_failed.py --state <batch_state> --errors <batch_errors> --existing <partial_output> --output <final_output>`

5. ✅ **Validation**
   - Use `validation_helper.py` to check output files for length, prayer ending, duplicate words, etc.
   - CLI: `python validation_helper.py <output.json>`

## 🏃 Typical Workflow

1. 🌱 Prepare your seed file in `seeds/`.
2. 📤 Submit the batch:
   ```bash
   python batch_claude_submit.py --seed seeds/2026/seed_tl_ASND_for_2026.json --lang tl --version ASND --output 2026/yearly_devotionals/TL
   ```
3. 📥 Collect results:
   ```bash
   python batch_claude_collect.py --state batch_state_tl_ASND_20260415_173212.json
   ```
4. 🛠️ If errors remain, repair:
   ```bash
   python batch_repair_failed.py --state batch_state_tl_ASND_20260415_173212.json --errors 2026/yearly_devotionals/batch_errors_tl_ASND_20260415_182836.json --existing 2026/yearly_devotionals/TL/279-Devocional_year_2026_tl_ASND.json --output 2026/yearly_devotionals/TL/Devocional_year_2026_tl_ASND.json
   ```
5. ✅ Validate:
   ```bash
   python validation_helper.py 2026/yearly_devotionals/TL/Devocional_year_2026_tl_ASND.json
   ```

## 🚀 One-Click Orchestration

You can use the new `main_batch_pipeline.py` to run all steps in sequence (see that file for usage).

---

**ℹ️ Note:**
- Requires `anthropic` and `python-dotenv` packages.
- Set your `ANTHROPIC_API_KEY` in a `.env` file or environment variable.
- All scripts are in `seed_generation/`.
