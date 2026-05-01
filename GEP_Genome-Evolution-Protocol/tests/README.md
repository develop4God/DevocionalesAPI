# GEP Tests

Comprehensive test suite for the Genome Evolution Protocol.

## Test Files

- **test_lang_registry.py** — Language registry validation (202 tests)
  - Tests all registered languages
  - Validates version checking
  - Tests fil/tl migration
  - Integration tests with prompts.py and source.py

- **test_pipeline.py** — Full pipeline integration test
  - Runs overnight mode on 5 stub entries
  - Validates audit log creation
  - Tests genome accumulation
  - Validates skip-reviewed logic

- **test_genome_validator.py** — Genome pattern validation (NEW)
  - Pattern matching in entries
  - Validation report generation
  - File validation
  - Edge cases and multiple categories

## Running Tests

### Run all tests
```bash
python3 tests/test_lang_registry.py
python3 tests/test_pipeline.py
python3 tests/test_genome_validator.py
```

### Run specific test
```bash
python3 tests/test_genome_validator.py
```

## Test Coverage

- ✅ Language registration and validation
- ✅ Version validation
- ✅ Genome pattern matching
- ✅ Pipeline integration
- ✅ Audit log creation
- ✅ File validation
- ✅ Edge cases and error handling

## Expected Output

All tests should pass with exit code 0:
```
════════════════════════════════════════════════════════════
  Results: 202 passed / 0 failed
  🎉 All tests passed
════════════════════════════════════════════════════════════
```
