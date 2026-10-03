"""Businesses (comercios).

One row per participating establishment, owned 1:1 by a SELLER user. The
user_id UNIQUE constraint is what guarantees a person cannot own two shops.

`points_per_bs` is the business's own exchange rate (reto 3.5: "Bs 1 gastado =
1 punto"). It lives here rather than in a global config table because every
establishisement is free to run its own loyalty rate, and the admin panel edits
it per business.

`active` is the on/off switch for the whole business: when it is false the shop
disappears from the marketplace, its catalog stops being purchasable and its
owner cannot credit points -- but its historical orders and ledger rows stay
untouched, because they are financial history.
"""

from sqlalchemy import CheckConstraint
from sqlalchemy.types import Uuid

from extensions import db
from models.base import TimestampMixin, new_uuid


class Business(TimestampMixin, db.Model):
    __tablename__ = "business"
    __table_args__ = (
        CheckConstraint("points_per_bs > 0", name="ck_business_points_per_bs_positive"),
        CheckConstraint("delivery_fee_bs >= 0", name="ck_business_delivery_fee_non_negative"),
    )

    id = db.Column(Uuid(as_uuid=True), primary_key=True, default=new_uuid)

    # ON DELETE CASCADE: deleting the owner account deletes the shop.
    user_id = db.Column(
        Uuid(as_uuid=True),
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    name = db.Column(db.String(160), nullable=False)
    category = db.Column(db.String(80), nullable=True, index=True)
    description = db.Column(db.Text, nullable=True)
    # Free-text location inside the mall ("Plaza nivel 2, local 214"). The
    # marketplace shows it so the customer knows where to walk to on pickup.
    location = db.Column(db.String(200), nullable=True)

    points_per_bs = db.Column(
        db.Numeric(6, 2), nullable=False, default=1, server_default="1"
    )
    active = db.Column(
        db.Boolean, nullable=False, default=True, server_default=db.true()
    )

    # Envíos a domicilio opcionales por negocio
    offers_delivery = db.Column(
        db.Boolean, nullable=False, default=False, server_default=db.false()
    )
    delivery_fee_bs = db.Column(
        db.Numeric(10, 2), nullable=False, default=0, server_default="0"
    )
    delivery_info = db.Column(db.String(255), nullable=True)

    owner = db.relationship("User", back_populates="business", foreign_keys=[user_id])
    products = db.relationship("Product", back_populates="business", cascade="all, delete-orphan")
    orders = db.relationship("Order", back_populates="business", cascade="all, delete-orphan")
    rewards = db.relationship("Reward", back_populates="business", cascade="all, delete-orphan")
    transactions = db.relationship("Transaction", back_populates="business")

    @property
    def owner_user(self):
        return self.owner

    def to_dict(self, include_owner: bool = False) -> dict:
        data = {
            "id": str(self.id),
            "name": self.name,
            "category": self.category,
            "description": self.description,
            "location": self.location,
            "points_per_bs": float(self.points_per_bs),
            "active": self.active,
            "offers_delivery": self.offers_delivery,
            "delivery_fee_bs": float(self.delivery_fee_bs),
            "delivery_info": self.delivery_info,
        }
        if include_owner and self.owner is not None:
            data["owner"] = {
                "id": str(self.owner.id),
                "name": self.owner.name,
                "email": self.owner.email,
            }
        return data

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Business {self.name} active={self.active}>"
