.PHONY: build up down restart logs shell migrate makemigrations load-data setup psql redis-cli test help

# ── Docker lifecycle ──────────────────────────────────────────────────────────

build:
	docker-compose build

up:
	docker-compose up -d

down:
	docker-compose down

restart:
	docker-compose restart api

logs:
	docker-compose logs -f api

# ── First-time setup ──────────────────────────────────────────────────────────

## Full setup: build image, start all services, load fuel data
setup: build up load-data

## Load fuel station prices from CSV and geocode locations (~20-40 min first run)
load-data:
	docker-compose exec api python manage.py load_fuel_data


# ── Tests ─────────────────────────────────────────────────────────────────────

## Run full test suite inside the API container (no Postgres or Redis needed)
test:
	docker-compose exec api pytest -v

## Run tests locally (requires: pip install -r requirements.txt)
test-local:
	pytest -v

# ── Database ──────────────────────────────────────────────────────────────────

migrate:
	docker-compose exec api python manage.py migrate

makemigrations:
	docker-compose exec api python manage.py makemigrations

# ── Dev utilities ─────────────────────────────────────────────────────────────

shell:
	docker-compose exec api python manage.py shell

psql:
	docker-compose exec postgres psql -U $${DB_USER} -d $${DB_NAME}

redis-cli:
	docker-compose exec redis redis-cli

# ── Help ──────────────────────────────────────────────────────────────────────

help:
	@echo ""
	@echo "Usage: make <target>"
	@echo ""
	@echo "  setup               Build, start services, and load fuel data (first-time)"
	@echo "  build               Build Docker image"
	@echo "  up                  Start all services in background"
	@echo "  down                Stop all services"
	@echo "  restart             Restart the API container"
	@echo "  logs                Tail API logs"
	@echo ""
	@echo "  load-data           Load + geocode fuel stations (~20-40 min first run)"
	@echo ""
	@echo "  test                Run test suite inside Docker container"
	@echo "  test-local          Run test suite locally"
	@echo ""
	@echo "  migrate             Run Django migrations"
	@echo "  makemigrations      Generate new migration files"
	@echo "  shell               Open Django shell"
	@echo "  psql                Open Postgres CLI"
	@echo "  redis-cli           Open Redis CLI"
	@echo ""
