# GEP Dry Run Files

This folder contains dry-run batch files and validation outputs.

## Purpose

Dry run files are used for:
- Testing batch configurations without submitting to API
- Validating JSONL format before submission
- Cost estimation before running full batches
- Debugging prompt construction

## Structure

```
dry_run/
├── batch_input/          ← Dry run batch JSONL files
├── validation_reports/   ← Genome validation reports
└── test_outputs/         ← Test run outputs
```

## Creating Dry Run Files

### Using build_batch.py
```bash
python3 build_batch.py \
  --lang es --version RVR1960 --year 2025 \
  --phase 1 \
  --local data/source/Devocional_year_2025_es_RVR1960.json \
  --output dry_run/batch_input/test_es_RVR1960_2025_p1.jsonl
```

### Using genome_validator.py
```bash
python3 genome_validator.py \
  --file data/source/Devocional_year_2025_es_RVR1960.json \
  --lang es --version RVR1960 --year 2025 \
  --export dry_run/validation_reports/es_RVR1960_2025_validation.json
```

## Usage

Dry run files can be inspected manually or used for:
1. **Token estimation** — Count tokens before submission
2. **Prompt validation** — Verify system/user prompts are correct
3. **Format validation** — Ensure JSONL structure is valid
4. **Genome testing** — Test genome pattern injection

## Notes

- Dry run files are not submitted to APIs
- They are safe to commit to version control (no API keys)
- Use them to test changes before production runs
