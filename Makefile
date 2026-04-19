PYTHON := .venv/bin/python
PIP := .venv/bin/pip
UVICORN := .venv/bin/uvicorn
ALEMBIC := .venv/bin/alembic
CELERY := .venv/bin/celery
NPM := npm
COMPOSE_CMD := $(shell if docker compose version >/dev/null 2>&1; then echo "docker compose"; elif command -v docker-compose >/dev/null 2>&1; then echo "docker-compose"; else echo "docker compose"; fi)

.PHONY: bootstrap-local doctor-local sync-env infra-up infra-down migrate run-backend run-admin run-worker run-beat run-all

bootstrap-local:
	./scripts/bootstrap_local.sh

doctor-local:
	$(PYTHON) scripts/local_env_doctor.py

sync-env:
	python3 scripts/sync_freeswitch_env.py

infra-up:
	$(COMPOSE_CMD) up -d postgres redis

infra-down:
	$(COMPOSE_CMD) down

migrate:
	$(ALEMBIC) upgrade head

run-backend:
	$(UVICORN) app.main:app --host 0.0.0.0 --port 8000 --reload

run-admin:
	$(NPM) --prefix admin-panel run dev -- --host 0.0.0.0 --port 5173

run-worker:
	$(CELERY) -A app.workers.celery_app worker --loglevel=info -Q default

run-beat:
	$(CELERY) -A app.workers.celery_app beat --loglevel=info

run-all:
	./scripts/run_local_stack.sh
