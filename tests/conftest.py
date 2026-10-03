"""Pytest configuration.

The suite runs against a throwaway database (hackaton_test) so that
reset_store() — which deletes every row in the users table — can never
wipe the real development/production data.

This MUST be set before app/models are imported, because models.users
reads DATABASE_URL at import time. pytest imports conftest.py first, and
load_dotenv() does not override variables that are already set.
"""

import os

from dotenv import load_dotenv

load_dotenv()

_base_url = os.environ.get("DATABASE_URL", "")
if _base_url:
    # Same server/credentials as DATABASE_URL, but the hackaton_test database.
    os.environ["DATABASE_URL"] = _base_url.rsplit("/", 1)[0] + "/hackaton_test"
