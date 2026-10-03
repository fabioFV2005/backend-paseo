"""Blueprint registration."""

from api.admin_routes import admin_bp
from api.auth_routes import auth_bp
from api.business_routes import business_bp
from api.catalog_routes import catalog_bp
from api.customer_routes import customer_bp


def register_blueprints(app) -> None:
    """Attach every blueprint to the app.

    URL prefixes are fixed here so a route's full path is visible in one place
    instead of being spread across the route modules.
    """
    app.register_blueprint(auth_bp)                      # /login, /auth/*, /api/me
    app.register_blueprint(customer_bp, url_prefix="/api")
    app.register_blueprint(catalog_bp, url_prefix="/api")
    app.register_blueprint(business_bp, url_prefix="/api")
    app.register_blueprint(admin_bp, url_prefix="/api/admin")


__all__ = [
    "register_blueprints",
    "auth_bp",
    "customer_bp",
    "catalog_bp",
    "business_bp",
    "admin_bp",
]
