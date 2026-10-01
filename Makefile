.PHONY: install samples demo serve test test-fast test-cov clean

PYTHON ?= python3

install:
	$(PYTHON) -m pip install -e ".[test]"

samples:
	$(PYTHON) scripts/generate_sample.py

demo:
	$(PYTHON) scripts/run_demo.py

serve:
	$(PYTHON) -m uvicorn app.api.main:app --host 127.0.0.1 --port 8000

test:
	$(PYTHON) -m pytest tests/ -v

test-fast:
	$(PYTHON) -m pytest tests/ -m "not mcsim" -q

test-cov:
	$(PYTHON) -m pytest tests/ -q --cov=app --cov-report=term-missing

clean:
	rm -rf .pytest_cache **/__pycache__
	rm -f data/*.db data/*.db-wal data/*.db-shm
