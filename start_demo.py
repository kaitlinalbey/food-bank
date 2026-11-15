"""Start one central API and three separate local food-bank node processes."""
import subprocess
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def prepare_fresh_demo():
    """Only auto-fill a database that does not exist yet."""
    from database import DEFAULT_DB
    from seed_more_demo_data import enrich_demo
    path=Path(os.environ.get("FOOD_BANK_DB_PATH") or DEFAULT_DB)
    if not path.exists():
        counts=enrich_demo(path)
        print(f"Created fresh fictional data: {counts['items']} items across {counts['banks']} banks.",flush=True)



def main():
    # This launcher is the classroom demo, so it switches demo mode on for the four
    # processes it starts (public demo passwords, outage simulation for admins).
    # Set FOOD_BANK_DEMO_MODE=0 before starting to opt out.
    os.environ.setdefault("FOOD_BANK_DEMO_MODE", "1")
    prepare_fresh_demo()
    commands = [
        [sys.executable, "server.py", "--port", "5000"],
        [sys.executable, "node.py", "--bank", "1", "--port", "5001"],
        [sys.executable, "node.py", "--bank", "2", "--port", "5002"],
        [sys.executable, "node.py", "--bank", "3", "--port", "5003"],
    ]
    processes = []
    print("Food Bank Database — local classroom demo", flush=True)
    print("Central API:  http://127.0.0.1:5000/api/health", flush=True)
    print("Central bank: http://127.0.0.1:5001", flush=True)
    print("Northside:    http://127.0.0.1:5002", flush=True)
    print("Westside:    http://127.0.0.1:5003", flush=True)
    from database import demo_mode
    if demo_mode():
        print("CLASSROOM DEMO MODE: staff/demo123 and admin/admin123 are enabled. Local use only.", flush=True)
    else:
        print("Demo mode is OFF: demo passwords are refused. Use manage_accounts.py to set up accounts.", flush=True)
    print("Press Ctrl+C to stop all 4 services.\n", flush=True)
    try:
        for command in commands:
            processes.append(subprocess.Popen(command, cwd=ROOT))
        while True:
            stopped = [p.returncode for p in processes if p.poll() is not None]
            if stopped:
                print("One of the services stopped; shutting down demo.", flush=True)
                break
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopping demo...", flush=True)
    finally:
        for proc in processes:
            if proc.poll() is None:
                proc.terminate()
        for proc in processes:
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
        print("All services stopped.", flush=True)


if __name__ == "__main__":
    main()
