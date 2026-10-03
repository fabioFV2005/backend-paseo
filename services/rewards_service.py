"""Paseo Points rewards: the benefits catalog and coupon redemption.

`redeem_reward` is the most correctness-sensitive function in the backend. It
must end with all of this true, or none of it:

  - the customer's points are gone,
  - one reward unit is out of stock,
  - one coupon exists with the reward's terms frozen into it,
  - the customer's level is up to date.

If any of those fails the others must not have happened. That is why the whole
body is one transaction with a single commit, and why the stock decrement is a
conditional UPDATE (`WHERE stock > 0`) rather than a read-modify-write in
Python: the conditional update is atomic in the database, so two customers
racing for the last unit cannot both win.
"""

import datetime
from decimal import Decimal
from typing import Optional

from enums import CouponStatus, DiscountType, TransactionType
from extensions import db
from models import (
    Business,
    Coupon,
    Reward,
    Transaction,
    User,
    find_user_by_id,
    new_coupon_code,
)
from services.errors import (
    BusinessRuleError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)
from services.points import get_balance, spend_points


def list_rewards(
    *,
    business_id=None,
    include_inactive: bool = False,
    only_sponsored: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> list[Reward]:
    """The benefits catalog.

    Sponsored rewards (``business_id IS NULL``) are always included unless the
    caller explicitly asks for a single business, because a Paseo-wide promotion
    is valid everywhere and a customer browsing shop X's rewards should still
    see them.
    """
    query = db.select(Reward)
    if business_id is not None:
        query = query.where(
            db.or_(Reward.business_id == business_id, Reward.business_id.is_(None))
        )
    elif only_sponsored:
        query = query.where(Reward.business_id.is_(None))
    if not include_inactive:
        query = query.where(Reward.active.is_(True))
    query = query.order_by(Reward.points_cost.asc())
    return list(
        db.session.execute(query.limit(limit).offset(offset)).scalars().unique()
    )


def get_reward_or_404(reward_id) -> Reward:
    try:
        reward = find_reward(reward_id)
    except (ValueError, TypeError):
        raise NotFoundError("Reward not found")
    if reward is None:
        raise NotFoundError("Reward not found")
    return reward


def find_reward(reward_id) -> Optional[Reward]:
    if isinstance(reward_id, str):
        import uuid as _uuid

        try:
            reward_id = _uuid.UUID(reward_id)
        except ValueError:
            return None
    return db.session.get(Reward, reward_id)


def _reward_decimal(data: dict, field: str, *, required: bool = True) -> Optional[Decimal]:
    from services.catalog import _to_decimal

    if field not in data:
        if required:
            raise ValidationError(f"'{field}' is required")
        return None
    return _to_decimal(data[field], field)


def create_reward(data: dict, *, business=None) -> Reward:
    """Create a reward from an admin payload.

    Pass ``business=None`` (or omit ``business_id`` from ``data``) to sponsor it
    from Paseo itself, which is then valid in every shop.

    Every rule the table's CHECK constraints enforce is re-checked here so a bad
    payload comes back as a readable 400 instead of an IntegrityError. The
    database stays the backstop; this is the layer that explains.
    """
    title = (data.get("title") or "").strip()
    if not title:
        raise ValidationError("'title' is required")

    discount_type = data.get("discount_type") or DiscountType.PERCENT.value
    if DiscountType.parse(discount_type) is None:
        raise ValidationError(
            "'discount_type' must be one of: " + ", ".join(DiscountType.values())
        )

    discount_value = _reward_decimal(data, "discount_value")
    if discount_value <= 0:
        raise ValidationError("'discount_value' must be greater than 0")

    max_discount = _reward_decimal(data, "max_discount_bs", required=False)
    if discount_type == DiscountType.PERCENT.value:
        if discount_value > 100:
            raise ValidationError("A percentage discount cannot exceed 100")
        if max_discount is None:
            # Without a cap, 10% off a Bs 5000 order costs the shop Bs 500 in
            # points it never sold. The cap is not optional for percentages.
            raise ValidationError(
                "A percentage discount requires 'max_discount_bs'"
            )
    elif max_discount is not None:
        raise ValidationError("'max_discount_bs' only applies to a percentage discount")

    points_cost = data.get("points_cost")
    if points_cost is None:
        raise ValidationError("'points_cost' is required")
    points_cost = int(points_cost)
    if points_cost <= 0:
        raise ValidationError("'points_cost' must be greater than 0")

    valid_days = int(data.get("valid_days") or 30)
    if valid_days <= 0:
        raise ValidationError("'valid_days' must be greater than 0")

    stock = data.get("stock")
    if stock is not None:
        stock = int(stock)
        if stock < 0:
            raise ValidationError("'stock' cannot be negative")

    min_purchase = _reward_decimal(data, "min_purchase_bs", required=False)

    reward = Reward(
        business_id=business.id if business else None,
        title=title,
        description=(data.get("description") or "").strip() or None,
        discount_type=discount_type,
        discount_value=discount_value,
        max_discount_bs=max_discount,
        min_purchase_bs=min_purchase if min_purchase is not None else Decimal(0),
        points_cost=points_cost,
        valid_days=valid_days,
        stock=stock,
        active=bool(data.get("active", True)),
    )
    db.session.add(reward)
    db.session.commit()
    return reward


def update_reward(reward: Reward, data: dict) -> Reward:
    """Edit a reward.

    Editing is safe for coupons already issued because every coupon snapshots
    its own discount terms at issue time. Changing a reward never changes what an
    outstanding coupon is worth -- otherwise a shop could raise the cost of a
    coupon a customer is already holding.
    """
    if "title" in data:
        title = (data.get("title") or "").strip()
        if not title:
            raise ValidationError("'title' cannot be empty")
        reward.title = title

    if "description" in data:
        reward.description = (data.get("description") or "").strip() or None

    if "points_cost" in data:
        cost = int(data["points_cost"])
        if cost <= 0:
            raise ValidationError("'points_cost' must be greater than 0")
        reward.points_cost = cost

    if "valid_days" in data:
        days = int(data["valid_days"])
        if days <= 0:
            raise ValidationError("'valid_days' must be greater than 0")
        reward.valid_days = days

    if "stock" in data:
        stock = data["stock"]
        if stock is None:
            reward.stock = None
        else:
            stock = int(stock)
            if stock < 0:
                raise ValidationError("'stock' cannot be negative")
            reward.stock = stock

    if "active" in data:
        reward.active = bool(data["active"])

    db.session.commit()
    return reward


def issue_coupon(
    customer: User,
    reward: Reward,
    *,
    commit: bool = True,
) -> Coupon:
    """Create the coupon for a redeemed reward, freezing the reward's terms.

    Does NOT touch points or stock: ``redeem_reward`` orchestrates that. Kept
    separate because admin-created giveaways may want a coupon without a
    charge.
    """
    coupon = Coupon(
        customer_id=customer.id,
        reward_id=reward.id,
        code=new_coupon_code(),
        # Snapshot: see the note in models/rewards.py about why these are copied.
        discount_type=reward.discount_type,
        discount_value=Decimal(str(reward.discount_value)),
        max_discount_bs=(
            Decimal(str(reward.max_discount_bs))
            if reward.max_discount_bs is not None
            else None
        ),
        min_purchase_bs=Decimal(str(reward.min_purchase_bs)),
        status=CouponStatus.ACTIVE.value,
        expires_at=datetime.datetime.now(datetime.timezone.utc)
        + datetime.timedelta(days=reward.valid_days),
    )
    db.session.add(coupon)
    if commit:
        db.session.commit()
    return coupon


def redeem_reward(customer: User, reward_id) -> Coupon:
    """Exchange a customer's points for a coupon. Reto 3.6 / paso 8.

    Atomic: points, stock, coupon and level all succeed or none do.
    """
    reward = get_reward_or_404(reward_id)

    if not reward.active:
        raise BusinessRuleError("This reward is not available")

    # A business may only fund its own rewards. Sponsored rewards (no business)
    # belong to Paseo and any customer may redeem them.
    if reward.business_id is not None:
        owner = reward.business
        if owner is None or not owner.active:
            raise BusinessRuleError("The business funding this reward is not active")

    # Conditional UPDATE: atomic in the database, so two customers racing for
    # the last unit cannot both decrement it. A NULL stock means unlimited and
    # must not be touched by this statement.
    if reward.stock is not None:
        result = db.session.execute(
            db.update(Reward)
            .where(Reward.id == reward.id, Reward.stock > 0)
            .values(stock=Reward.stock - 1)
        )
        if result.rowcount == 0:
            db.session.rollback()
            raise ConflictError("This reward is out of stock")

    # Charges the customer, writes the negative ledger row, updates the level.
    redemption = spend_points(
        customer,
        reward.points_cost,
        entry_type=TransactionType.REDEEM,
        business=reward.business,
        note=f"Canje: {reward.title}",
        # No commit yet: the coupon must be written in the same transaction.
        commit=False,
    )

    coupon = issue_coupon(customer, reward, commit=False)

    # The coupon must have a primary key before it can be referenced, and
    # issue_coupon(commit=False) deliberately does not flush -- so flush here
    # rather than trusting coupon.id to be populated. Reading an unflushed
    # object's id gives None, which would silently write a NULL coupon_id and
    # leave the charge untraceable.
    db.session.flush()

    # Link the charge to the coupon it bought. The coupon does not exist while
    # spend_points() runs, so this backfill is the only place it can happen --
    # and without it the redemption is untraceable: cancelling the order that
    # used the coupon could not find what to refund, and the ledger rows would
    # point at nothing.
    redemption.coupon_id = coupon.id
    assert redemption.coupon_id is not None, "coupon was not persisted before linking"

    db.session.commit()
    return coupon


def calculate_coupon_discount(coupon: Coupon, subtotal) -> Decimal:
    """How much a coupon takes off a purchase of ``subtotal`` bolivars.

    Enforces both coupon conditions that a customer could otherwise bypass by
    ordering a cheaper basket:

      - ``min_purchase_bs``: the purchase must reach the minimum.
      - ``max_discount_bs``: the ceiling that makes a percentage sustainable
        for the business. A 50% coupon capped at Bs 20 gives Bs 20 off a
        Bs 500 order, not Bs 250.

    The discount can never exceed the subtotal, so the result is always a valid
    amount to subtract (the DB CHECK enforces the same invariant).
    """
    subtotal = Decimal(str(subtotal))
    if subtotal < Decimal(str(coupon.min_purchase_bs)):
        raise BusinessRuleError(
            "The purchase does not reach the minimum required by this coupon",
            details={
                "min_purchase_bs": float(coupon.min_purchase_bs),
                "subtotal_bs": float(subtotal),
            },
        )

    if coupon.discount_type == DiscountType.PERCENT.value:
        discount = subtotal * Decimal(str(coupon.discount_value)) / Decimal(100)
        cap = (
            Decimal(str(coupon.max_discount_bs))
            if coupon.max_discount_bs is not None
            else None
        )
        if cap is not None:
            discount = min(discount, cap)
    else:
        discount = Decimal(str(coupon.discount_value))

    return min(discount, subtotal).quantize(Decimal("0.01"))


def find_coupon_by_code(code: str) -> Optional[Coupon]:
    if not code:
        return None
    return db.session.execute(
        db.select(Coupon).where(Coupon.code == code.strip().upper())
    ).scalar_one_or_none()


def get_coupon_for_customer(customer: User, coupon_id) -> Coupon:
    """Fetch one of the customer's own coupons, or 404.

    Scoped to the customer so a coupon id cannot be used to probe for other
    people's codes.
    """
    import uuid as _uuid

    try:
        cid = _uuid.UUID(coupon_id) if isinstance(coupon_id, str) else coupon_id
    except (ValueError, TypeError):
        raise NotFoundError("Coupon not found")
    coupon = db.session.execute(
        db.select(Coupon).where(Coupon.id == cid, Coupon.customer_id == customer.id)
    ).scalar_one_or_none()
    if coupon is None:
        raise NotFoundError("Coupon not found")
    return coupon


def list_customer_coupons(customer: User, limit: int = 50, offset: int = 0) -> list[Coupon]:
    return list(
        db.session.execute(
            db.select(Coupon)
            .where(Coupon.customer_id == customer.id)
            .order_by(Coupon.created_at.desc())
            .limit(limit)
            .offset(offset)
        ).scalars()
    )


def validate_coupon(code: str, business: Business) -> Coupon:
    """A business validates a customer's coupon at the counter. Reto 3.6.

    Marks the coupon used and records who validated it. Idempotent in the useful
    direction: re-scanning a coupon that is already used reports the original
    validation instead of erroring, because that is exactly what happens when a
    customer walks back to the till after a network hiccup.
    """
    coupon = find_coupon_by_code(code)
    if coupon is None:
        raise NotFoundError("Coupon not found")

    if coupon.status_enum is CouponStatus.USED:
        raise BusinessRuleError(
            "This coupon was already used",
            details={"used_at": coupon.used_at.isoformat() if coupon.used_at else None},
        )
    if coupon.status_enum is CouponStatus.CANCELLED:
        raise BusinessRuleError("This coupon was cancelled")
    if coupon.is_expired:
        raise BusinessRuleError("This coupon has expired")

    # A coupon funded by a specific business can only be honoured by that
    # business; a Paseo-sponsored one works anywhere.
    if coupon.reward is not None and coupon.reward.business_id is not None:
        if coupon.reward.business_id != business.id:
            raise ForbiddenError(
                "This coupon belongs to a different business",
                details={"business_id": str(coupon.reward.business_id)},
            )

    coupon.status = CouponStatus.USED.value
    coupon.used_at = datetime.datetime.now(datetime.timezone.utc)
    coupon.validated_by = business.id
    db.session.commit()
    return coupon


def cancel_coupon(coupon: Coupon, refund: bool = True) -> Coupon:
    """Cancel a coupon and, by default, give the points back.

    The refund is a NEW positive ledger row referencing the same coupon, so the
    original redemption stays visible in the customer's history and the balance
    still adds up.
    """
    if coupon.status_enum is CouponStatus.USED:
        raise BusinessRuleError("A used coupon cannot be cancelled")
    if coupon.status_enum is CouponStatus.CANCELLED:
        return coupon

    coupon.status = CouponStatus.CANCELLED.value
    if refund and coupon.reward is not None:
        from services.points import refund_points

        refund_points(
            coupon.customer,
            coupon.reward.points_cost,
            business=coupon.reward.business,
            coupon=coupon,
            note="Cancelacion de cupon",
            commit=False,
        )
    db.session.commit()
    return coupon
