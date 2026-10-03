"""Smoke test against the real PostgreSQL database.

The pytest suite runs on SQLite because that is fast and self-contained, which
leaves a real gap: NUMERIC precision, `SELECT ... FOR UPDATE`, native UUID and
the CHECK constraints only exist on PostgreSQL. This script closes that gap by
driving the actual API against the actual database.

It writes rows with a unique marker and removes them in a `finally`, so a run
that dies halfway does not leave the demo database littered with half-smoke
fixtures. That matters more than it sounds: the first version of this script
crashed mid-run and left a SELLER, a shop, a product and three EARN rows behind,
which then showed up in the seeded demo.

Refuses to run against a database whose name does not look like a development
one.

Run it with:  python tools/smoke_postgres.py
"""

import os
import sys
import uuid
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DATABASE_URL  # noqa: E402

if not DATABASE_URL.startswith("postgresql"):
    print(f"Expected PostgreSQL, got: {DATABASE_URL}")
    print("Set DATABASE_URL in .env to a development database first.")
    sys.exit(1)

if "paseo" not in DATABASE_URL:
    print(f"Refusing to touch a database that is not obviously a dev one: {DATABASE_URL}")
    sys.exit(1)

from app import create_app  # noqa: E402
from extensions import db  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label} {detail}")
        failures.append(label)


def run_checks(app, client, marker: str) -> None:
    from datetime import datetime, timedelta, timezone

    from auth import create_token
    from enums import TransactionType
    from models import (
        Business,
        Product,
        Transaction,
        User,
        get_or_create_google_user,
    )
    from roles import UserRole

    print("\n== PostgreSQL smoke test ==")

    # --- Fixtures ------------------------------------------------------------
    # The shop's rate is 0.33 points per bolivar, chosen because it does not
    # divide evenly: 33.33 x 0.33 = 10.9989, so any rounding shows up.
    with app.app_context():
        user = get_or_create_google_user(
            google_sub=f"smoke-{marker}",
            email=f"smoke-{marker}@paseo.test",
            name="Smoke Shop Owner",
        )
        check(
            "UUID comes back as a real UUID (not a string)",
            isinstance(user.id, uuid.UUID),
            f"got {type(user.id).__name__}",
        )

        user.role = UserRole.SELLER.value
        db.session.flush()
        shop = Business(
            user_id=user.id,
            name=f"Smoke Shop {marker}",
            category="Comida",
            points_per_bs=Decimal("0.33"),
            active=True,
        )
        db.session.add(shop)
        db.session.flush()
        product = Product(
            business_id=shop.id,
            name=f"Smoke Product {marker}",
            price_bs=Decimal("33.33"),
            stock=10,
            active=True,
        )
        db.session.add(product)
        db.session.commit()

        product_id = str(product.id)
        shop_id = shop.id
        shop_headers = {"Authorization": f"Bearer {create_token(user)}"}

        customer = get_or_create_google_user(
            google_sub=f"smoke-cust-{marker}",
            email=f"smoke-cust-{marker}@paseo.test",
            name="Smoke Customer",
        )
        qr_code = customer.qr_code
        customer_headers = {"Authorization": f"Bearer {create_token(customer)}"}
        customer_id = customer.id

        # Turn the duplicate window off for the section that measures rounding,
        # so three back-to-back scans are three real purchases.
        app.config["SCAN_DUPLICATE_WINDOW_SECONDS"] = 0

    def scan():
        return client.post(
            "/api/business/scan",
            json={"qr_code": qr_code, "amount_bs": "33.33"},
            headers=shop_headers,
        )

    # --- Public catalog ------------------------------------------------------
    response = client.get("/api/products")
    check("GET /api/products is public", response.status_code == 200)
    check(
        "a product created above is visible",
        any(item["id"] == product_id for item in response.get_json()["items"]),
    )

    response = client.get("/api/rewards")
    check("GET /api/rewards is public", response.status_code == 200)
    check(
        "anonymous rewards catalog reports points as null",
        response.get_json()["points"] is None,
    )

    # --- Auth ----------------------------------------------------------------
    response = client.get("/api/me", headers=customer_headers)
    check("GET /api/me with a token", response.status_code == 200)
    check("balance starts at 0", response.get_json()["points"] == 0)

    response = client.get("/api/me")
    check("GET /api/me without a token is 401", response.status_code == 401)

    # --- NUMERIC precision, floored per purchase -----------------------------
    # Each scan of Bs 33.33 at 0.33 points/Bs earns floor(10.9989) = 10.
    #
    # Floored PER PURCHASE, not on the sum: three separate sales earn 30, not
    # floor(3 x 10.9989) = 32. Rounding up would let a customer beat the business
    # by splitting one purchase into several tiny ones, and it would make the
    # balance disagree with the receipt the shop printed.
    for _ in range(3):
        response = scan()
    check("scan accepted", response.status_code == 201, response.get_json())

    with app.app_context():
        from models import find_user_by_email

        row = find_user_by_email(f"smoke-cust-{marker}@paseo.test")
        check(
            "points floor per purchase: 3 x floor(33.33*0.33) = 30, not 32",
            row.points_balance == 30,
            f"got {row.points_balance}",
        )
        check("level stayed BRONZE under 300 points", row.level == "BRONZE")
        amounts = db.session.execute(
            db.select(Transaction.amount_bs).where(
                Transaction.customer_id == customer_id,
                Transaction.type == TransactionType.EARN.value,
            )
        ).scalars().all()
        check(
            "amount stored exactly as NUMERIC (33.33, no float drift)",
            amounts
            and all(abs(Decimal(str(a)) - Decimal("33.33")) < Decimal("0.0001") for a in amounts),
            f"amounts={amounts}",
        )

    # --- Duplicate window ----------------------------------------------------
    app.config["SCAN_DUPLICATE_WINDOW_SECONDS"] = 60

    blocked = scan()
    check(
        "a repeat scan of the same pair inside the window is refused",
        blocked.status_code == 409,
        blocked.get_json(),
    )
    check(
        "the refusal tells the shop how long to wait",
        blocked.headers.get("Retry-After") == "60",
        dict(blocked.headers),
    )
    with app.app_context():
        from models import find_user_by_email

        row = find_user_by_email(f"smoke-cust-{marker}@paseo.test")
        check(
            "the refused scan credited nothing",
            row.points_balance == 30,
            f"got {row.points_balance}",
        )

        # Age every EARN this shop made for this customer past the window, not
        # just the newest: the guard looks for the most recent one inside the
        # window, so ageing one still leaves the others to trip it.
        aged = db.session.execute(
            db.select(Transaction).where(
                Transaction.customer_id == customer_id,
                Transaction.business_id == shop_id,
                Transaction.type == TransactionType.EARN.value,
            )
        ).scalars().all()
        aged_at = datetime.now(timezone.utc) - timedelta(seconds=61)
        for row in aged:
            row.created_at = aged_at
        db.session.commit()

    allowed = scan()
    check(
        "the same scan is accepted once the window has passed",
        allowed.status_code == 201,
        allowed.get_json(),
    )
    with app.app_context():
        from models import find_user_by_email

        row = find_user_by_email(f"smoke-cust-{marker}@paseo.test")
        check("balance is 40 after the accepted repeat", row.points_balance == 40, f"got {row.points_balance}")

    # --- CHECK constraints actually fire -------------------------------------
    with app.app_context():
        shop_row = db.session.execute(
            db.select(Business).where(Business.name == f"Smoke Shop {marker}")
        ).scalars().one()
        try:
            db.session.add(
                Product(
                    business_id=shop_row.id,
                    name="Negative stock",
                    price_bs=Decimal("1.00"),
                    stock=-1,
                    active=True,
                )
            )
            db.session.commit()
            check("CHECK constraint blocks negative stock", False, "insert succeeded")
        except Exception:
            db.session.rollback()
            check("CHECK constraint blocks negative stock", True)

    # --- Row-level locking is accepted by the dialect ------------------------
    with app.app_context():
        locked = db.session.execute(
            db.select(User).where(User.id == customer_id).with_for_update()
        ).scalar_one()
        check("SELECT ... FOR UPDATE works on PostgreSQL", locked is not None)
        db.session.rollback()

    # --- Order lifecycle -----------------------------------------------------
    response = client.post(
        "/api/orders",
        json={"items": [{"product_id": product_id, "quantity": 2}]},
        headers=customer_headers,
    )
    check("order created", response.status_code == 201, response.get_json())
    order = response.get_json()["order"]
    check("total is 66.66", abs(order["total_bs"] - 66.66) < 0.01, order["total_bs"])
    check("order starts in received", order["status"] == "received")
    check(
        "pickup code issued",
        isinstance(order["pickup_code"], str) and len(order["pickup_code"]) == 6,
        order["pickup_code"],
    )
    check(
        "points_earned at checkout is a preview, not a credit",
        order["points_earned"] == 21,
        order["points_earned"],
    )
    with app.app_context():
        from models import find_user_by_email

        row = find_user_by_email(f"smoke-cust-{marker}@paseo.test")
        check(
            "the balance did not move at checkout",
            row.points_balance == 40,
            f"got {row.points_balance}",
        )

    order_id = order["id"]
    response = client.post(
        f"/api/business/orders/{order_id}/pickup",
        json={"code": order["pickup_code"]},
        headers=shop_headers,
    )
    check("cannot deliver a received order", response.status_code == 422)

    for status in ("confirmed", "preparing", "ready_for_pickup"):
        response = client.patch(
            f"/api/business/orders/{order_id}/status",
            json={"status": status},
            headers=shop_headers,
        )
        if response.status_code != 200:
            break
    check("order advanced to ready_for_pickup", response.status_code == 200)

    response = client.post(
        f"/api/business/orders/{order_id}/pickup",
        json={"code": order["pickup_code"]},
        headers=shop_headers,
    )
    check("pickup delivered the order", response.status_code == 200, response.get_json())

    with app.app_context():
        from models import find_user_by_email

        row = find_user_by_email(f"smoke-cust-{marker}@paseo.test")
        # 40 from the scans, plus floor(66.66 x 0.33) = floor(21.9978) = 21.
        check(
            "delivery credited 21, reaching 61 -- and was not blocked by the window",
            row.points_balance == 61,
            f"got {row.points_balance}",
        )


def cleanup(app, marker: str) -> None:
    """Remove everything this run created, in FK order, whatever went wrong.

    Matching is case-insensitive because emails are stored lowercased, so a
    marker containing uppercase letters would otherwise match nothing and leave
    the fixtures behind -- which is exactly the failure this function exists to
    prevent.
    """
    from models import Business, Coupon, Order, OrderItem, Product, Reward, Transaction, User

    with app.app_context():
        users = db.session.execute(
            db.select(User.id).where(User.email.ilike(f"%{marker}%"))
        ).scalars().all()
        if not users:
            print("\n(nothing to clean up)")
            return

        orders = db.session.execute(
            db.select(Order.id).where(Order.customer_id.in_(users))
        ).scalars().all()
        if orders:
            # A coupon points at its order from the order side (Order.coupon_id),
            # so it has to go before the orders it is referenced by.
            coupons = db.session.execute(
                db.select(Coupon.id).where(Coupon.customer_id.in_(users))
            ).scalars().all()
            if coupons:
                db.session.execute(
                    db.delete(Transaction).where(Transaction.coupon_id.in_(coupons))
                )
                db.session.execute(db.delete(Coupon).where(Coupon.id.in_(coupons)))
            db.session.execute(db.delete(Transaction).where(Transaction.order_id.in_(orders)))
            db.session.execute(db.delete(OrderItem).where(OrderItem.order_id.in_(orders)))
            db.session.execute(db.delete(Order).where(Order.id.in_(orders)))

        shops = db.session.execute(
            db.select(Business.id).where(Business.name.ilike(f"%{marker}%"))
        ).scalars().all()
        db.session.execute(
            db.delete(Product).where(Product.business_id.in_(shops))
        )
        db.session.execute(
            db.delete(Reward).where(Reward.business_id.in_(shops))
        )
        db.session.execute(db.delete(Business).where(Business.id.in_(shops)))
        db.session.execute(
            db.delete(Transaction).where(Transaction.business_id.in_(shops))
        )
        db.session.execute(db.delete(Transaction).where(Transaction.customer_id.in_(users)))
        db.session.execute(db.delete(User).where(User.id.in_(users)))
        db.session.commit()

        leftover = db.session.execute(
            db.select(db.func.count(User.id)).where(User.email.ilike(f"%{marker}%"))
        ).scalar_one()
        check("every row this run created was removed", leftover == 0, f"{leftover} left")


def main() -> int:
    app = create_app()
    # Generated before anything can fail, so the finally block always knows what
    # to look for.
    marker = uuid.uuid4().hex[:8]
    try:
        with app.test_client() as client:
            run_checks(app, client, marker)
    finally:
        cleanup(app, marker)

    print()
    if failures:
        print(f"{len(failures)} check(s) FAILED: {failures}")
        return 1
    print("All PostgreSQL checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())