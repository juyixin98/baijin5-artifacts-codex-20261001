.PHONY: install test cov reproduce serve clean

install:
	pip install -r requirements.txt && pip install -e .

test:
	python3 -m pytest tests/ -p no:warnings

cov:
	python3 -m pytest tests/ -p no:warnings --cov=src/aipw --cov-report=term-missing

reproduce:
	python3 scripts/reproduce.py

serve:
	AIPW_DB=runs.db uvicorn aipw.api:app --port 8000

clean:
	rm -rf build dist *.egg-info src/*.egg-info .pytest_cache artifacts runs.db
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
