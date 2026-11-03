"""Smart Factory Maintenance Scheduler - Modern Vercel-inspired Architecture.

Run locally:
    python main.py

Or with uvicorn:
    uvicorn main:app --reload
"""

from __future__ import annotations

import argparse
import atexit
import sys
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn

# Re-export core primitives for backward compatibility and test suites
from app.store import Store, parse_date, status_for
from app.api import create_app

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "factory.db"

# Initialize FastAPI application instance
app = create_app(DB_PATH)
atexit.register(lambda: app.state.store.close() if hasattr(app.state, "store") else None)


def open_browser_delayed(url: str, delay_sec: float = 1.0) -> None:
    """Open default browser after server initializes."""
    def _open():
        time.sleep(delay_sec)
        try:
            webbrowser.open(url)
        except Exception:
            pass

    threading.Thread(target=_open, daemon=True).start()


def main():
    parser = argparse.ArgumentParser(
        description="Smart Factory Maintenance Scheduler - Vercel-inspired local edition"
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host interface to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Port to listen on (default: 8000)")
    parser.add_argument("--no-browser", action="store_true", help="Do not open browser automatically")
    parser.add_argument("--seed", action="store_true", help="Pre-seed demo machinery on startup")

    args = parser.parse_args()

    # Pre-seed demo if requested and database is empty
    if args.seed:
        store = Store(DB_PATH)
        if not store.machines():
            store.seed_demo()
            print("Demo machinery and schedules seeded successfully.")
        store.close()

    url = f"http://{args.host}:{args.port}"
    print("\n" + "=" * 65)
    print("   SMART FACTORY MAINTENANCE PLATFORM (Vercel Edition)")
    print("=" * 65)
    print(f" * Local Dashboard:  {url}")
    print(f" * REST API Docs:    {url}/docs")
    print(f" * Database Storage: {DB_PATH}")
    print("=" * 65 + "\n")

    if not args.no_browser:
        open_browser_delayed(url)

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
