"""Rewards (benefits) and Coupons (issued redemptions).

`rewards` is the catalog: what a customer can buy with points. `coupons` is the
result of buying one: a concrete code with an expiry that the customer shows at
the counter and the business validates.

Two decisions worth reading before changing anything:

1. **Reward.business_id is NULLABLE.** NULL means the coupon is sponsored by
   Paseo Aranjuez itself rather than by a shop. That is how the admin publishes
   a blanket promotion (reto 3.6, "administrador... crear recompensas").

2. **Coupon stores a snapshot of the reward's terms.** A business is free to
   edit or even retire a reward it published last month. Coupons already issued
   must keep the discount they were sold with, otherwise raising a price would
   silently rewrite the history of every outstanding coupon. So the terms are
   copied onto the coupon at issue time and read from there forever after.

`coupons.reward_id` intentionally has NO cascade: deleting a reward that has
already been redeemed must fail rather than orphan its coupons.
"""

import datetime
import uuid

from sqlalchemy import CheckConstraint, Index, UniqueConstraint
from sqlalchemy.types import Uuid

from enums import CouponStatus, DiscountType
from extensions import db
from models.base import TimestampMixin, new_uuid


def new_coupon_code() -> str:
    """8 uppercase hex characters, e.g. "A1B2C3D4"."""
    return uuid.uuid4().hex[:8].upper()


class Reward(TimestampMixin, db.Model):
    __tablename__ = "rewards"
    __table_args__ = (
        CheckConstraint(
            "discount_type IN ('percent', 'fixed')", name="ck_rewards_discount_type"
        ),
        CheckConstraint("discount_value > 0", name="ck_rewards_discount_positive"),
        CheckConstraint(
            "max_discount_bs IS NULL OR max_discount_bs > 0",
            name="ck_rewards_max_discount_positive",
        ),
        CheckConstraint(
            "min_purchase_bs >= 0", name="ck_rewards_min_purchase_non_negative"
        ),
        CheckConstraint("points_cost > 0", name="ck_rewards_points_cost_positive"),
        CheckConstraint("valid_days > 0", name="ck_rewards_valid_days_positive"),
        CheckConstraint("stock IS NULL OR stock >= 0", name="ck_rewards_stock_non_negative"),
        # A percentage discount above 100% is nonsense, and a percentage with
        # no ceiling is an unbounded liability for the business that funds it.
        CheckConstraint(
            "discount_type <> 'percent' OR discount_value <= 100",
            name="ck_rewards_percent_not_above_100",
        ),
        CheckConstraint(
            "discount_type <> 'percent' OR max_discount_bs IS NOT NULL",
            name="ck_rewards_percent_needs_cap",
        ),
        Index("ix_rewards_business_active", "business_id", "active"),
    )

    id = db.Column(Uuid(as_uuid=True), primary_key=True, default=new_uuid)

    # NULL = sponsored by Paseo Aranjuez. CASCADE so a shop's rewards go with it.
    business_id = db.Column(
        Uuid(as_uuid=True),
        db.ForeignKey("business.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    title = db.Column(db.String(160), nullable=False)
    description = db.Column(db.Text, nullable=True)

    discount_type = db.Column(db.String(16), nullable=False, default=DiscountType.PERCENT.value)
    discount_value = db.Column(db.Numeric(8, 2), nullable=False)
    # Mandatory for percentages (enforced above), optional for fixed amounts.
    max_discount_bs = db.Column(db.Numeric(8, 2), nullable=True)
    min_purchase_bs = db.Column(db.Numeric(10, 2), nullable=False, default=0, server_default="0")

    points_cost = db.Column(db.Integer, nullable=False)
    valid_days = db.Column(db.Integer, nullable=False, default=30, server_default="30")
    # NULL means unlimited.
    stock = db.Column(db.Integer, nullable=True)
    active = db.Column(
        db.Boolean, nullable=False, default=True, server_default=db.true()
    )

    business = db.relationship("Business", back_populates="rewards")
    coupons = db.relationship("Coupon", back_populates="reward")

    @property
    def discount_type_enum(self) -> DiscountType:
        return DiscountType.parse(self.discount_type) or DiscountType.PERCENT

    @property
    def is_unlimited(self) -> bool:
        return self.stock is None

    def is_available(self) -> bool:
        return self.active and (self.stock is None or self.stock > 0)

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "business_id": str(self.business_id) if self.business_id else None,
            "business_name": self.business.name if self.business else "Paseo Aranjuez",
            "title": self.title,
            "description": self.description,
            "discount_type": self.discount_type_enum.value,
            "discount_value": float(self.discount_value),
            "max_discount_bs": (
                float(self.max_discount_bs) if self.max_discount_bs is not None else None
            ),
            "min_purchase_bs": float(self.min_purchase_bs),
            "points_cost": self.points_cost,
            "valid_days": self.valid_days,
            "stock": self.stock,
            "active": self.active,
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Reward {self.title} cost={self.points_cost}>"


class Coupon(TimestampMixin, db.Model):
    __tablename__ = "coupons"
    __table_args__ = (
        UniqueConstraint("code", name="uq_coupons_code"),
        CheckConstraint(
            "discount_type IN ('percent', 'fixed')", name="ck_coupons_discount_type"
        ),
        CheckConstraint(
            "status IN ('active', 'used', 'cancelled')", name="ck_coupons_status"
        ),
        Index("ix_coupons_customer_created", "customer_id", "created_at"),
    )

    id = db.Column(Uuid(as_uuid=True), primary_key=True, default=new_uuid)

    customer_id = db.Column(
        Uuid(as_uuid=True),
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # No cascade: a reward that was already redeemed must not be deletable.
    reward_id = db.Column(
        Uuid(as_uuid=True),
        db.ForeignKey("rewards.id"),
        nullable=False,
        index=True,
    )

    code = db.Column(db.String(16), nullable=False, unique=True, default=new_coupon_code)

    # --- Snapshot of the reward's terms at issue time (see module docstring) --
    discount_type = db.Column(db.String(16), nullable=False)
    discount_value = db.Column(db.Numeric(8, 2), nullable=False)
    max_discount_bs = db.Column(db.Numeric(8, 2), nullable=True)
    min_purchase_bs = db.Column(db.Numeric(10, 2), nullable=False, default=0, server_default="0")

    status = db.Column(
        db.String(16),
        nullable=False,
        default=CouponStatus.ACTIVE.value,
        server_default=CouponStatus.ACTIVE.value,
    )
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)

    # The business that validated the redemption at the counter.
    validated_by = db.Column(
        Uuid(as_uuid=True),
        db.ForeignKey("business.id", ondelete="SET NULL"),
        nullable=True,
    )
    used_at = db.Column(db.DateTime(timezone=True), nullable=True)

    customer = db.relationship("User", back_populates="coupons")
    reward = db.relationship("Reward", back_populates="coupons")
    validated_by_business = db.relationship("Business", foreign_keys=[validated_by])
    transactions = db.relationship("Transaction", back_populates="coupon")
    orders = db.relationship("Order", back_populates="coupon")

    @property
    def status_enum(self) -> CouponStatus:
        return CouponStatus.parse(self.status) or CouponStatus.ACTIVE

    @property
    def is_redeemable(self) -> bool:
        return self.status_enum is CouponStatus.ACTIVE and not self.is_expired

    @property
    def is_expired(self) -> bool:
        """Whether the coupon's validity window has passed.

        `expires_at` is stored timezone-aware on PostgreSQL. SQLite (used only
        by the test suite) hands back naive datetimes, so an aware value is
        normalised to naive UTC before comparing -- otherwise the comparison
        would raise rather than answer, and a coupon would never expire in one
        of the two environments.

        Deliberately not `datetime.utcnow()`: that is deprecated in Python 3.12+
        and its replacement is built here anyway to guarantee the same shape on
        both sides of the comparison.
        """
        if self.expires_at is None:
            return False

        expires_at = self.expires_at
        if expires_at.tzinfo is not None:
            expires_at = expires_at.astimezone(datetime.timezone.utc).replace(tzinfo=None)

        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        return expires_at <= now

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "code": self.code,
            "reward_id": str(self.reward_id),
            "reward_title": self.reward.title if self.reward else None,
            "discount_type": self.discount_type,
            "discount_value": float(self.discount_value),
            "max_discount_bs": (
                float(self.max_discount_bs) if self.max_discount_bs is not None else None
            ),
            "min_purchase_bs": float(self.min_purchase_bs),
            "status": self.status_enum.value,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "expired": self.is_expired,
            "used_at": self.used_at.isoformat() if self.used_at else None,
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Coupon {self.code} status={self.status}>"
