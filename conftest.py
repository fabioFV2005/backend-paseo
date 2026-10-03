"""Pytest configuration.

Its main job is putting the project root on sys.path so `import app` works when
pytest is invoked as a bare `pytest` and not as `python -m pytest`. Without this
the suite fails with `ModuleNotFoundError: No module named 'app'`.

The database is SQLite in-memory, not PostgreSQL:

  - no server needed, so the suite is self-contained and reproducible;
  - roughly ten times faster, which matters when the suite grows;
  - the models are written to be dialect-portable (UUID ids and code
    generation happen in Python, not in SQL), so nothing that the tests assert
    is PostgreSQL-only behaviour.

What the tests therefore do NOT cover is PostgreSQL-specific behaviour: NUMERIC
precision, row-level locking, and `SELECT ... FOR UPDATE`. The trade is
accepted deliberately -- those need a real PostgreSQL integration suite, not a
unit suite pretending to check them.
"""

import os

import pytest

# Must happen before importing anything that reads the environment.
os.environ.setdefault("TESTING", "1")
# Point the app at SQLite before config.py is imported anywhere.
os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
# A fixed secret keeps tokens from an earlier test run from being surprising.
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-not-used-in-production-32")
os.environ.setdefault("FLASK_ENV", "test")
os.environ.setdefault("GOOGLE_CLIENT_ID", "test-google-client-id")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test-google-client-secret")

from app import create_app  # noqa: E402
from extensions import db as _db  # noqa: E402
from models import User, get_or_create_google_user  # noqa: E402


@pytest.fixture()
def app():
    """A fresh application with an empty schema, per test."""
    application = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite+pysqlite:///:memory:",
        }
    )
    with application.app_context():
        _db.create_all()
        yield application
        _db.session.remove()
        _db.drop_all()


@pytest.fixture()
def db(app):
    """The database handle, bound to the per-test app context."""
    return _db


@pytest.fixture()
def client(app):
    """Flask test client."""
    return app.test_client()


@pytest.fixture()
def make_user(app):
    """Factory for a signed-up customer, bypassing Google entirely.

    Tests call this instead of mocking the OAuth round trip, because the
    identity that matters downstream is the row in `users`, not the token.
    """

    def _make(email="cliente@paseo.test", name="Cliente Test", google_sub=None, role=None):
        user = get_or_create_google_user(
            google_sub=google_sub or f"sub-{email}",
            email=email,
            name=name,
        )
        if role is not None:
            user.role = role.value if hasattr(role, "value") else role
            _db.session.commit()
        return user

    return _make


@pytest.fixture()
def auth_header(app):
    """Builds an Authorization header for a given user."""

    def _header(user):
        from auth import create_token

        return {"Authorization": f"Bearer {create_token(user)}"}

    return _header
