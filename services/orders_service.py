"""PaseoYa orders: checkout, lifecycle and pickup validation.

Three rules drive the whole module.

**1. The client never sends a price.** Checkout takes product ids and
quantities and re-reads every price from `products` inside the transaction.
A tampered request that says "1 x Audifonos, 1 Bs" is ignored, because the
totals are computed from the database rows the request only pointed at. This is
the single most important integrity rule in the backend.

**2. A cart belongs to exactly one business.** PaseoYa is pickup-only, and the
whole point is one trip to the mall. A cart spanning two shops is rejected at
checkout rather than silently split into two orders, because two orders means
two pickup codes, two payments to confirm and two walks across the Paseo.

**3. Points are credited on DELIVERY, not on payment.** Crediting at checkout
would leave phantom points whenever a customer abandons or a business cancels
an order. On delivery the money has actually changed hands and the stock is
genuinely gone, so the ledger entry is a fact rather than a promise. The order
carries ``points_earned`` as a preview for the UI, but the real entry is the
ledger row written at pickup.
"""

import datetime
import uuid
from decimal import Decimal
from typing import Optional

from enums import CouponStatus, OrderStatus, PaymentMethod, TransactionType
from extensions import db
from models import (
    Coupon,
    Order,
    OrderItem,
    Product,
    Transaction,
    User,
    new_pickup_code,
)
from services.catalog import get_product
from services.errors import (
    BusinessRuleError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)
from services.points import credit_purchase, points_for_purchase, refund_points
from services.rewards_service import (
    calculate_coupon_discount,
    find_coupon_by_code,
    get_coupon_for_customer,
)

MAX_QTY_PER_ITEM = 99


# --- Checkout ---------------------------------------------------------------


def _parse_cart(payload) -> list[tuple[str, int]]:
    """Validate the incoming cart shape and collapse duplicate lines.

    Accepting duplicate lines for the same product and summing them means a
    client cannot slip past a per-line quantity cap by splitting the quantity
    across two entries.
    """
    if not payload:
        raise ValidationError("The cart is empty")
    if not isinstance(payload, list):
        raise ValidationError("'items' must be a list")

    quantities: dict[str, int] = {}
    for index, line in enumerate(payload):
        if not isinstance(line, dict):
            raise ValidationError(f"Item {index + 1} must be an object")
        product_id = line.get("product_id")
        if not product_id:
            raise ValidationError(f"Item {index + 1} is missing 'product_id'")
        try:
            quantity = int(line.get("quantity", 1))
        except (TypeError, ValueError):
            raise ValidationError(f"Item {index + 1} has a non-integer 'quantity'")
        if quantity <= 0:
            raise ValidationError(f"Item {index + 1} must have a quantity of at least 1")
        if quantity > MAX_QTY_PER_ITEM:
            raise ValidationError(
                f"Item {index + 1} exceeds the maximum of {MAX_QTY_PER_ITEM} units"
            )
        quantities[str(product_id)] = quantities.get(str(product_id), 0) + quantity

    for _, quantity in quantities.items():
        if quantity > MAX_QTY_PER_ITEM:
            raise ValidationError(
                f"Combined quantity exceeds the maximum of {MAX_QTY_PER_ITEM} units"
            )
    return list(quantities.items())


def _generate_order_number() -> str:
    """Build a human-readable order reference like "PA-20261003-0007".

    Sequenced per day because customers and staff read it out loud at the
    counter.

    Known race, accepted on purpose: two checkouts at the same instant both
    count N orders and both propose N+1, so one of them hits the UNIQUE
    constraint. The constraint is the real guarantee -- the count is only a
    convenience. A retry loop around the INSERT would have to savepoint the
    whole stock decrement to stay correct, which is more machinery than this
    deserves: it fails a handful of orders a day at worst, the customer retries
    and gets the next number. The UNIQUE constraint is what stops a duplicate
    receipt, so the failure mode is a rejected order, never a double one.
    """
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d")
    prefix = f"PA-{today}-"
    count = db.session.execute(
        db.select(db.func.count(Order.id)).where(Order.order_number.like(f"{prefix}%"))
    ).scalar_one()
    return f"{prefix}{int(count) + 1:04d}"


def create_order(
    customer: User,
    items_payload,
    *,
    coupon_id=None,
    coupon_code: Optional[str] = None,
    payment_method: Optional[str] = None,
    note: Optional[str] = None,
    delivery_type: str = "pickup",
    delivery_address: Optional[str] = None,
    delivery_phone: Optional[str] = None,
    delivery_instructions: Optional[str] = None,
) -> Order:
    """Turn a client cart into a persisted order. Reto 5.5.

    Atomic: stock, the coupon and the order either all exist or none do.

    A coupon is referenced by ``coupon_id`` or ``coupon_code``, never both. The
    id is what the frontend already holds after GET /api/me/coupons; the code is
    what someone reads off a printed coupon and types in.
    """
    if coupon_id and coupon_code:
        raise ValidationError("Provide either 'coupon_id' or 'coupon_code', not both")

    cart = _parse_cart(items_payload)

    if payment_method is not None:
        if PaymentMethod.parse(payment_method) is None:
            raise ValidationError(
                "'payment_method' must be one of: "
                + ", ".join(PaymentMethod.values())
            )

    # Lock the product rows so two concurrent checkouts cannot both pass the
    # stock check and then both decrement.
    products: list[Product] = []
    for product_id, quantity in cart:
        product = get_product(product_id)
        if not product.active:
            raise BusinessRuleError(
                f"'{product.name}' is no longer available",
                details={"product_id": product_id},
            )
        if quantity > product.stock:
            raise ConflictError(
                f"Only {product.stock} unit(s) of '{product.name}' left",
                details={"product_id": product_id, "requested": quantity, "stock": product.stock},
            )
        products.append(product)

    # --- Rule 2: one business per order ---
    business_ids = {p.business_id for p in products}
    if len(business_ids) > 1:
        raise BusinessRuleError(
            "All items must belong to the same business: PaseoYa orders are "
            "picked up in a single visit",
            details={"business_ids": sorted(str(b) for b in business_ids)},
        )
    business = products[0].business
    if business is None or not business.active:
        raise BusinessRuleError("This business is not accepting orders")

    # --- Server-side pricing ---
    subtotal = Decimal("0.00")
    lines: list[tuple[Product, int, Decimal, Decimal]] = []
    for product, (_, quantity) in zip(products, cart):
        unit_price = Decimal(str(product.price_bs))
        line_total = (unit_price * quantity).quantize(Decimal("0.01"))
        subtotal += line_total
        lines.append((product, quantity, unit_price, line_total))
    subtotal = subtotal.quantize(Decimal("0.01"))

    # --- Optional coupon from Paseo Points ---
    coupon: Optional[Coupon] = None
    discount = Decimal("0.00")
    if coupon_id or coupon_code:
        if coupon_code:
            coupon = find_coupon_by_code(coupon_code)
            if coupon is None or coupon.customer_id != customer.id:
                # Never reveal that somebody else's code exists.
                raise NotFoundError("Coupon not found")
        else:
            # Scoped to the owner, so another customer's coupon id is a 404
            # here rather than a 403 that would confirm it exists.
            coupon = get_coupon_for_customer(customer, coupon_id)
        if not coupon.is_redeemable:
            reason = (
                "expired"
                if coupon.is_expired
                else coupon.status_enum.value
            )
            raise BusinessRuleError(
                f"This coupon cannot be used ({reason})",
                details={"status": coupon.status_enum.value},
            )
        if coupon.reward is not None and coupon.reward.business_id is not None:
            if coupon.reward.business_id != business.id:
                raise BusinessRuleError(
                    "This coupon belongs to a different business",
                    details={"coupon_business_id": str(coupon.reward.business_id)},
                )
        discount = calculate_coupon_discount(coupon, subtotal)

    delivery_type = (delivery_type or "pickup").strip().lower()
    if delivery_type not in ("pickup", "delivery"):
        raise ValidationError("'delivery_type' must be either 'pickup' or 'delivery'")

    delivery_fee = Decimal("0.00")
    if delivery_type == "delivery":
        if not business.offers_delivery:
            raise BusinessRuleError(
                f"'{business.name}' no ofrece envíos a domicilio, solo retiro en tienda."
            )
        delivery_address = (delivery_address or "").strip()
        if not delivery_address:
            raise ValidationError(
                "La dirección de entrega ('delivery_address') es obligatoria para pedidos a domicilio"
            )
        delivery_fee = Decimal(str(business.delivery_fee_bs or 0)).quantize(Decimal("0.01"))

    total = (subtotal - discount + delivery_fee).quantize(Decimal("0.01"))

    # Points are previewed on the amount actually PAID, not on the subtotal.
    # Crediting the subtotal would make coupons a points exploit: buy Bs 1000,
    # pay Bs 100 with a huge coupon, and still collect points for Bs 1000.
    points_preview = points_for_purchase(total, business.points_per_bs)

    order = Order(
        id=uuid.uuid4(),
        order_number=_generate_order_number(),
        customer_id=customer.id,
        business_id=business.id,
        status=OrderStatus.RECEIVED.value,
        subtotal_bs=subtotal,
        discount_bs=discount,
        delivery_fee_bs=delivery_fee,
        total_bs=total,
        coupon_id=coupon.id if coupon else None,
        points_earned=points_preview,
        payment_method=payment_method,
        delivery_type=delivery_type,
        delivery_address=delivery_address if delivery_type == "delivery" else None,
        delivery_phone=(delivery_phone or "").strip() or None,
        delivery_instructions=(delivery_instructions or "").strip() or None,
        pickup_code=new_pickup_code(),
        note=(note or "").strip()[:300] or None,
    )
    db.session.add(order)

    for product, quantity, unit_price, line_total in lines:
        db.session.add(
            OrderItem(
                order_id=order.id,
                product_id=product.id,
                # Snapshot: the receipt must survive a rename or a deletion.
                product_name=product.name,
                unit_price_bs=unit_price,
                quantity=quantity,
                subtotal_bs=line_total,
            )
        )
        # Conditional decrement: atomic, so the stock check above cannot be
        # raced past by a concurrent checkout.
        result = db.session.execute(
            db.update(Product)
            .where(Product.id == product.id, Product.stock >= quantity)
            .values(stock=Product.stock - quantity)
        )
        if result.rowcount == 0:
            db.session.rollback()
            raise ConflictError(
                f"'{product.name}' just ran out of stock",
                details={"product_id": str(product.id)},
            )

    if coupon is not None:
        # The discount is already applied to this order, so the coupon is
        # consumed here rather than waiting to be scanned at the counter.
        coupon.status = CouponStatus.USED.value
        coupon.used_at = datetime.datetime.now(datetime.timezone.utc)
        coupon.validated_by = business.id

    db.session.commit()
    return order


# --- Reading ----------------------------------------------------------------


def get_order(order_id) -> Order:
    try:
        oid = uuid.UUID(order_id) if isinstance(order_id, str) else order_id
    except (ValueError, TypeError, AttributeError):
        raise NotFoundError("Order not found")
    order = db.session.get(Order, oid)
    if order is None:
        raise NotFoundError("Order not found")
    return order


def get_order_for_customer(order_id, customer: User) -> Order:
    order = get_order(order_id)
    if order.customer_id != customer.id:
        raise NotFoundError("Order not found")
    return order


def list_customer_orders(
    customer: User, *, status: Optional[str] = None, limit: int = 50, offset: int = 0
) -> list[Order]:
    query = db.select(Order).where(Order.customer_id == customer.id)
    if status:
        if OrderStatus.parse(status) is None:
            raise ValidationError("Unknown order status")
        query = query.where(Order.status == status)
    query = query.order_by(Order.created_at.desc())
    return list(db.session.execute(query.limit(limit).offset(offset)).scalars())


def list_business_orders(
    business_id, *, status: Optional[str] = None, limit: int = 50, offset: int = 0
) -> list[Order]:
    query = db.select(Order).where(Order.business_id == business_id)
    if status:
        if OrderStatus.parse(status) is None:
            raise ValidationError("Unknown order status")
        query = query.where(Order.status == status)
    # Open orders first, oldest first: that is the queue a business works
    # through during the day.
    query = query.order_by(Order.created_at.asc())
    return list(db.session.execute(query.limit(limit).offset(offset)).scalars())


# --- Lifecycle --------------------------------------------------------------


def assert_business_owns(business, order: Order) -> None:
    if order.business_id != business.id:
        raise ForbiddenError("This order belongs to a different business")


def advance_order_status(order: Order, new_status: str, business=None) -> Order:
    """Move an order forward (reto 5.8).

    Only the owning business may drive it, and only along the legal path in
    ``Order.TRANSITIONS``. Reaching DELIVERED is what credits the points.
    """
    target = OrderStatus.parse(new_status)
    if target is None:
        raise ValidationError(
            "Unknown status", details={"allowed": sorted(OrderStatus.values())}
        )
    if business is not None:
        assert_business_owns(business, order)
    if not order.can_transition_to(target):
        raise BusinessRuleError(
            f"Cannot move an order from '{order.status}' to '{target.value}'",
            details={"allowed": order.allowed_transitions()},
        )

    now = datetime.datetime.now(datetime.timezone.utc)
    order.status = target.value
    if target is OrderStatus.CONFIRMED:
        order.confirmed_at = order.confirmed_at or now
    elif target is OrderStatus.READY_FOR_PICKUP:
        order.ready_at = now
    elif target is OrderStatus.CUSTOMER_ARRIVED:
        order.arrived_at = now

    db.session.flush()

    if target is OrderStatus.DELIVERED:
        order.delivered_at = now
        # Cancel within the same transaction: if crediting fails, the order
        # must not sit in DELIVERED with no points.
        _credit_order_points(order, business)

    db.session.commit()
    return order


def mark_customer_arrived(order: Order, customer: User) -> Order:
    """The customer taps "I am at the Paseo". Reto 5.8.

    Optional step: READY_FOR_PICKUP goes straight to DELIVERED if the customer
    never presses it. It exists to give the business a heads-up.
    """
    if order.customer_id != customer.id:
        raise NotFoundError("Order not found")
    if not order.can_transition_to(OrderStatus.CUSTOMER_ARRIVED):
        raise BusinessRuleError(
            "This order cannot be marked as arrived in its current state",
            details={"status": order.status},
        )
    order.status = OrderStatus.CUSTOMER_ARRIVED.value
    order.arrived_at = datetime.datetime.now(datetime.timezone.utc)
    db.session.commit()
    return order


def validate_pickup(order: Order, code: str, business=None) -> Order:
    """Redeem the pickup code at the counter. Reto 5.9 / paso 11.

    Scans the customer's QR (or types the 6 digits), confirms the order belongs
    to this business and to this customer, and hands it over. Delivering is
    what credits the loyalty points.
    """
    expected = (order.pickup_code or "").strip()
    supplied = (code or "").strip()
    if not supplied or supplied != expected:
        # Deliberately does not say which of the two was wrong.
        raise ForbiddenError("The pickup code is not valid for this order")

    if business is not None:
        assert_business_owns(business, order)

    if order.status_enum is OrderStatus.DELIVERED:
        raise BusinessRuleError("This order was already delivered")
    if order.status_enum is OrderStatus.CANCELLED:
        raise BusinessRuleError("This order was cancelled")
    if order.status_enum not in (
        OrderStatus.READY_FOR_PICKUP,
        OrderStatus.CUSTOMER_ARRIVED,
    ):
        # Refusing to hand over something the shop has not finished is the whole
        # point of the lifecycle. Without this check a cashier could deliver a
        # `received` order straight from the counter, the customer would get a
        # hot drink that does not exist, and the points would be credited for
        # it. The customer standing there is not evidence the food exists.
        raise BusinessRuleError(
            "This order is not ready for pickup yet",
            details={
                "status": order.status_enum.value,
                "allowed_from": [
                    OrderStatus.READY_FOR_PICKUP.value,
                    OrderStatus.CUSTOMER_ARRIVED.value,
                ],
            },
        )

    order.status = OrderStatus.DELIVERED.value
    order.delivered_at = datetime.datetime.now(datetime.timezone.utc)
    db.session.flush()
    _credit_order_points(order, business)
    db.session.commit()
    return order


def _credit_order_points(order: Order, business=None) -> None:
    """Write the EARN ledger row for a delivered order.

    Idempotent: if a ledger row already references this order, nothing is
    written. The status transition normally prevents a second call, but the
    guard makes the invariant hold even if a future caller reaches here a
    different way. Double-crediting points is the worst bug this system could
    have, so it is worth the one extra query.
    """
    already = db.session.execute(
        db.select(Transaction.id).where(
            Transaction.order_id == order.id,
            Transaction.type == TransactionType.EARN.value,
        ).limit(1)
    ).scalar_one_or_none()
    if already is not None:
        return

    shop = business or order.business
    entry = credit_purchase(
        order.customer,
        shop,
        order.total_bs,
        order=order,
        note=f"Pedido {order.order_number}",
        commit=False,
    )
    # Refresh the preview so the response shows what was actually credited,
    # which may be 0 for a purchase too small to earn anything.
    order.points_earned = entry.points if entry is not None else 0


def cancel_order(order: Order, *, reason: Optional[str] = None, business=None) -> Order:
    """Cancel an open order: restore stock and undo anything it consumed.

    Two things were consumed and both have to come back, or the customer is
    punished for the shop cancelling:

    - **stock**, because the goods were never handed over;
    - **the points spent on a coupon**, because that coupon bought a discount
      which is now void. Leaving the redemption in place would mean the
      customer paid 300 points for nothing, and the only way to fix it would be
      an admin manually crediting them back.

    The refund is a NEW ledger row, never an edit of the redemption: the
    customer's statement has to keep adding up to their balance, and a
    redemption that silently vanished would hide a real event.

    A cancellation after delivery is refused rather than silently reversed: at
    that point the money moved and the walk home happened, so undoing it is a
    decision for a human, made through the admin adjustment path.
    """
    if business is not None:
        assert_business_owns(business, order)
    if order.status_enum is OrderStatus.DELIVERED:
        raise BusinessRuleError(
            "A delivered order cannot be cancelled; request an admin adjustment"
        )
    if not order.can_transition_to(OrderStatus.CANCELLED):
        raise BusinessRuleError("This order cannot be cancelled in its current state")

    order.status = OrderStatus.CANCELLED.value
    if reason:
        order.note = f"{order.note or ''} | Cancelado: {reason}".strip()[:300]

    # Put the stock back: the goods were never handed over.
    for item in order.items:
        if item.product_id is not None:
            db.session.execute(
                db.update(Product)
                .where(Product.id == item.product_id)
                .values(stock=Product.stock + item.quantity)
            )

    # Return the coupon's points and make the coupon usable again.
    if order.coupon_id is not None:
        coupon = db.session.get(Coupon, order.coupon_id)
        if coupon is not None:
            spent = _points_spent_for_coupon(coupon)
            if spent > 0:
                # A Coupon has no business of its own: the funder is whoever
                # owns the reward behind it, and for a Paseo-sponsored reward
                # that is nobody. Attributing the refund that way keeps the
                # movement history honest about who gave the points back.
                reward = coupon.reward
                refund_points(
                    order.customer,
                    spent,
                    business=reward.business if reward is not None else None,
                    coupon=coupon,
                    note=f"Cancelacion del pedido {order.order_number}",
                    commit=False,
                )
            # Back to 'active', not 'cancelled': the redemption is reversed
            # above, so the coupon never really took effect and the customer
            # is entitled to spend it on another order. An expired coupon
            # stays put -- is_redeemable checks is_expired independently, so
            # restoring it to 'active' cannot resurrect it past its deadline.
            coupon.status = CouponStatus.ACTIVE.value
            coupon.used_at = None
            coupon.validated_by = None

    db.session.commit()
    return order


def _points_spent_for_coupon(coupon: Coupon) -> int:
    """How many points were deducted to obtain this coupon.

    Read from the ledger rather than recomputed from ``coupon.reward``, because
    the reward may have been re-priced since the customer redeemed it. Returns
    the absolute value of the matching REDEEM row.
    """
    from enums import TransactionType as _TT

    row = db.session.execute(
        db.select(db.func.sum(Transaction.points)).where(
            Transaction.coupon_id == coupon.id,
            Transaction.type == _TT.REDEEM.value,
        )
    ).scalar_one()
    return abs(int(row or 0))
