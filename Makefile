# PowerGrid — start everything with: make
# App:  http://127.0.0.1:5000
# Login: admin@powergrid.in / powergrid123
# Stop the database: make stop

PYTHON       ?= python3
VENV         := env
BIN          := $(VENV)/bin
DB_CONTAINER := powergrid-mysql

.DEFAULT_GOAL := run
.PHONY: run setup db stop test up down

run: setup db
	$(BIN)/python app.py

setup: $(BIN)/python .env $(VENV)/.deps

$(BIN)/python:
	$(PYTHON) -m venv $(VENV)

.env:
	cp .env.example .env

$(VENV)/.deps: requirements.txt $(BIN)/python
	$(BIN)/pip install -r requirements.txt
	touch $(VENV)/.deps

db:
	@docker start $(DB_CONTAINER) >/dev/null 2>&1 || \
		docker run -d --name $(DB_CONTAINER) \
			-e MYSQL_ROOT_PASSWORD=powergrid \
			-e MYSQL_DATABASE=power_db \
			-e MYSQL_USER=powergrid_user \
			-e MYSQL_PASSWORD=powergrid \
			-p 3306:3306 \
			mysql:8.0
	@echo "Waiting for MySQL..."
	@i=0; \
	until docker exec $(DB_CONTAINER) mysqladmin ping -h 127.0.0.1 -uroot -ppowergrid --silent >/dev/null 2>&1; do \
		i=$$((i + 1)); \
		if [ $$i -gt 60 ]; then echo "MySQL did not become ready in time."; exit 1; fi; \
		sleep 2; \
	done
	@echo "MySQL is ready."

stop:
	-docker stop $(DB_CONTAINER)

test: setup
	$(BIN)/python -m pytest -q

up:
	docker compose up --build -d

down:
	docker compose down
