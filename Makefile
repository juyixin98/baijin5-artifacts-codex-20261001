.PHONY: install fixtures serve test cov clean

install:
	pip install -r requirements.txt

fixtures:
	PYTHONPATH=src python3 -m defeasible.cli load-fixture fixtures/birds.json
	PYTHONPATH=src python3 -m defeasible.cli load-fixture fixtures/nixon_diamond.json
	PYTHONPATH=src python3 -m defeasible.cli load-fixture fixtures/team_defeat.json
	@# priority_cycle.json is a negative fixture used by the test-suite;
	@# loading it is rejected with state_conflict by design.

serve:
	PYTHONPATH=src python3 -m defeasible.cli serve

test:
	PYTHONPATH=src python3 -m pytest

cov:
	PYTHONPATH=src python3 -m coverage run --source=src/defeasible -m pytest -q
	PYTHONPATH=src python3 -m coverage report

clean:
	rm -rf data logs .pytest_cache .coverage __pycache__
	find . -type d -name __pycache__ -exec rm -rf {} +
