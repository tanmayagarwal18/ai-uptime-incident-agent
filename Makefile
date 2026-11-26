PYTHON ?= python

.PHONY: install demo-server monitor dashboard api seed simulate test lint format clean

install:
	$(PYTHON) -m pip install -r requirements.txt

demo-server:
	$(PYTHON) -m uvicorn demo.flaky_server:app --host 127.0.0.1 --port 8001

monitor:
	$(PYTHON) scripts/run_monitor.py

dashboard:
	$(PYTHON) -m streamlit run dashboard/streamlit_app.py

api:
	$(PYTHON) -m uvicorn app.api.main:app --host 127.0.0.1 --port 8000 --reload

seed:
	$(PYTHON) scripts/seed_demo_data.py

simulate:
	$(PYTHON) scripts/simulate_incident.py --mode error_500

test:
	$(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff check .

format:
	$(PYTHON) -m ruff format .

clean:
	rm -f data/*.db data/*.db-wal data/*.db-shm
