"""PaseoYa catalog: browsing businesses and searching products.

Covers the marketplace read side (reto 5.10 category tree, 5.11 global search)
and the CRUD a business needs to publish its own catalog (reto 5.7).

Search deliberately uses ILIKE rather than PostgreSQL full-text search. The
Spanish FTS index declared in paseo_aranjuez_schema.sql is better for ranking
and typo tolerance, but it is PostgreSQL-only, and the same models have to run
on SQLite for the test suite. ILIKE works identically on both. If search volume
ever justifies it, swapping the predicate is a one-line change.
"""

from decimal import Decimal, InvalidOperation
from typing import Optional

from extensions import db
from models import Business, Product
from services.errors import ForbiddenError, NotFoundError, ValidationError


def _to_decimal(value, field: str) -> Decimal:
    """Parse a user-supplied amount into an exact Decimal.

    Money never goes through float: float cannot represent 0.10 exactly, and a
    catalog full of prices that are off by a centavo is a bug report waiting to
    happen. Invalid input is rejected here rather than silently becoming 0.
    """
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError(f"'{field}' must be a number", details={"field": field})


def get_business_or_404(business_id) -> Business:
    import uuid as _uuid

    try:
        bid = _uuid.UUID(business_id) if isinstance(business_id, str) else business_id
    except (ValueError, TypeError):
        raise NotFoundError("Business not found")
    business = db.session.get(Business, bid)
    if business is None:
        raise NotFoundError("Business not found")
    return business


def get_product(product_id) -> Product:
    import uuid as _uuid

    try:
        pid = _uuid.UUID(product_id) if isinstance(product_id, str) else product_id
    except (ValueError, TypeError):
        raise NotFoundError("Product not found")
    product = db.session.get(Product, pid)
    if product is None:
        raise NotFoundError("Product not found")
    return product


def list_businesses(
    *,
    category: Optional[str] = None,
    search: Optional[str] = None,
    only_active: bool = True,
    limit: int = 50,
    offset: int = 0,
) -> list[Business]:
    """Participating establishments (reto 3.6, "consultar establecimientos")."""
    query = db.select(Business)
    if only_active:
        query = query.where(Business.active.is_(True))
    if category:
        query = query.where(Business.category == category)
    if search:
        pattern = f"%{search.strip()}%"
        query = query.where(
            db.or_(Business.name.ilike(pattern), Business.description.ilike(pattern))
        )
    query = query.order_by(Business.name.asc())
    return list(db.session.execute(query.limit(limit).offset(offset)).scalars())


def list_categories(*, only_active: bool = True) -> list[str]:
    """The category tree for the marketplace home (reto 5.5).

    Derived from the businesses that actually exist, so the menu can never
    advertise a category with nothing in it. Products carry their own category
    too; the union of both is what a shopper can filter by.
    """
    business_query = db.select(Business.category).where(
        Business.category.isnot(None), Business.category != ""
    )
    product_query = db.select(Product.category).where(
        Product.category.isnot(None),
        Product.category != "",
        Product.active.is_(True),
    )
    if only_active:
        business_query = business_query.where(Business.active.is_(True))

    rows = db.session.execute(business_query.union(product_query)).scalars().all()
    return sorted({row for row in rows if row})


def list_products(
    *,
    business_id=None,
    category: Optional[str] = None,
    search: Optional[str] = None,
    include_inactive: bool = False,
    in_stock_only: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> list[Product]:
    """Catalog listing / global search (reto 5.11).

    ``search`` matches name or description across every business, which is what
    lets a shopper type "audifonos bluetooth" and compare shops.
    """
    query = db.select(Product)
    if business_id is not None:
        query = query.where(Product.business_id == business_id)
    if category:
        query = query.where(Product.category == category)
    if not include_inactive:
        query = query.where(Product.active.is_(True))
    if in_stock_only:
        query = query.where(Product.stock > 0)
    if search:
        pattern = f"%{search.strip()}%"
        query = query.where(
            db.or_(Product.name.ilike(pattern), Product.description.ilike(pattern))
        )
    query = query.order_by(Product.name.asc())
    return list(db.session.execute(query.limit(limit).offset(offset)).scalars())


def search_products(term: str, limit: int = 50) -> list[Product]:
    """Convenience wrapper for the global search box."""
    if not term or not term.strip():
        raise ValidationError("The search term cannot be empty")
    return list_products(search=term, limit=limit)


# --- Business-side CRUD -----------------------------------------------------


def _apply_product_fields(product: Product, data: dict, *, partial: bool) -> None:
    """Write validated catalog fields onto a product."""
    if not partial or "name" in data:
        name = (data.get("name") or "").strip()
        if not name:
            raise ValidationError("'name' is required")
        product.name = name

    if not partial or "description" in data:
        product.description = (data.get("description") or "").strip() or None

    if not partial or "category" in data:
        product.category = (data.get("category") or "").strip() or None

    if not partial or "price_bs" in data:
        price = _to_decimal(data.get("price_bs"), "price_bs")
        if price <= 0:
            raise ValidationError("'price_bs' must be greater than 0")
        product.price_bs = price

    if not partial or "stock" in data:
        raw_stock = data.get("stock", 0)
        try:
            stock = int(raw_stock)
        except (TypeError, ValueError):
            raise ValidationError("'stock' must be an integer")
        if stock < 0:
            raise ValidationError("'stock' cannot be negative")
        product.stock = stock

    if "active" in data:
        product.active = bool(data["active"])

    if "image_url" in data:
        product.image_url = (data.get("image_url") or "").strip() or None


def create_product(business: Business, data: dict) -> Product:
    product = Product(business_id=business.id)
    _apply_product_fields(product, data, partial=False)
    db.session.add(product)
    db.session.commit()
    return product


def update_product(product: Product, data: dict) -> Product:
    _apply_product_fields(product, data, partial=True)
    db.session.commit()
    return product


def assert_owns_product(business: Business, product: Product) -> None:
    """A business may only touch its own catalog."""
    if product.business_id != business.id:
        raise ForbiddenError("This product belongs to a different business")
