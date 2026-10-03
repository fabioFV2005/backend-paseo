"""Application settings.

All configuration is read from the environment (loaded from .env) exactly
once, at import time. Nothing else in the codebase calls os.environ directly.

Two deliberate choices:

1. There is NO hardcoded fallback for the signing secrets. In development a
   random secret is generated per process, so a forgotten JWT_SECRET_KEY can
   never sign tokens with a publicly known value. In production a missing
   secret is a hard startup error instead of a silent vulnerability.

2. DATABASE_URL has no fallback either. A wrong database is a deployment
   mistake you want to hear about immediately, not a fresh empty SQLite file.
"""

import os
import secrets

from dotenv import load_dotenv

# Must run before reading any environment variable.
load_dotenv()


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


# --- Core Flask -------------------------------------------------------------

# SECRET_KEY is the conventional name; FLASK_SECRET_KEY is accepted as a
# fallback so either spelling in .env works.
SECRET_KEY = os.environ.get("SECRET_KEY") or os.environ.get("FLASK_SECRET_KEY")
DEBUG = _env_flag("FLASK_DEBUG", default=False)
TESTING = _env_flag("TESTING", default=False)
# Set FLASK_ENV=production on the deployed server. Only then do we refuse to
# start without real secrets and start requiring HTTPS-only cookies.
IS_PRODUCTION = os.environ.get("FLASK_ENV", "").strip().lower() == "production"

# --- Database ---------------------------------------------------------------

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is not set. Copy .env.example to .env and fill it in, for example:\n"
        "  DATABASE_URL=postgresql+psycopg2://usuario:clave@localhost:5432/paseo_aranjuez\n"
        "SQLite also works for a quick look: DATABASE_URL=sqlite/paseo_aranjuez.db"
    )
SQLALCHEMY_ECHO = _env_flag("SQLALCHEMY_ECHO", default=False)

# --- Signing secrets --------------------------------------------------------

JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "").strip()
if not JWT_SECRET_KEY:
    if IS_PRODUCTION:
        raise RuntimeError(
            "JWT_SECRET_KEY must be set when FLASK_ENV=production. "
            "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
        )
    # Development: random per process. Users log in again after a restart,
    # which is the correct trade-off versus a hardcoded default secret that
    # anyone reading the repository could use to forge an admin token.
    JWT_SECRET_KEY = secrets.token_hex(32)
elif IS_PRODUCTION and len(JWT_SECRET_KEY) < 32:
    raise RuntimeError("JWT_SECRET_KEY must be at least 32 characters in production")

if not SECRET_KEY:
    # The Flask session cookie only carries the OAuth "state" CSRF token, but
    # it must not be signed with the JWT key: one compromised signing key
    # should never unlock both.
    SECRET_KEY = secrets.token_hex(32)

# --- Google OAuth -----------------------------------------------------------

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
GOOGLE_REDIRECT_URI = os.environ.get(
    "GOOGLE_REDIRECT_URI", "http://localhost:5000/auth/google/callback"
).strip()

# --- Tokens and cookies -----------------------------------------------------

JWT_ALGORITHM = "HS256"
TOKEN_LIFETIME_HOURS = int(os.environ.get("TOKEN_LIFETIME_HOURS", "24"))
SESSION_COOKIE_NAME = "paseo_session"
# Force HTTPS-only cookies in production even if the env var is not set.
SESSION_COOKIE_SECURE = _env_flag("SESSION_COOKIE_SECURE", default=IS_PRODUCTION)

# --- Rate limiting ----------------------------------------------------------

# How long a business has to wait before crediting the SAME customer again at
# the same shop. Without this, a shop and a customer in collusion can scan the
# pair in a loop and mint an unlimited balance out of thin air.
#
# The cost is a genuine edge: two separate purchases by the same customer at the
# same shop inside this window are refused. The blast radius is "the shop waits
# 60 seconds", not lost money, which is why a blunt window is the right trade
# here. Set to 0 to disable.
SCAN_DUPLICATE_WINDOW_SECONDS = int(os.environ.get("SCAN_DUPLICATE_WINDOW_SECONDS", "60"))

# In-process counters live in the worker's memory, so with several workers each
# one keeps its own tally and the effective limit multiplies. Point this at
# Redis before running more than one process.
RATELIMIT_STORAGE_URI = os.environ.get("RATELIMIT_STORAGE_URI", "memory://").strip()

# --- CORS -------------------------------------------------------------------

# Comma-separated list of allowed origins. Empty means "same-origin only",
# which is correct while the frontend is served from the same host.
CORS_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("CORS_ORIGINS", "").split(",")
    if origin.strip()
]


def google_configured() -> bool:
    """True when the Google OAuth credentials needed for sign-in are present."""
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)
