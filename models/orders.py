"""Orders and order items (PaseoYa).

PaseoYa is pickup-only: there is no delivery address and no courier, because
the whole point of the challenge is to use online ordering to pull people into
the physical mall (reto 5.6, "RETIRO PRESENCIAL OBLIGATORIO").

An order therefore always belongs to exactly ONE business. A cart spanning two
shops is rejected at checkout rather than silently split, because splitting it
would mean two pickups and two payment confirmations in a system built to make
one trip to the mall.

Order totals are always recomputed on the server from `products.price_bs`. The
client sends only product ids and quantities; it never sends a price. That is
the single most important integrity rule in this module.

`order_items` carries its own copy of the product name and unit price. Orders
are receipts: they must still make sense years later, after the shop renames or
deletes the product. `product_id` is therefore ON DELETE SET NULL and the
snapshot columns are the real record.
"""

from sqlalchemy import CheckConstraint, Index
from sqlalchemy.types import Uuid

from enums import OrderStatus, PaymentMethod
from extensions import db
from models.base import TimestampMixin, new_pickup_code, new_uuid


class Order(TimestampMixin, db.Model):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint(
            "status IN ('received', 'confirmed', 'preparing', 'ready_for_pickup', "
            "'on_the_way', 'customer_arrived', 'delivered', 'cancelled')",
            name="ck_orders_status",
        ),
        CheckConstraint(
            "delivery_type IN ('pickup', 'delivery')",
            name="ck_orders_delivery_type",
        ),
        CheckConstraint("delivery_fee_bs >= 0", name="ck_orders_delivery_fee_non_negative"),
        CheckConstraint("subtotal_bs >= 0", name="ck_orders_subtotal_non_negative"),
        CheckConstraint("discount_bs >= 0", name="ck_orders_discount_non_negative"),
        CheckConstraint("total_bs >= 0", name="ck_orders_total_non_negative"),
        # The discount can never exceed the subtotal it discounts.
        CheckConstraint("discount_bs <= subtotal_bs", name="ck_orders_discount_le_subtotal"),
        Index("ix_orders_customer_created", "customer_id", "created_at"),
        Index("ix_orders_business_status", "business_id", "status"),
    )

    # Legal status transitions (reto 5.8 + optional delivery).
    TRANSITIONS: dict[str, set[str]] = {
        OrderStatus.RECEIVED.value: {
            OrderStatus.CONFIRMED.value,
            OrderStatus.CANCELLED.value,
        },
        OrderStatus.CONFIRMED.value: {
            OrderStatus.PREPARING.value,
            OrderStatus.CANCELLED.value,
        },
        OrderStatus.PREPARING.value: {
            OrderStatus.READY_FOR_PICKUP.value,
            OrderStatus.ON_THE_WAY.value,
            OrderStatus.CANCELLED.value,
        },
        OrderStatus.READY_FOR_PICKUP.value: {
            OrderStatus.CUSTOMER_ARRIVED.value,
            OrderStatus.DELIVERED.value,
            OrderStatus.CANCELLED.value,
        },
        OrderStatus.ON_THE_WAY.value: {
            OrderStatus.DELIVERED.value,
            OrderStatus.CANCELLED.value,
        },
        OrderStatus.CUSTOMER_ARRIVED.value: {
            OrderStatus.DELIVERED.value,
            OrderStatus.CANCELLED.value,
        },
        OrderStatus.DELIVERED.value: set(),
        OrderStatus.CANCELLED.value: set(),
    }

    id = db.Column(Uuid(as_uuid=True), primary_key=True, default=new_uuid)

    # Human-readable reference the customer reads out at the counter.
    order_number = db.Column(db.String(24), nullable=False, unique=True, index=True)

    customer_id = db.Column(
        Uuid(as_uuid=True),
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    business_id = db.Column(
        Uuid(as_uuid=True),
        db.ForeignKey("business.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    status = db.Column(
        db.String(24),
        nullable=False,
        default=OrderStatus.RECEIVED.value,
        server_default=OrderStatus.RECEIVED.value,
        index=True,
    )

    # All server-computed from products.price_bs. See module docstring.
    subtotal_bs = db.Column(db.Numeric(10, 2), nullable=False, default=0, server_default="0")
    discount_bs = db.Column(db.Numeric(10, 2), nullable=False, default=0, server_default="0")
    total_bs = db.Column(db.Numeric(10, 2), nullable=False, default=0, server_default="0")

    # A Paseo Points coupon applied to this order: the bridge between the two
    # retos. SET NULL so cancelling a coupon never erases the order.
    coupon_id = db.Column(
        Uuid(as_uuid=True),
        db.ForeignKey("coupons.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Cached preview of what this order will credit. The ledger row created on
    # delivery is the real record; this is what the UI shows before that.
    points_earned = db.Column(db.Integer, nullable=False, default=0, server_default="0")

    payment_method = db.Column(db.String(16), nullable=True)
    # 6 digits, shown as a QR by the frontend (reto 5.9).
    pickup_code = db.Column(db.String(8), nullable=False, default=new_pickup_code)
    note = db.Column(db.String(300), nullable=True)

    # Fulfillment / Delivery
    delivery_type = db.Column(
        db.String(16), nullable=False, default="pickup", server_default="pickup"
    )
    delivery_address = db.Column(db.String(255), nullable=True)
    delivery_phone = db.Column(db.String(32), nullable=True)
    delivery_instructions = db.Column(db.String(300), nullable=True)
    delivery_fee_bs = db.Column(
        db.Numeric(10, 2), nullable=False, default=0, server_default="0"
    )

    confirmed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    ready_at = db.Column(db.DateTime(timezone=True), nullable=True)
    arrived_at = db.Column(db.DateTime(timezone=True), nullable=True)
    delivered_at = db.Column(db.DateTime(timezone=True), nullable=True)

    customer = db.relationship("User", back_populates="orders")
    business = db.relationship("Business", back_populates="orders")
    items = db.relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")
    coupon = db.relationship("Coupon", back_populates="orders")
    transactions = db.relationship("Transaction", back_populates="order")

    @property
    def status_enum(self) -> OrderStatus:
        return OrderStatus.parse(self.status) or OrderStatus.RECEIVED

    @property
    def is_open(self) -> bool:
        return self.status_enum.value in OrderStatus.open_states()

    @property
    def item_count(self) -> int:
        return sum(item.quantity for item in self.items)

    def can_transition_to(self, new_status) -> bool:
        """Whether the order is allowed to move to ``new_status``."""
        parsed = OrderStatus.parse(new_status) if isinstance(new_status, str) else new_status
        if parsed is None:
            return False
        return parsed.value in self.TRANSITIONS.get(self.status_enum.value, set())

    def allowed_transitions(self) -> list[str]:
        return sorted(self.TRANSITIONS.get(self.status_enum.value, set()))

    def to_dict(self, include_items: bool = True) -> dict:
        data = {
            "id": str(self.id),
            "order_number": self.order_number,
            "status": self.status_enum.value,
            "allowed_transitions": self.allowed_transitions(),
            "subtotal_bs": float(self.subtotal_bs),
            "discount_bs": float(self.discount_bs),
            "delivery_fee_bs": float(self.delivery_fee_bs),
            "total_bs": float(self.total_bs),
            "points_earned": self.points_earned,
            "pickup_code": self.pickup_code,
            "payment_method": self.payment_method,
            "delivery_type": self.delivery_type,
            "delivery_address": self.delivery_address,
            "delivery_phone": self.delivery_phone,
            "delivery_instructions": self.delivery_instructions,
            "note": self.note,
            "customer_id": str(self.customer_id),
            "business_id": str(self.business_id),
            "business_name": self.business.name if self.business else None,
            "coupon_id": str(self.coupon_id) if self.coupon_id else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "delivered_at": self.delivered_at.isoformat() if self.delivered_at else None,
        }
        if include_items:
            data["items"] = [item.to_dict() for item in self.items]
        return data

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Order {self.order_number} {self.status} total={self.total_bs}>"


class OrderItem(db.Model):
    __tablename__ = "order_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_order_items_quantity_positive"),
        CheckConstraint("unit_price_bs > 0", name="ck_order_items_price_positive"),
        CheckConstraint("subtotal_bs >= 0", name="ck_order_items_subtotal_non_negative"),
    )

    id = db.Column(Uuid(as_uuid=True), primary_key=True, default=new_uuid)

    order_id = db.Column(
        Uuid(as_uuid=True),
        db.ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # SET NULL: deleting a product must not erase it from a past order. The
    # snapshot columns below are what the receipt is actually made of.
    product_id = db.Column(
        Uuid(as_uuid=True),
        db.ForeignKey("products.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # --- Snapshot taken at checkout time ---
    product_name = db.Column(db.String(180), nullable=False)
    unit_price_bs = db.Column(db.Numeric(10, 2), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    subtotal_bs = db.Column(db.Numeric(10, 2), nullable=False)

    order = db.relationship("Order", back_populates="items")
    product = db.relationship("Product", back_populates="order_items")

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "product_id": str(self.product_id) if self.product_id else None,
            "product_name": self.product_name,
            "unit_price_bs": float(self.unit_price_bs),
            "quantity": self.quantity,
            "subtotal_bs": float(self.subtotal_bs),
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<OrderItem {self.quantity}x {self.product_name}>"
