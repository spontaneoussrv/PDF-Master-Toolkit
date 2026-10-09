"""Allows `python -m app` during development."""

from app.main import main

if __name__ == "__main__":
    raise SystemExit(main())
