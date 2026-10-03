"""Public marketplace browsing: businesses, categories, products and search.

These are the only endpoints that work without a session, because a shopper
browses the Paseo before deciding to sign in (reto 5.5).
"""

from flask import Blueprint, jsonify, request

from services.catalog import (
    get_product,
    list_businesses,
    list_categories,
    list_products,
    search_products,
)
from services.errors import ValidationError

catalog_bp = Blueprint("catalog", __name__)


def _pagination_args(default_limit: int = 50) -> tuple[int, int]:
    try:
        limit = int(request.args.get("limit", default_limit))
        offset = int(request.args.get("offset", 0))
    except (TypeError, ValueError):
        raise ValidationError("'limit' and 'offset' must be integers")
    return max(1, min(limit, 200)), max(0, offset)


@catalog_bp.get("/businesses")
def businesses():
    """Participating establishments, filterable by category or name.

    Reto 3.6: the customer can "consultar establecimientos participantes".
    """
    limit, offset = _pagination_args()
    rows = list_businesses(
        category=request.args.get("category"),
        search=request.args.get("q"),
        limit=limit,
        offset=offset,
    )
    return jsonify(
        {"items": [row.to_dict() for row in rows], "limit": limit, "offset": offset}
    )


@catalog_bp.get("/categories")
def categories():
    """The category tree for the marketplace home screen (reto 5.5)."""
    return jsonify({"items": list_categories()})


@catalog_bp.get("/products")
def products():
    """Catalog listing and the global search box (reto 5.11).

    Query parameters:
        business_id  -- one shop's catalog
        category     -- filter by category
        q            -- free text across every shop
        in_stock     -- "true" to hide sold-out items
    """
    limit, offset = _pagination_args()
    term = request.args.get("q")
    rows = (
        search_products(term, limit=limit)
        if term
        else list_products(
            business_id=request.args.get("business_id") or None,
            category=request.args.get("category"),
            in_stock_only=request.args.get("in_stock", "").lower() in {"1", "true", "yes"},
            limit=limit,
            offset=offset,
        )
    )
    return jsonify(
        {"items": [row.to_dict(include_business=True) for row in rows], "limit": limit}
    )


@catalog_bp.get("/products/<product_id>")
def product_detail(product_id):
    """Product detail, with the shop's name and location for the pickup trip."""
    return jsonify(get_product(product_id).to_dict(include_business=True))
