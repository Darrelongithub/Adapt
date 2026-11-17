# Operate from engine/: `make lint`, `make test`, `make check`.
# PYTHON defaults to the local venv; override with `make PYTHON=python3 ...`.

PYTHON ?= .venv/bin/python
SRC := src
DOC ?=

.PHONY: help lint format-check typecheck openapi test check

help:
	@echo "make lint         ruff check + format check + openapi/error-code guard"
	@echo "make typecheck    mypy --strict on the package"
	@echo "make test         pytest"
	@echo "make check        all of the above"
	@echo "make openapi      contract guard only (DOC=path/to/INTEGRATION.md)"

lint: format-check typecheck openapi

format-check:
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .

typecheck:
	MYPYPATH=$(SRC) $(PYTHON) -m mypy $(SRC)/adapt_engine scripts/check_openapi.py

# Exits non-zero when a /v1 route lacks auth, an error response is missing the
# envelope, or errors.py emits a code absent from INTEGRATION.md §6.
openapi:
	$(PYTHON) scripts/check_openapi.py $(if $(DOC),--doc $(DOC),)

test:
	PYTHONPATH=$(SRC) $(PYTHON) -m pytest -q

check: lint test
