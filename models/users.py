"""User storage.

For simplicity this uses an in-memory store. In a real deployment, replace
these functions with database queries — the rest of the code (auth, roles,
routes) stays exactly the same.

IMPORTANT: this module is the single place where roles are written. Nobody
else may set a user's role.
"""

from dataclasses import dataclass
from itertools import count
from typing import Dict, Optional

from roles import UserRole


@dataclass
class User:
    id: int
    email: str
    name: str
    role: UserRole
    google_sub: Optional[str] = None  # Google's unique subject id for this user


# --- In-memory "database" ---------------------------------------------------

_id_counter = count(start=1)
_users_by_id: Dict[int, User] = {}
_user_ids_by_google_sub: Dict[str, int] = {}


def find_user_by_id(user_id: int) -> Optional[User]:
    return _users_by_id.get(user_id)


def find_user_by_google_sub(google_sub: str) -> Optional[User]:
    user_id = _user_ids_by_google_sub.get(google_sub)
    return _users_by_id.get(user_id) if user_id is not None else None


def get_or_create_google_user(google_sub: str, email: str, name: str) -> User:
    """Return the user for this Google account, creating it on first login.

    - A brand-new user ALWAYS gets UserRole.USER. Google and the frontend
      have no say in this: the role is hardcoded here.
    - An existing user is returned unchanged, so their current role
      (USER, SELLER or ADMIN) is always kept and never reset.
    """
    existing = find_user_by_google_sub(google_sub)
    if existing is not None:
        return existing

    user = User(
        id=next(_id_counter),
        email=email,
        name=name,
        role=UserRole.USER,  # default role — the ONLY role ever assigned automatically
        google_sub=google_sub,
    )
    _users_by_id[user.id] = user
    _user_ids_by_google_sub[google_sub] = user.id
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
    user = find_user_by_id(user_id)
    if user is None:
        raise LookupError(f"User {user_id} not found")
    user.role = role
    return user


def reset_store() -> None:
    """Clear the store. Only used by tests."""
    global _id_counter
    _users_by_id.clear()
    _user_ids_by_google_sub.clear()
    _id_counter = count(start=1)
