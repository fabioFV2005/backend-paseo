"""User roles for the application.

These are the ONLY roles that exist. The role of a user is decided by the
backend (never by Google, never by the frontend):

- USER:   default role, assigned automatically to every new account.
- SELLER: assigned only through an authorized application flow.
- ADMIN:  assigned only manually through a protected administrative process.
"""

from enums import StrEnum


class UserRole(StrEnum):
    """All possible user roles. Inherits from str so it is easy to put in a JWT/JSON.

    The values are the ones stored in the database CHECK constraint and returned
    by the API. Do not rename them without a migration.
    """

    USER = "USER"
    SELLER = "SELLER"
    ADMIN = "ADMIN"
