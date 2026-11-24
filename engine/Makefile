# Operate from engine/: `make lint`, `make test`, `make check`.
# PYTHON defaults to the local venv; override with `make PYTHON=python3 ...`.

PYTHON ?= .venv/bin/python
SRC := src
DOC ?=

.PHONY: help lint format-check typecheck openapi test check ci db migrate roundtrip provision rotate-status

help:
	@echo "make lint         ruff check + format check + openapi/error-code guard"
	@echo "make typecheck    mypy --strict on the package"
	@echo "make test         pytest"
	@echo "make check        all of the above"
	@echo "make ci           the whole gate: check + migrations + contract doc"
	@echo "make openapi      contract guard only (DOC=path/to/INTEGRATION.md)"
	@echo "make roundtrip    alembic up -> down -> up on a scratch database"
	@echo "make provision    create a tenant (SLUG=acme NAME=\"Acme\")"
	@echo "make db           start the dev-only bundled PostgreSQL and print its URL"
	@echo "make migrate      alembic upgrade head (needs ADAPT_MIGRATION_DATABASE_URL)"

lint: format-check typecheck openapi

format-check:
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .

# Every script, not a chosen list: a script that is not type-checked is a
# script that drifts.
typecheck:
	MYPYPATH=$(SRC) $(PYTHON) -m mypy $(SRC)/adapt_engine scripts

# Exits non-zero when a /v1 route lacks auth, an error response is missing the
# envelope, or errors.py emits a code absent from INTEGRATION.md §6.
openapi:
	$(PYTHON) scripts/check_openapi.py $(if $(DOC),--doc $(DOC),)

test:
	PYTHONPATH=$(SRC) $(PYTHON) -m pytest -q

# The whole gate, exactly as CI runs it: one script, so "green locally" and
# "green in CI" cannot drift into two different definitions.
ci:
	./scripts/ci.sh

# up -> down -> up on a database of its own; the down migration is the one
# nobody runs until an incident.
roundtrip:
	PYTHONPATH=$(SRC) $(PYTHON) -m pytest -q tests/test_migrations.py -k up_down_up

# Provisioning is an owner operation; the runtime role cannot create a tenant
# or a data key.
provision:
	$(PYTHON) scripts/provision_tenant.py --slug "$(SLUG)" --name "$(NAME)"

rotate-status:
	$(PYTHON) scripts/rotate_master_key.py status

# Dev-only: the bundled server is an alternative to `docker compose up postgres`.
db:
	$(PYTHON) scripts/dev_postgres.py

# Migrations run as adapt_owner (ADAPT_MIGRATION_DATABASE_URL), never the app role.
migrate:
	$(PYTHON) -m alembic upgrade head

check: lint test
