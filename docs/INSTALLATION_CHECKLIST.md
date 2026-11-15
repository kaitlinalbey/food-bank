# Installation Checklist

Use this list to set up the project on a new Windows computer and confirm that it works. Full instructions are in `README.md`.

## Before you start

- [ ] Windows 10 or 11
- [ ] Python 3.10 or newer, installed with **Add Python to PATH** checked. Check with `py -3 --version` or `python --version`.
- [ ] Internet connection for the first run, which installs Flask
- [ ] Ports 5000, 5001, 5002 and 5003 are free. Close any other copy of the project, and stop Docker if it is running this project.

## Install and start

- [ ] Clone the repository (or extract a ZIP of it) into a normal folder, for example `Downloads\food-bank-management-system`.
- [ ] Double-click `run_presentation_windows.bat`. The first run creates `.venv` and installs Flask, which takes about a minute.
- [ ] The terminal shows the four addresses, and also "Fresh demo data: ..." and "Normal data/food_bank.db and normal node queues are untouched."
- [ ] Leave the terminal open.

## Check that it works

- [ ] http://127.0.0.1:5000/api/health shows `{"service": "food-bank-central-api", "status": "ok"}`.
- [ ] http://127.0.0.1:5001 shows the Central Food Pantry login page. Log in as `staff` / `demo123`.
- [ ] The dashboard loads, and "My inventory" shows 48 items.
- [ ] http://127.0.0.1:5002 (Northside) and http://127.0.0.1:5003 (Westside) also log in with `staff` / `demo123`, and you stay logged in on Central.
- [ ] On Central, log out and log in as `admin` / `admin123`. "Database administration" appears in the top links and opens.
- [ ] http://127.0.0.1:5000/docs shows the API reference page.
- [ ] Press Ctrl+C in the terminal. All four services stop.

## Optional checks

- [ ] `run_tests_windows.bat` finishes with `OK` and no skipped tests.
- [ ] `run_release_check_windows.bat` reports `TESTS_PASSED_DOCKER_RUNTIME_UNVERIFIED`. `PARTIALLY_VERIFIED` means some tests were skipped or Docker Compose was not found. `FAIL` means something failed. The Docker runtime is always checked separately with `verify_docker_windows.bat`.
- [ ] `.venv\Scripts\python.exe verify_distributed.py` ends with `18/18 scenarios passed`.
- [ ] Log in on Central as `staff`. The **Offline sync** page shows the connection status but no outage button. As `admin`, the **Simulate connection outage** button is shown.
- [ ] With Docker Desktop running: `verify_docker_windows.bat`.

## If something fails

See section 11 (Troubleshooting) of `README.md`.
