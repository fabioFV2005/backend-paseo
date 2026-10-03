"""Shared Flask extension instances.

Kept in their own module so models, services and the app factory can all import
them without creating a circular dependency.
"""

from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
cors = CORS()

# Rate limiting is keyed by IP because the endpoints that need it are exactly
# the ones an unauthenticated attacker hits: /auth/google and /api/business/scan.
# The storage backend is in-process memory, which is per-worker -- fine for a
# single development server, wrong the moment there are several workers or more
# than one machine. See README for the Redis swap.
limiter = Limiter(
    key_func=get_remote_address,
    storage_uri="memory://",
)