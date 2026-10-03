"""User accounts.

The `users` table is the root of the whole system: every other table hangs off
it. Three different populations share this one table, distinguished by `role`:

- USER   : a Paseo Points customer. Holds points, scans nothing, earns by
            buying and redeems coupons.
- SELLER : a business owner. Always linked 1:1 to a `business` row, which
            holds the catalog and the orders.
- ADMIN  : Paseo Aranjuez staff. Manages businesses, rewards and roles.

Authentication is Google-only, so `google_sub` is the real identity key and
`password_hash` is nullable and unused (kept because the original hackathon
schema declared it and a future local-login flow would need it).

The points balance is NOT a column. It is a correlated SUM over the
`transactions` ledger, exposed as a column_property so it can be SELECTed and
ORDERed by in one round trip (that is what makes the customer ranking in the
admin panel a plain query instead of a correlated subquery per row).
"""

import uuid
from typing import Optional

from sqlalchemy import CheckConstraint, UniqueConstraint, func, select
from sqlalchemy.orm import column_property, declared_attr, validates
from sqlalchemy.types import Uuid

from enums import CustomerLevel
from extensions import db
from models.base import TimestampMixin, new_qr_code, new_uuid
from roles import UserRole


class User(TimestampMixin, db.Model):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "role IN ('USER', 'SELLER', 'ADMIN')",
            name="ck_users_role",
        ),
        CheckConstraint(
            "level IN ('BRONZE', 'SILVER', 'GOLD', 'PLATINUM')",
            name="ck_users_level",
        ),
    )

    id = db.Column(Uuid(as_uuid=True), primary_key=True, default=new_uuid)

    email = db.Column(db.String(320), nullable=False, unique=True, index=True)
    google_sub = db.Column(db.String(128), nullable=True, unique=True, index=True)
    # Unused today: sign-in is Google-only. Nullable so Google signups work.
    password_hash = db.Column(db.String(255), nullable=True)

    name = db.Column(db.String(160), nullable=False)
    phone = db.Column(db.String(32), nullable=True)

    # Google profile photo and device location
    picture = db.Column(db.String(512), nullable=True)
    latitude = db.Column(db.Float, nullable=True)
    longitude = db.Column(db.Float, nullable=True)

    role = db.Column(
        db.String(16),
        nullable=False,
        default=UserRole.USER.value,
        server_default=UserRole.USER.value,
    )
    level = db.Column(
        db.String(16),
        nullable=False,
        default=CustomerLevel.BRONZE.value,
        server_default=CustomerLevel.BRONZE.value,
    )

    # Credential the business scans to credit points (reto 3.7). 16 hex chars
    # of randomness, so it cannot be guessed or walked sequentially.
    qr_code = db.Column(db.String(32), nullable=False, unique=True, default=new_qr_code)

    # --- Relationships (defined lazily to avoid import cycles) ---------------

    business = db.relationship(
        "Business",
        back_populates="owner",
        uselist=False,
        cascade="all, delete-orphan",
        foreign_keys="Business.user_id",
    )
    coupons = db.relationship("Coupon", back_populates="customer", cascade="all, delete-orphan")
    transactions = db.relationship(
        "Transaction", back_populates="customer", cascade="all, delete-orphan"
    )
    orders = db.relationship("Order", back_populates="customer", cascade="all, delete-orphan")

    # --- Derived -------------------------------------------------------------

    # SUM of the customer's ledger, exposed as a real column so it can be
    # SELECTed, filtered and ORDERed by in one round trip -- that is what makes
    # the admin customer ranking a plain query instead of a correlated subquery
    # per row.
    #
    # declared_attr (not column_property) because the expression needs the
    # class itself: column_property takes a finished expression, and here the
    # expression references Transaction, which cannot be imported until this
    # module has finished loading. declared_attr defers the body until mapper
    # configuration, by which point both exist.
    @declared_attr
    def points_balance(cls):
        from models.transactions import Transaction  # local: circular at import time

        return column_property(
            select(func.coalesce(func.sum(Transaction.points), 0))
            .where(Transaction.customer_id == cls.id)
            .correlate_except(Transaction)
            .scalar_subquery(),
            deferred=True,
        )

    # --- Behaviour -----------------------------------------------------------

    @validates("email")
    def _normalize_email(self, key, value: str) -> str:
        """Always store the email lowercased.

        The database has a UNIQUE constraint on email, so 'Ana@x.com' and
        'ana@x.com' would otherwise be two different accounts that both belong
        to the same person. Google treats them as the same address.
        """
        return value.strip().lower() if value else value

    @property
    def role_enum(self) -> UserRole:
        return UserRole.parse(self.role) or UserRole.USER

    @property
    def level_enum(self) -> CustomerLevel:
        return CustomerLevel.parse(self.level) or CustomerLevel.BRONZE

    @property
    def is_admin(self) -> bool:
        return self.role_enum is UserRole.ADMIN

    @property
    def is_seller(self) -> bool:
        return self.role_enum is UserRole.SELLER

    @property
    def display_role(self) -> str:
        return self.role_enum.value

    def to_dict(self, include_private: bool = False) -> dict:
        """Public representation for API responses."""
        data = {
            "id": str(self.id),
            "email": self.email,
            "name": self.name,
            "role": self.role_enum.value,
            "level": self.level_enum.value,
            "picture": self.picture,
            "latitude": self.latitude,
            "longitude": self.longitude,
        }
        if include_private:
            data["phone"] = self.phone
            data["qr_code"] = self.qr_code
            data["points_balance"] = self.points_balance
        return data

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<User {self.email} role={self.role} level={self.level}>"


# --- Query helpers ----------------------------------------------------------
#
# IMPORTANT: this module is the single place where roles are WRITTEN. No other
# module, route or serializer may set User.role. See set_user_role().


def find_user_by_id(user_id) -> User | None:
    """Look a user up by primary key. Accepts a UUID or its string form."""
    if isinstance(user_id, str):
        try:
            user_id = uuid.UUID(user_id)
        except ValueError:
            return None
    return db.session.get(User, user_id)


def find_user_by_google_sub(google_sub: str) -> User | None:
    if not google_sub:
        return None
    return db.session.execute(
        db.select(User).where(User.google_sub == google_sub)
    ).scalar_one_or_none()


def find_user_by_qr_code(qr_code: str) -> User | None:
    """Resolve the customer whose personal QR a business just scanned."""
    if not qr_code:
        return None
    return db.session.execute(
        db.select(User).where(User.qr_code == qr_code.strip())
    ).scalar_one_or_none()


def find_user_by_email(email: str) -> User | None:
    if not email:
        return None
    return db.session.execute(
        db.select(User).where(User.email == email.strip().lower())
    ).scalar_one_or_none()


class GoogleAccountConflict(ValueError):
    """The email already belongs to an account tied to a different Google id.

    Raised instead of letting the INSERT hit the UNIQUE(email) constraint, so
    the caller can answer 409 with a readable message rather than 500.
    """


def get_or_create_google_user(
    google_sub: str,
    email: str,
    name: str,
    picture: Optional[str] = None,
) -> User:
    """Return the user for this Google account, creating it on first login.

    - A brand-new user ALWAYS gets UserRole.USER. Google and the frontend have
      no say in this: the role is hardcoded below.
    - An existing user is returned with its current role untouched, so a
      manually assigned SELLER/ADMIN role survives every later login and is
      never reset or upgraded.
    """
    existing = find_user_by_google_sub(google_sub)
    if existing is not None:
        if name and existing.name != name:
            existing.name = name
        if picture and existing.picture != picture:
            existing.picture = picture
        db.session.commit()
        return existing

    by_email = find_user_by_email(email)
    if by_email is not None:
        if by_email.google_sub:
            raise GoogleAccountConflict(
                "This email is already registered with a different Google account"
            )
        # Link the provider identity to the pre-existing row.
        by_email.google_sub = google_sub
        if name and by_email.name != name:
            by_email.name = name
        if picture and by_email.picture != picture:
            by_email.picture = picture
        db.session.commit()
        return by_email

    user = User(
        id=new_uuid(),
        email=email,
        name=name,
        picture=picture,
        google_sub=google_sub,
        role=UserRole.USER.value,  # default role: the ONLY role ever auto-assigned
        level=CustomerLevel.BRONZE.value,
        qr_code=new_qr_code(),
    )
    db.session.add(user)
    db.session.commit()
    return user


def update_user_location(user_id, latitude: float, longitude: float) -> User:
    """Set the user's last known location.

    Raises ValueError if coordinates are out of range.
    """
    if not (-90 <= latitude <= 90):
        raise ValueError(f"Invalid latitude: {latitude!r} (must be between -90 and 90)")
    if not (-180 <= longitude <= 180):
        raise ValueError(f"Invalid longitude: {longitude!r} (must be between -180 and 180)")
    user = find_user_by_id(user_id)
    if user is None:
        raise LookupError(f"User {user_id} not found")
    user.latitude = latitude
    user.longitude = longitude
    db.session.commit()
    return user


def set_user_role(user_id, role: UserRole) -> User:
    """Change a user's role.

    This is the ONLY way a user becomes SELLER or ADMIN. It must only be called
    from an authorized application flow (the admin panel, or the business
    registration flow that approves a merchant) -- never from a public
    endpoint and never with data coming from Google or from the frontend.
    """
    if not isinstance(role, UserRole):
        raise ValueError(f"Invalid role: {role!r}")
    user = find_user_by_id(user_id)
    if user is None:
        raise LookupError(f"User {user_id} not found")
    user.role = role.value
    db.session.commit()
    return user


def reset_store() -> None:
    """Delete ALL users. Used by tests."""
    for user in db.session.execute(db.select(User)).scalars():
        db.session.delete(user)
    db.session.commit()
