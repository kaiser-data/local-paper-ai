### Task 1: Scaffold

**Files:**
- Create: `pyproject.toml`, `.python-version`, `src/local_paper_ai/__init__.py`, `tests/test_smoke.py`, `.github/workflows/ci.yml`
- Modify: `.gitignore` (append venv/cache entries if missing)

**Interfaces:**
- Produces: importable package `local_paper_ai` with `__version__ = "0.1.0"`; console script `local-paper-ai` → `local_paper_ai.cli:app` (created in Task 13; until then the script entry exists but is unused).

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "local-paper-ai"
version = "0.1.0"
description = "Companion for Paperless-ngx: which paper originals may I shred? Deadlines, verified backups, local-first AI."
readme = "README.md"
license = "MIT"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115",
    "httpx>=0.27",
    "jinja2>=3.1",
    "psutil>=6.0",
    "pydantic>=2.8",
    "python-multipart>=0.0.9",
    "pyyaml>=6.0",
    "typer>=0.12",
    "uvicorn>=0.30",
]

[project.scripts]
local-paper-ai = "local_paper_ai.cli:app"

[dependency-groups]
dev = [
    "mypy>=1.11",
    "pytest>=8.3",
    "pytest-cov>=5.0",
    "respx>=0.21",
    "ruff>=0.6",
    "types-PyYAML",
    "types-psutil",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/local_paper_ai"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "SIM", "RUF"]

[tool.mypy]
strict = true
packages = ["local_paper_ai"]
mypy_path = "src"
plugins = ["pydantic.mypy"]

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = ["integration: needs a live Paperless-ngx (LPA_IT_URL, LPA_IT_TOKEN)"]
addopts = "-m 'not integration'"
```

- [ ] **Step 2: Package, pin, smoke test**

`.python-version`:
```
3.12
```

`src/local_paper_ai/__init__.py`:
```python
"""local-paper-ai: may I throw this paper away?"""

__version__ = "0.1.0"
```

`tests/test_smoke.py`:
```python
import local_paper_ai


def test_version() -> None:
    assert local_paper_ai.__version__ == "0.1.0"
```

- [ ] **Step 3: Install and run**

Run: `uv sync && uv run pytest -q && uv run ruff check . && uv run mypy`
Expected: `1 passed`, ruff `All checks passed!`, mypy `Success: no issues found`. A `uv.lock` is created; commit it.

- [ ] **Step 4: CI workflow** `.github/workflows/ci.yml`

```yaml
name: ci
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv sync --frozen
      - run: uv run ruff format --check .
      - run: uv run ruff check .
      - run: uv run mypy
      - run: uv run pytest --cov=local_paper_ai --cov-report=term-missing
```

- [ ] **Step 5: `.gitignore`.** Make sure it contains `.venv/`, `__pycache__/`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `.coverage`, `data/`, `config.yaml`, `.env`.

- [ ] **Step 6: Commit and push**

```bash
git add -A
git commit -m "chore: scaffold python package, tooling and CI

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git push
```
