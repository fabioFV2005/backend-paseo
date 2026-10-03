"""Paseo Points: the loyalty ledger.

The invariant this module protects: **a balance is the SUM of the ledger rows,
and every balance change is exactly one new row.** Nothing is ever updated in
place. That is what makes the movement history trustworthy enough to show a
customer, and it is why `User.points_balance` is a derived column rather than a
stored number.

Concurrency: redeeming a coupon checks the balance and then writes. Two
redemptions racing on the same customer would both read the same balance and
both succeed if we did not serialize them. `spend_points` therefore takes a row
lock on the customer (`SELECT ... FOR UPDATE`) before re-reading the balance, so
the second transaction blocks and then sees the first one's write. That is the
minimal correct fix and it needs no extra infrastructure.
"""

import math
from decimal import Decimal
from typing import Optional

from enums import CustomerLevel, TransactionType
from extensions import db
from models import Business, Transaction, User
from services.errors import BusinessRuleError, ConflictError, ForbiddenError, ValidationError

# Level thresholds in points. The reto's own examples (300 for a coupon,
# 700 for a promotional product, 1500 for an exclusive promotion) are used as
# the tier boundaries.
LEVEL_THRESHOLDS: list[tuple[CustomerLevel, int]] = [
    (CustomerLevel.BRONZE, 0),
    (CustomerLevel.SILVER, 300),
    (CustomerLevel.GOLD, 700),
    (CustomerLevel.PLATINUM, 1500),
]


# --- Reading ----------------------------------------------------------------


def get_balance(customer: User) -> int:
    """Current point balance. Always read from the ledger, never cached."""
    row = db.session.execute(
        db.select(db.func.coalesce(db.func.sum(Transaction.points), 0)).where(
            Transaction.customer_id == customer.id
        )
    ).scalar_one()
    return int(row)


def level_for_balance(balance: int) -> CustomerLevel:
    """Highest level whose threshold the balance has reached."""
    level = CustomerLevel.BRONZE
    for candidate, threshold in LEVEL_THRESHOLDS:
        if balance >= threshold:
            level = candidate
    return level


def get_level_progress(balance: int) -> dict:
    """Everything the frontend needs to render a progress bar to the next tier.

    Returns the current level, the next one, the points still missing and a
    0..1 progress ratio. At the top level ``next_level`` is None and progress
    is 1.0, so the UI has one code path instead of two.
    """
    current = level_for_balance(balance)
    current_index = [lvl for lvl, _ in LEVEL_THRESHOLDS].index(current)
    floor_points = LEVEL_THRESHOLDS[current_index][1]

    if current_index + 1 >= len(LEVEL_THRESHOLDS):
        return {
            "level": current.value,
            "next_level": None,
            "points_to_next": 0,
            "progress": 1.0,
            "current_threshold": floor_points,
        }

    next_level, next_threshold = LEVEL_THRESHOLDS[current_index + 1]
    span = next_threshold - floor_points
    progress = 0.0 if span <= 0 else min(1.0, max(0.0, (balance - floor_points) / span))
    return {
        "level": current.value,
        "next_level": next_level.value,
        "points_to_next": max(0, next_threshold - balance),
        "progress": round(progress, 4),
        "current_threshold": floor_points,
    }


def points_for_purchase(amount_bs, points_per_bs) -> int:
    """Points earned by a purchase, rounded DOWN.

    Rounding down is deliberate and consistent with crediting the customer: if
    rounding went the other way the business would pay for points nobody
    earned. Whole points only, because the ledger column is an integer.
    """
    amount = Decimal(str(amount_bs))
    rate = Decimal(str(points_per_bs))
    return int(math.floor(amount * rate))


# --- Writing ----------------------------------------------------------------


def _write_entry(
    customer: User,
    points: int,
    entry_type: TransactionType,
    *,
    business: Optional[Business] = None,
    order=None,
    coupon=None,
    amount_bs=None,
    note: Optional[str] = None,
) -> Transaction:
    """Append one ledger row and refresh the customer's level.

    Assumes it is called inside a transaction the caller commits. The
    ``points <> 0`` CHECK on the table is the last line of defence against a
    meaningless zero-value movement reaching the database.
    """
    if points == 0:
        raise ValidationError(
            "A points movement of zero is not a valid ledger entry",
            details={"type": entry_type.value},
        )

    entry = Transaction(
        customer_id=customer.id,
        business_id=business.id if business else None,
        order_id=order.id if order else None,
        coupon_id=coupon.id if coupon else None,
        type=entry_type.value,
        points=points,
        amount_bs=Decimal(str(amount_bs)) if amount_bs is not None else None,
        note=note,
    )
    db.session.add(entry)

    # Recompute the level inside the same transaction as the movement, so a
    # crash between the two can never leave a customer at the wrong tier.
    # flush() first: the SUM has to see the row we just added.
    db.session.flush()
    customer.level = level_for_balance(get_balance(customer)).value

    return entry


def credit_purchase(
    customer: User,
    business: Business,
    amount_bs,
    *,
    order=None,
    note: Optional[str] = None,
    commit: bool = True,
) -> Optional[Transaction]:
    """Credit points for a purchase registered by a business.

    This is the QR-scan flow of reto 3.7 and, when ``order`` is given, the
    PaseoYa delivery flow. The rate is the business's own ``points_per_bs``.

    Returns None when the purchase is too small to be worth a single point
    (for example Bs 0.50 at 1 point per bolivar). That is not an error: the
    caller reports ``points_credited: 0`` and the sale still stands. Returning
    None rather than raising is what keeps a 1-point-per-bolivar rate from
    rejecting a customer who bought a single coffee.
    """
    if business is None:
        raise ValidationError("A business is required to credit a purchase")
    if not business.active:
        raise ForbiddenError("This business is not active and cannot credit points")
    if business.user_id is not None and business.user_id == customer.id:
        # Otherwise a shop could scan its own QR, mint an arbitrary balance and
        # redeem rewards against points it invented. The ledger would look
        # legitimate: a real business, a real EARN row. Refused before any
        # money-equivalent value is created.
        raise BusinessRuleError(
            "A business cannot credit points to its own owner",
            details={"business_id": str(business.id), "customer_id": str(customer.id)},
        )

    amount = Decimal(str(amount_bs))
    if amount <= 0:
        raise ValidationError("The purchase amount must be greater than 0")

    earned = points_for_purchase(amount, business.points_per_bs)
    if earned < 1:
        return None

    entry = _write_entry(
        customer,
        earned,
        TransactionType.EARN,
        business=business,
        order=order,
        amount_bs=amount,
        note=note or f"Compra en {business.name}",
    )
    if commit:
        db.session.commit()
    return entry


def spend_points(
    customer: User,
    points: int,
    *,
    entry_type: TransactionType = TransactionType.REDEEM,
    business: Optional[Business] = None,
    order=None,
    coupon=None,
    note: Optional[str] = None,
    commit: bool = True,
) -> Transaction:
    """Deduct points, refusing to overdraw the customer.

    ``entry_type`` defaults to REDEEM. A caller that needs to verify something
    beyond "enough points" must check it BEFORE calling this, inside its own
    transaction: once points are gone the refund path exists but is a different
    code path with different semantics.
    """
    if points <= 0:
        raise ValidationError("Points to spend must be greater than 0")

    # Serialize concurrent spends on the same customer: the second transaction
    # blocks here until the first one commits, then re-reads the balance below
    # and sees the deduction.
    db.session.execute(
        db.select(User).where(User.id == customer.id).with_for_update()
    ).scalar_one()

    balance = get_balance(customer)
    if balance < points:
        raise ConflictError(
            "Not enough points",
            details={"balance": balance, "required": points, "missing": points - balance},
        )

    entry = _write_entry(
        customer,
        -points,
        entry_type,
        business=business,
        order=order,
        coupon=coupon,
        note=note,
    )
    if commit:
        db.session.commit()
    return entry


def refund_points(
    customer: User,
    points: int,
    *,
    business: Optional[Business] = None,
    order=None,
    coupon=None,
    note: Optional[str] = None,
    commit: bool = True,
) -> Transaction:
    """Give points back: a cancelled coupon or a corrected manual entry.

    Always a NEW positive row. Rewriting the original negative row would erase
    the fact that a redemption ever happened, and the customer's statement
    would stop adding up.
    """
    if points <= 0:
        raise ValidationError("Points to refund must be greater than 0")

    entry = _write_entry(
        customer,
        abs(points),
        TransactionType.REFUND,
        business=business,
        order=order,
        coupon=coupon,
        note=note or "Devolucion de puntos",
    )
    if commit:
        db.session.commit()
    return entry


def adjust_points(
    customer: User,
    delta: int,
    *,
    note: Optional[str] = None,
    commit: bool = True,
) -> Transaction:
    """Manual correction by a Paseo admin.

    The escape hatch for the real world: a customer complains, an operator
    fixes their balance. Positive and negative deltas are both allowed, which
    is why the type is ADJUST rather than BONUS. ``note`` is effectively
    mandatory in practice -- the admin API requires it -- because an unexplained
    balance change in an append-only ledger is indistinguishable from a bug.
    """
    if delta == 0:
        raise ValidationError("The adjustment must not be zero")

    if delta < 0:
        db.session.execute(
            db.select(User).where(User.id == customer.id).with_for_update()
        ).scalar_one()
        if get_balance(customer) < abs(delta):
            balance = get_balance(customer)
            raise ConflictError(
                "Cannot remove more points than the customer has",
                details={
                    "balance": balance,
                    "requested": abs(delta),
                    "missing": abs(delta) - balance,
                },
            )

    entry_type = TransactionType.BONUS if delta > 0 else TransactionType.ADJUST
    entry = _write_entry(
        customer,
        delta,
        entry_type,
        note=note or "Ajuste manual del administrador",
    )
    if commit:
        db.session.commit()
    return entry


def list_transactions(customer: User, limit: int = 50, offset: int = 0) -> list[Transaction]:
    """Most recent movements first. Indexed by (customer_id, created_at DESC)."""
    return list(
        db.session.execute(
            db.select(Transaction)
            .where(Transaction.customer_id == customer.id)
            .order_by(Transaction.created_at.desc(), Transaction.id.desc())
            .limit(limit)
            .offset(offset)
        ).scalars()
    )
