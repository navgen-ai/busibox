# Testing

**Purpose**: Correct test invocation patterns using `make` commands

## Running Tests

ALWAYS use `make test-docker` for local development. NEVER run `pytest` directly.

### Basic Usage

```bash
make test-docker SERVICE=agent       # All non-slow, non-GPU tests
make test-docker SERVICE=authz       # All authz tests
```

### Targeting Specific Tests

Use `ARGS=` (NOT `PYTEST_ARGS=`) to pass pytest arguments through the Makefile:

```bash
# Specific test function
make test-docker SERVICE=agent ARGS="tests/integration/test_schema_extraction.py::test_clean_markdown_for_extraction"

# Multiple specific tests (space-separated)
make test-docker SERVICE=agent ARGS="tests/integration/test_file.py::test_one tests/integration/test_file.py::test_two"

# Test directory
make test-docker SERVICE=agent ARGS="tests/unit"

# Specific test file
make test-docker SERVICE=agent ARGS="tests/integration/test_schema_extraction.py"
```

### Including Slow/GPU Tests

```bash
# FAST=1 is the default (skips @pytest.mark.slow and @pytest.mark.gpu)
make test-docker SERVICE=agent FAST=0   # Include slow/GPU tests

# FAST is automatically disabled when ARGS starts with tests/
make test-docker SERVICE=agent ARGS="tests/integration/test_slow.py"
```

### Discovering Tests

```bash
make test-docker ACTION=list                                    # Overview of all services
make test-docker ACTION=list SERVICE=agent                      # List agent test files
make test-docker ACTION=list SERVICE=agent CATEGORY=unit        # List unit test files
make test-docker ACTION=list SERVICE=agent CATEGORY=unit DETAIL=full  # Show individual test IDs
```

## Common Mistakes

```bash
# ❌ WRONG: PYTEST_ARGS doesn't work through the Makefile
make test-docker SERVICE=agent PYTEST_ARGS="-k test_name"

# ✅ CORRECT: Use ARGS=
make test-docker SERVICE=agent ARGS="tests/integration/test_file.py::test_name"

# ❌ WRONG: -k filters have quoting issues through make
make test-docker SERVICE=agent ARGS="-k 'test_a or test_b'"

# ✅ CORRECT: Use explicit test paths instead of -k
make test-docker SERVICE=agent ARGS="tests/path/file.py::test_a tests/path/file.py::test_b"

# ❌ WRONG: Running pytest directly (misses env setup, secrets, PYTHONPATH)
cd srv/agent && pytest tests/ -v

# ✅ CORRECT: Always use make
make test-docker SERVICE=agent
```

## Remote Testing

```bash
make test-local SERVICE=agent INV=staging                    # Against staging
make test-local SERVICE=authz INV=production                 # Against production
make test-local SERVICE=data INV=staging ARGS="-m pvt"       # PVT tests only
make test-local SERVICE=data INV=staging WORKER=1 FAST=0     # Full pipeline with worker
```

## Test Markers

| Marker | Description | Skipped by FAST |
|--------|-------------|-----------------|
| `@pytest.mark.unit` | Unit tests | No |
| `@pytest.mark.integration` | Integration tests | No |
| `@pytest.mark.pvt` | Post-deployment validation | No |
| `@pytest.mark.slow` | Slow tests (model loading) | Yes |
| `@pytest.mark.gpu` | GPU-required tests | Yes |

## Related

- `docs/developers/architecture/08-tests.md` - Full testing architecture
- `TESTING.md` - Testing overview
- `CLAUDE.md` - Quick reference
