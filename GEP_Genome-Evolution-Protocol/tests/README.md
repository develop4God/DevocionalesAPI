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

- **test_main.py** — Interactive menu system (NEW)
  - Session management (load, save, defaults)
  - Menu navigation and input handling
  - Command building for all pipeline stages
  - Integration with lang_registry
  - Provider loading from providers.yml
  - Edge cases and error handling

## Running Tests

### Run all tests
```bash
python3 tests/test_lang_registry.py
python3 tests/test_pipeline.py
python3 tests/test_genome_validator.py
# Note: test_main.py requires pytest
python3 -m pytest tests/test_main.py -v
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
