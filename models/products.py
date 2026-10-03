"""Products (PaseoYa catalog).

Each row is one item a single business sells. Every product belongs to exactly
one shop: PaseoYa is a marketplace of independent catalogs, not a shared
inventory (reto 5.10, "Cada negocio tendrá su propio catálogo").

Prices are stored in bolivars as NUMERIC, never floats. NUMERIC is exact, so
totals never drift by a centavo when an order is summed.

`stock` is enforced at checkout inside the order transaction, so two customers
racing for the last unit cannot both succeed.
"""

from sqlalchemy import CheckConstraint, Index
from sqlalchemy.types import Uuid

from extensions import db
from models.base import TimestampMixin, new_uuid


class Product(TimestampMixin, db.Model):
    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint("price_bs > 0", name="ck_products_price_positive"),
        CheckConstraint("stock >= 0", name="ck_products_stock_non_negative"),
        # Supports the global search of reto 5.11 ("Audífonos Bluetooth" across
        # every shop) and the per-shop catalog listing.
        Index("ix_products_business_active", "business_id", "active"),
    )

    id = db.Column(Uuid(as_uuid=True), primary_key=True, default=new_uuid)

    # ON DELETE CASCADE: a shop that goes away takes its catalog with it.
    business_id = db.Column(
        Uuid(as_uuid=True),
        db.ForeignKey("business.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name = db.Column(db.String(180), nullable=False)
    description = db.Column(db.Text, nullable=True)
    # Denormalized per product so the marketplace can filter by category across
    # all shops without joining through business.
    category = db.Column(db.String(80), nullable=True, index=True)

    price_bs = db.Column(db.Numeric(10, 2), nullable=False)
    stock = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    active = db.Column(
        db.Boolean, nullable=False, default=True, server_default=db.true()
    )
    # Optional image URL. Kept as a plain string so the backend never has to
    # handle file uploads.
    image_url = db.Column(db.String(500), nullable=True)

    business = db.relationship("Business", back_populates="products")
    order_items = db.relationship("OrderItem", back_populates="product")

    @property
    def is_available(self) -> bool:
        return self.active and self.stock > 0

    def to_dict(self, include_business: bool = False) -> dict:
        data = {
            "id": str(self.id),
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "price_bs": float(self.price_bs),
            "stock": self.stock,
            "active": self.active,
            "image_url": self.image_url,
            "business_id": str(self.business_id),
        }
        if include_business and self.business is not None:
            data["business"] = {
                "id": str(self.business.id),
                "name": self.business.name,
                "category": self.business.category,
                "location": self.business.location,
            }
        return data

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Product {self.name} price={self.price_bs} stock={self.stock}>"
