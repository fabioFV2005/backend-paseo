"""Pytest configuration.

The test suite runs against SQLite in-memory (configured in root conftest.py).
When DATABASE_URL points to PostgreSQL, it falls back to a throwaway database
(hackaton_test) so testing never touches production data.
"""

import os

_base_url = os.environ.get("DATABASE_URL", "")
if _base_url and _base_url.startswith("postgres"):
    os.environ["DATABASE_URL"] = _base_url.rsplit("/", 1)[0] + "/hackaton_test"
