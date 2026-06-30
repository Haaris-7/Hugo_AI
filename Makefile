.PHONY: setup start stop restart test lint

# First-time setup: build the stack and open the setup wizard.
setup:
	./setup.sh

# Start the stack (without rebuilding).
start:
	docker compose up -d

# Stop the stack.
stop:
	./setup.sh --stop

# Rebuild and restart (use after code changes).
restart:
	./setup.sh --restart

# Run backend tests.
test:
	docker compose run --rm dev python -m pytest -q

# Run linter.
lint:
	docker compose run --rm dev python -m ruff check backend hermes-plugin alembic
