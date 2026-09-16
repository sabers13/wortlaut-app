PYTHON ?= $(shell if [ -f .venv/bin/python ]; then echo .venv/bin/python; else echo python3; fi)
RUFF   ?= $(shell if [ -f .venv/bin/ruff ]; then echo .venv/bin/ruff; else echo ruff; fi)
MYPY   ?= $(shell if [ -f .venv/bin/mypy ]; then echo .venv/bin/mypy; else echo mypy; fi)
PYTEST ?= $(shell if [ -f .venv/bin/pytest ]; then echo .venv/bin/pytest; else echo pytest; fi)

.PHONY: gate ruff mypy pytest check-agents check-modules

gate: ruff mypy pytest check-agents check-modules

ruff:
	$(RUFF) check .

mypy:
	$(MYPY) --strict .

pytest:
	$(PYTEST) -q

check-agents:
	$(PYTHON) tools/check_agents.py

check-modules:
	$(PYTHON) tools/check_modules.py
