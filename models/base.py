"""Shared model plumbing."""

import datetime
import uuid

from extensions import db


def utcnow() -> datetime.datetime:
    """Timezone-aware 'now', used as the ORM-side default for timestamp columns.

    Must stay a plain datetime: returning a SQL function here breaks the
    driver, because this value is bound as a parameter.
    """
    return datetime.datetime.now(datetime.timezone.utc)


def new_uuid() -> uuid.UUID:
    """Generate a primary key.

    Ids are generated in Python rather than by the server (``gen_random_uuid()``)
    for two reasons: the same models then work unchanged on SQLite for the test
    suite, and the value is known before the INSERT, which makes it easy to
    build order numbers and pickup codes in the same transaction.

    Returns a ``uuid.UUID``, not a hex string, because every id column is
    declared ``Uuid(as_uuid=True)``. That flag means SQLAlchemy hands the value
    to the driver as a native UUID (and renders it as CHAR(32) on SQLite
    automatically), so returning a string here would fail at bind time with
    ``'str' object has no attribute 'hex'``. The dialect portability comes from
    the column type, not from flattening the value to text.
    """
    return uuid.uuid4()


def new_qr_code() -> str:
    """Personal QR payload for a customer (re reto 3.7).

    16 hex characters of cryptographic randomness: the code IS the credential
    the business scans, so it must not be guessable or sequential.
    """
    return uuid.uuid4().hex[:16]


def new_pickup_code() -> str:
    """6-digit numeric code the customer shows at pickup (re reto 5.9).

    Short enough to read out loud, short enough to brute-force over the whole
    numeric range if it were the only guard, so pickup validation always
    requires being logged in as the business that owns the order.
    """
    return f"{uuid.uuid4().int % 1_000_000:06d}"


class TimestampMixin:
    """created_at / updated_at columns.

    The Python-side `default` is what the ORM sends in the INSERT, so it has to
    return a real datetime. The `server_default` is the SQL-level backstop for
    rows written outside the ORM (psql, a data migration, a manual fix). Both
    exist on purpose: belt and braces for a financial table.
    """

    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        server_default=db.func.now(),
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
        server_default=db.func.now(),
    )
