PYTHON ?= python3
VENV   := .venv
PY     := $(VENV)/bin/python

.PHONY: setup up down demo test graph clean

setup:  ## create the virtualenv and install dependencies
	$(PYTHON) -m venv $(VENV)
	$(PY) -m pip install -q --upgrade pip
	$(PY) -m pip install -q -r requirements.txt
	@test -f .env || cp .env.example .env
	@echo "Now put your ANTHROPIC_API_KEY in .env"

up:  ## start Postgres (checkpoints) and Langfuse (tracing)
	docker compose up -d --wait

down:
	docker compose down

demo:  ## run the agent on all three sample sources
	$(PY) -m agent build data/orders.csv "orders, loaded daily, one row per order"
	$(PY) -m agent build data/customers.csv "customers exported from the CRM, loaded daily, one row per customer"
	$(PY) -m agent build data/payments.csv "payments, loaded hourly, one row per payment"

test:  ## unit + dbt tests; the Claude end-to-end test runs only when ANTHROPIC_API_KEY is set
	$(PY) -m pytest -ra

graph:  ## print the graph as Mermaid
	@$(PY) -m agent graph

clean:  ## remove sandboxes and dbt build output
	rm -rf .sandbox dbt_project/target dbt_project/logs dbt_project/*.duckdb
