"""The points ledger (Paseo Points).

One row per balance change. Rows are NEVER updated and NEVER deleted: a
customer's balance is the SUM of their `points` column, so history is the
source of truth and an append-only table is what makes that safe. This is why
this model has `created_at` but deliberately no `updated_at` -- if the column
existed at all it would be a lie.

A single ledger serves both hackathon retos:

- `order_id IS NULL` -> the points came from a purchase the business recorded
  by scanning the customer's QR (Paseo Points flow).
- `order_id` set     -> the points came from delivering a PaseoYa order, which
  is the integration between the two retos.

`points` is signed: negative for spend, positive for earn. `amount_bs` is only
meaningful on an EARN row. Everything else about the movement goes in `note`,
which is free text for the human reading the admin audit view.
"""

from sqlalchemy import CheckConstraint, Index, desc
from sqlalchemy.types import Uuid

from enums import TransactionType
from extensions import db
from models.base import new_uuid, utcnow


class Transaction(db.Model):
    __tablename__ = "transactions"
    __table_args__ = (
        CheckConstraint("points <> 0", name="ck_transactions_points_nonzero"),
        CheckConstraint(
            "amount_bs IS NULL OR amount_bs > 0",
            name="ck_transactions_amount_positive",
        ),
        CheckConstraint(
            "type IN ('earn', 'redeem', 'refund', 'adjust', 'bonus')",
            name="ck_transactions_type",
        ),
        # "Mis movimientos" screens are always the latest N for one customer or
        # one business, so the index leads with the FK and sorts by time DESC.
        Index("ix_transactions_customer_created", "customer_id", desc("created_at")),
        Index("ix_transactions_business_created", "business_id", desc("created_at")),
    )

    id = db.Column(Uuid(as_uuid=True), primary_key=True, default=new_uuid)

    customer_id = db.Column(
        Uuid(as_uuid=True),
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # NULL on admin adjustments and on rewards sponsored by Paseo itself.
    business_id = db.Column(
        Uuid(as_uuid=True),
        db.ForeignKey("business.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Links the movement back to the PaseoYa order that caused it.
    order_id = db.Column(
        Uuid(as_uuid=True),
        db.ForeignKey("orders.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    type = db.Column(db.String(16), nullable=False, default=TransactionType.EARN.value)

    # Only set on EARN rows: what the customer actually spent in bolivars.
    amount_bs = db.Column(db.Numeric(10, 2), nullable=True)
    # Signed point amount. The CHECK above forbids a meaningless 0 row.
    points = db.Column(db.Integer, nullable=False)

    coupon_id = db.Column(
        Uuid(as_uuid=True),
        db.ForeignKey("coupons.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    note = db.Column(db.String(300), nullable=True)

    # No updated_at: this table is append-only.
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        server_default=db.func.now(),
    )

    customer = db.relationship("User", back_populates="transactions")
    business = db.relationship("Business", back_populates="transactions")
    order = db.relationship("Order", back_populates="transactions")
    coupon = db.relationship("Coupon", back_populates="transactions")

    @property
    def type_enum(self) -> TransactionType:
        return TransactionType.parse(self.type) or TransactionType.EARN

    def to_dict(self, include_names: bool = True) -> dict:
        data = {
            "id": str(self.id),
            "type": self.type_enum.value,
            "points": self.points,
            "amount_bs": float(self.amount_bs) if self.amount_bs is not None else None,
            "note": self.note,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "business_id": str(self.business_id) if self.business_id else None,
            "order_id": str(self.order_id) if self.order_id else None,
            "coupon_id": str(self.coupon_id) if self.coupon_id else None,
        }
        if include_names and self.business is not None:
            data["business_name"] = self.business.name
        return data

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Transaction {self.type} {self.points:+d}pts customer={self.customer_id}>"
