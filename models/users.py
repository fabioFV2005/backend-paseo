"""User storage backed by PostgreSQL.

The rest of the code (auth, roles, routes) only talks to the functions in
this module — nothing else may write a user's role.

- Every new user is registered automatically with UserRole.USER (the
  "client" role). Google and the frontend have no say in this.
- SELLER/ADMIN are assigned only manually, directly in the database:

      UPDATE users SET role = 'ADMIN' WHERE email = 'you@example.com';

  The role is read from the database on every request, so a manual change
  applies immediately — even to tokens issued before it.
"""

import os
from typing import Optional

from dotenv import load_dotenv
from sqlalchemy import Column, Enum, Float, Integer, String, create_engine, delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import declarative_base, sessionmaker

from roles import UserRole

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is not configured. Add it to your .env file, e.g.:\n"
        "  DATABASE_URL=postgresql+psycopg://postgres:root@localhost:5432/hackaton"
    )

# pool_pre_ping drops stale connections instead of failing mid-request.
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
# expire_on_commit=False keeps attributes readable after the session closes,
# so callers can use the returned User objects freely.
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(255), nullable=False)
    name = Column(String(255), nullable=False, default="")
    # Stored as VARCHAR with a CHECK constraint (values: USER, SELLER, ADMIN),
    # so manual SQL updates with invalid values are rejected by Postgres.
    role = Column(
        Enum(UserRole, native_enum=False, create_constraint=True),
        nullable=False,
        default=UserRole.USER,
        server_default=UserRole.USER.name,
    )
    google_sub = Column(String(255), unique=True, nullable=True, index=True)
    # URL of the Google profile photo (None if the account has none).
    picture = Column(String(512), nullable=True)
    # Last known location, set from the device (never comes from Google).
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)


# Create the table on startup if it doesn't exist yet.
Base.metadata.create_all(engine)


def find_user_by_id(user_id: int) -> Optional[User]:
    with SessionLocal() as session:
        user = session.get(User, user_id)
        if user is not None:
            session.expunge(user)
        return user


def find_user_by_google_sub(google_sub: str) -> Optional[User]:
    with SessionLocal() as session:
        user = session.execute(
            select(User).where(User.google_sub == google_sub)
        ).scalar_one_or_none()
        if user is not None:
            session.expunge(user)
        return user


def get_or_create_google_user(
    google_sub: str, email: str, name: str, picture: Optional[str] = None
) -> User:
    """Return the user for this Google account, creating it on first login.

    - A brand-new user ALWAYS gets UserRole.USER. Google and the frontend
      have no say in this: the role is hardcoded here.
    - An existing user keeps their role (USER, SELLER or ADMIN) untouched —
      it is never reset. Only the profile fields (email/name/picture) are
      refreshed, because Google is the source of truth for those.
    """
    with SessionLocal() as session:
        user = session.execute(
            select(User).where(User.google_sub == google_sub)
        ).scalar_one_or_none()

        if user is not None:
            user.email = email
            user.name = name
            user.picture = picture
            session.commit()
            session.expunge(user)
            return user

        user = User(
            email=email,
            name=name,
            picture=picture,
            role=UserRole.USER,  # default role — the ONLY role ever assigned automatically
            google_sub=google_sub,
        )
        session.add(user)
        try:
            session.commit()
        except IntegrityError:
            # A concurrent request created this Google account first — just read it.
            session.rollback()
            user = session.execute(
                select(User).where(User.google_sub == google_sub)
            ).scalar_one()
        session.expunge(user)
        return user


def update_user_location(user_id: int, latitude: float, longitude: float) -> User:
    """Set the user's last known location (sent by the device, not Google).

    Raises ValueError if the coordinates are out of range.
    """
    if not (-90 <= latitude <= 90):
        raise ValueError(f"Invalid latitude: {latitude!r} (must be between -90 and 90)")
    if not (-180 <= longitude <= 180):
        raise ValueError(f"Invalid longitude: {longitude!r} (must be between -180 and 180)")
    with SessionLocal() as session:
        user = session.get(User, user_id)
        if user is None:
            raise LookupError(f"User {user_id} not found")
        user.latitude = latitude
        user.longitude = longitude
        session.commit()
        session.expunge(user)
        return user


def set_user_role(user_id: int, role: UserRole) -> User:
    """Change a user's role.

    This is the ONLY way a user can become SELLER or ADMIN. It must only be
    called from an authorized application flow (e.g. a protected admin
    console or CLI) — never from a public endpoint and never with data
    coming from Google or from the frontend registration.
    """
    if not isinstance(role, UserRole):
        raise ValueError(f"Invalid role: {role!r}")
    with SessionLocal() as session:
        user = session.get(User, user_id)
        if user is None:
            raise LookupError(f"User {user_id} not found")
        user.role = role
        session.commit()
        session.expunge(user)
        return user


def reset_store() -> None:
    """Delete ALL users. Only used by tests, which run against a throwaway
    database (see tests/conftest.py) — never against real data."""
    with SessionLocal() as session:
        session.execute(delete(User))
        session.commit()
