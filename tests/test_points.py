"""End-to-end tests for the Paseo Points ledger (reto 3.7).

These go through HTTP because the interesting failures are not in the
arithmetic -- they are in what happens when two requests touch the same
customer, when a rule is checked in the wrong order, or when a service forgets
to commit. Testing the service functions directly would hide all three.

The invariant under test throughout: **the balance is always the sum of the
ledger, and every balance change is exactly one appended row.**
"""

from decimal import Decimal

import pytest

from extensions import db
from models import Business, Transaction, User
from roles import UserRole
from services.points import get_balance


@pytest.fixture()
def business(app):
    """An active shop at 1 point per bolivar."""
    user = User(
        email="shop@paseo.test",
        name="Shop",
        google_sub="sub-shop",
        role=UserRole.SELLER.value,
    )
    db.session.add(user)
    db.session.flush()
    shop = Business(
        user_id=user.id,
        name="Cafe Aranjuez",
        category="Comida",
        points_per_bs=Decimal("1.00"),
        active=True,
    )
    db.session.add(shop)
    db.session.commit()
    return shop


def token_for(user):
    from auth import create_token

    return {"Authorization": f"Bearer {create_token(user)}"}


# --- Accrual via QR scan ------------------------------------------------------


def test_scan_credits_points_at_the_business_rate(client, app, make_user, business):
    customer = make_user(email="c1@paseo.test")
    response = client.post(
        "/api/business/scan",
        json={"qr_code": customer.qr_code, "amount_bs": 150.50},
        headers=token_for(business.owner),
    )
    assert response.status_code == 201, response.get_json()
    body = response.get_json()
    assert body["points_credited"] == 150  # floor(150.50 * 1.00)

    db.session.refresh(customer)
    assert customer.points_balance == 150
    assert customer.level == "BRONZE"  # 150 < 300


def test_points_round_down_not_up(client, app, make_user, business):
    # Rounding up would make the business pay for points nobody earned.
    customer = make_user(email="c2@paseo.test")
    body = client.post(
        "/api/business/scan",
        json={"qr_code": customer.qr_code, "amount_bs": 99.99},
        headers=token_for(business.owner),
    ).get_json()
    assert body["points_credited"] == 99


def test_purchase_too_small_to_be_worth_a_point_is_not_an_error(client, app, make_user, business):
    # Bs 0.50 at 1 point/bolivar floors to 0. The sale still stands.
    customer = make_user(email="c3@paseo.test")
    response = client.post(
        "/api/business/scan",
        json={"qr_code": customer.qr_code, "amount_bs": "0.50"},
        headers=token_for(business.owner),
    )
    # 200, not 201: no ledger row was created, so there is no new resource to
    # point at. 201 is reserved for a scan that actually credited something.
    assert response.status_code == 200
    assert response.get_json()["points_credited"] == 0

    db.session.refresh(customer)
    assert customer.points_balance == 0
    # ...and crucially, no zero-value ledger row was written.
    assert db.session.execute(db.select(db.func.count(Transaction.id))).scalar_one() == 0


def test_unknown_qr_code_is_a_client_error(client, app, business):
    response = client.post(
        "/api/business/scan",
        json={"qr_code": "does-not-exist", "amount_bs": 50},
        headers=token_for(business.owner),
    )
    assert response.status_code == 404


def test_negative_amount_is_rejected(client, app, make_user, business):
    customer = make_user(email="c4@paseo.test")
    response = client.post(
        "/api/business/scan",
        json={"qr_code": customer.qr_code, "amount_bs": -100},
        headers=token_for(business.owner),
    )
    assert response.status_code == 400


def test_inactive_business_cannot_credit(client, app, make_user, business):
    business.active = False
    db.session.commit()
    customer = make_user(email="c5@paseo.test")
    response = client.post(
        "/api/business/scan",
        json={"qr_code": customer.qr_code, "amount_bs": 100},
        headers=token_for(business.owner),
    )
    assert response.status_code == 403


def test_crediting_business_rate_is_its_own_and_cannot_be_inflated(client, app, make_user):
    # A sale really happened at the rival, so crediting is legitimate -- but the
    # rate is the crediting business's own and the ledger row is attributed to
    # it. Neither the caller nor the customer can pick a friendlier rate.
    rival_user = User(
        email="rival@paseo.test", name="Rival", google_sub="sub-rival", role=UserRole.SELLER.value
    )
    db.session.add(rival_user)
    db.session.flush()
    rival = Business(
        user_id=rival_user.id,
        name="Rival Cafe",
        points_per_bs=Decimal("10.00"),  # a tempting rate
        active=True,
    )
    db.session.add(rival)
    db.session.commit()

    customer = make_user(email="c6@paseo.test")
    response = client.post(
        "/api/business/scan",
        json={"qr_code": customer.qr_code, "amount_bs": 100, "points_per_bs": 1000},
        headers=token_for(rival_user),
    )
    assert response.status_code == 201, response.get_json()
    assert response.get_json()["points_credited"] == 1000  # 100 * the rival's own 10.0

    db.session.refresh(customer)
    assert customer.points_balance == 1000
    # The row is attributed to the rival, so its own statement shows what it caused.
    entry = db.session.execute(db.select(Transaction)).scalars().one()
    assert str(entry.business_id) == str(rival.id)


def test_a_business_cannot_credit_points_to_itself(client, app, business):
    # Otherwise a shop could mint its own loyalty balance by scanning its own
    # QR and then redeem rewards against it.
    response = client.post(
        "/api/business/scan",
        json={"qr_code": business.owner.qr_code, "amount_bs": 1000},
        headers=token_for(business.owner),
    )
    assert response.status_code == 422


# --- Level progression --------------------------------------------------------


def test_level_promotes_as_the_balance_grows(client, app, make_user, business):
    # Two scans a second apart, which the anti-duplicate window would normally
    # refuse. This test is about level thresholds, not about the window, so it
    # switches the window off rather than faking the clock on every scan.
    app.config["SCAN_DUPLICATE_WINDOW_SECONDS"] = 0
    customer = make_user(email="c7@paseo.test")
    scan = lambda amount: client.post(  # noqa: E731
        "/api/business/scan",
        json={"qr_code": customer.qr_code, "amount_bs": amount},
        headers=token_for(business.owner),
    )
    assert scan(299).status_code == 201
    db.session.refresh(customer)
    assert customer.level == "BRONZE"

    scan(1)  # 300 exactly
    db.session.refresh(customer)
    assert customer.points_balance == 300
    assert customer.level == "SILVER"


def test_customer_sees_their_own_ledger(client, app, make_user, business):
    # Two scans back to back: the window is off here for the same reason as in
    # the level test -- this one is about ledger aggregation.
    app.config["SCAN_DUPLICATE_WINDOW_SECONDS"] = 0
    customer = make_user(email="c8@paseo.test")
    for amount in (100, 250):
        client.post(
            "/api/business/scan",
            json={"qr_code": customer.qr_code, "amount_bs": amount},
            headers=token_for(business.owner),
        )

    response = client.get("/api/me/transactions", headers=token_for(customer))
    assert response.status_code == 200
    items = response.get_json()["items"]
    assert len(items) == 2
    assert sum(item["points"] for item in items) == 350
    # Lowercase machine token: enums use lowercase except CustomerLevel.
    assert all(item["type"] == "earn" for item in items)


def test_customer_cannot_read_someone_else_ledger(client, app, make_user, business):
    customer = make_user(email="c9@paseo.test")
    other = make_user(email="c10@paseo.test")
    client.post(
        "/api/business/scan",
        json={"qr_code": other.qr_code, "amount_bs": 500},
        headers=token_for(business.owner),
    )
    assert client.get("/api/me/transactions", headers=token_for(customer)).get_json()["items"] == []


def test_points_endpoint_reports_progress(client, app, make_user, business):
    customer = make_user(email="c11@paseo.test")
    client.post(
        "/api/business/scan",
        json={"qr_code": customer.qr_code, "amount_bs": 400},
        headers=token_for(business.owner),
    )
    body = client.get("/api/me/points", headers=token_for(customer)).get_json()
    assert body["points"] == 400
    assert body["level"] == "SILVER"  # 400 >= 300 (BRONZE), below GOLD's 700
    # get_level_progress() is flattened into the response, not nested, so the
    # frontend reads body["progress"] as a 0..1 ratio for a progress bar.
    assert body["next_level"] == "GOLD"
    assert body["points_to_next"] == 300  # 700 - 400
    assert 0.0 < body["progress"] < 1.0
    assert body["current_threshold"] == 300


# --- Manual admin adjustments -------------------------------------------------


def test_admin_adjustment_is_appended_not_overwritten(client, app, make_user, business):
    customer = make_user(email="c12@paseo.test")
    admin = make_user(email="admin@paseo.test", role=UserRole.ADMIN)

    response = client.post(
        f"/api/admin/users/{customer.id}/points",
        json={"points": 500, "note": "Compra mal registrada"},
        headers=token_for(admin),
    )
    assert response.status_code == 201, response.get_json()
    assert response.get_json()["balance"] == 500

    db.session.refresh(customer)
    assert customer.points_balance == 500

    # A later adjustment stacks; the first row is still there.
    client.post(
        f"/api/admin/users/{customer.id}/points",
        json={"points": -200, "note": "Correccion"},
        headers=token_for(admin),
    )
    rows = db.session.execute(db.select(Transaction.points)).scalars().all()
    assert sorted(rows) == [-200, 500]


def test_admin_adjustment_requires_a_note(client, app, make_user, business):
    # An unexplained balance change in an append-only ledger is
    # indistinguishable from a bug six months later.
    customer = make_user(email="c13@paseo.test")
    admin = make_user(email="admin2@paseo.test", role=UserRole.ADMIN)
    response = client.post(
        f"/api/admin/users/{customer.id}/points",
        json={"points": 500},
        headers=token_for(admin),
    )
    assert response.status_code == 400


def test_non_admin_cannot_adjust_points(client, app, make_user):
    customer = make_user(email="c14@paseo.test")
    attacker = make_user(email="attacker@paseo.test")
    response = client.post(
        f"/api/admin/users/{customer.id}/points",
        json={"points": 999999, "note": "me"},
        headers=token_for(attacker),
    )
    assert response.status_code == 403
    db.session.refresh(customer)
    assert customer.points_balance == 0


def test_admin_cannot_overdraw_a_customer(client, app, make_user):
    customer = make_user(email="c15@paseo.test")
    admin = make_user(email="admin3@paseo.test", role=UserRole.ADMIN)
    response = client.post(
        f"/api/admin/users/{customer.id}/points",
        json={"points": -100, "note": "quitar"},
        headers=token_for(admin),
    )
    assert response.status_code == 409
    # `details` is a sibling of `error`, not nested inside it.
    assert response.get_json()["details"]["balance"] == 0
    assert response.get_json()["details"]["missing"] == 100


def test_zero_adjustment_is_rejected(client, app, make_user):
    # A zero row is meaningless and would also trip the DB CHECK.
    customer = make_user(email="c16@paseo.test")
    admin = make_user(email="admin4@paseo.test", role=UserRole.ADMIN)
    response = client.post(
        f"/api/admin/users/{customer.id}/points",
        json={"points": 0, "note": "nada"},
        headers=token_for(admin),
    )
    assert response.status_code == 400


def test_balance_is_the_sum_of_the_ledger_even_after_many_movements(app, make_user, business):
    from services.points import adjust_points, refund_points, spend_points

    customer = make_user(email="c17@paseo.test")
    adjust_points(customer, 1000, note="inicial")
    spend_points(customer, 300)
    refund_points(customer, 100)
    adjust_points(customer, -50, note="correccion")

    assert get_balance(customer) == 750
    rows = db.session.execute(
        db.select(Transaction.points).order_by(Transaction.created_at)
    ).scalars().all()
    assert sum(rows) == 750


# --- Anti-duplicate window ----------------------------------------------------
#
# Without a window, a shop and a customer in collusion can scan the pair in a
# loop and mint an unlimited balance. The rate limiter does not save it: 60
# scans a minute is plenty to farm.


def test_second_scan_of_the_same_pair_within_the_window_is_refused(
    client, app, make_user, business
):
    customer = make_user(email="c18@paseo.test")
    payload = {"qr_code": customer.qr_code, "amount_bs": 100}

    first = client.post(
        "/api/business/scan", json=payload, headers=token_for(business.owner)
    )
    assert first.status_code == 201, first.get_json()

    second = client.post(
        "/api/business/scan", json=payload, headers=token_for(business.owner)
    )
    assert second.status_code == 409, second.get_json()
    body = second.get_json()
    assert body["code"] == "conflict"
    details = body["details"]
    assert details["retry_after_seconds"] == 60
    # The shop is told WHICH scan to wait for, so it can explain the refusal.
    assert details["previous_transaction_id"] == first.get_json()["transaction"]["id"]
    # A 409 that says "wait" without saying how long is not actionable.
    assert second.headers.get("Retry-After") == "60"

    db.session.refresh(customer)
    assert customer.points_balance == 100, "the refused scan must not credit anything"


def test_same_scan_is_accepted_again_once_the_window_has_passed(
    client, app, make_user, business
):
    # Time travel: rewrite created_at rather than sleeping, so the test stays
    # instant and does not depend on how fast the machine runs.
    from datetime import datetime, timedelta, timezone

    customer = make_user(email="c19@paseo.test")
    payload = {"qr_code": customer.qr_code, "amount_bs": 100}
    first = client.post(
        "/api/business/scan", json=payload, headers=token_for(business.owner)
    )
    assert first.status_code == 201

    entry = db.session.execute(db.select(Transaction)).scalars().one()
    entry.created_at = datetime.now(timezone.utc) - timedelta(seconds=61)
    db.session.commit()

    again = client.post(
        "/api/business/scan", json=payload, headers=token_for(business.owner)
    )
    assert again.status_code == 201, again.get_json()

    db.session.refresh(customer)
    assert customer.points_balance == 200


def test_the_window_is_scoped_to_the_pair_not_to_the_customer(
    client, app, make_user, business
):
    # Three different shops in one minute is ordinary shopping. Blocking it would
    # break the common case to prevent an abuse that needs collusion.
    customer = make_user(email="c20@paseo.test")
    rival_user = User(
        email="rival2@paseo.test",
        name="Rival",
        google_sub="sub-rival2",
        role=UserRole.SELLER.value,
    )
    db.session.add(rival_user)
    db.session.flush()
    rival = Business(
        user_id=rival_user.id,
        name="Otro Cafe",
        points_per_bs=Decimal("1.00"),
        active=True,
    )
    db.session.add(rival)
    db.session.commit()

    payload = {"qr_code": customer.qr_code, "amount_bs": 100}
    assert (
        client.post(
            "/api/business/scan", json=payload, headers=token_for(business.owner)
        ).status_code
        == 201
    )
    assert (
        client.post(
            "/api/business/scan", json=payload, headers=token_for(rival_user)
        ).status_code
        == 201
    )

    db.session.refresh(customer)
    assert customer.points_balance == 200


def test_order_delivery_is_not_blocked_by_a_recent_scan(client, app, make_user, business):
    # The scan window must NOT apply to PaseoYa order delivery. Otherwise a
    # customer who is scanned at a shop and then collects an order from the same
    # shop within 60 seconds never gets the points for the delivery -- and the
    # points are what the shop is owed.
    customer = make_user(email="c21@paseo.test")
    product = _product_for(business, Decimal("10.00"))

    scanned = client.post(
        "/api/business/scan",
        json={"qr_code": customer.qr_code, "amount_bs": 100},
        headers=token_for(business.owner),
    )
    assert scanned.status_code == 201

    order = client.post(
        "/api/orders",
        json={"items": [{"product_id": str(product.id), "quantity": 1}]},
        headers=token_for(customer),
    ).get_json()["order"]

    for status in ("confirmed", "preparing", "ready_for_pickup"):
        client.patch(
            f"/api/business/orders/{order['id']}/status",
            json={"status": status},
            headers=token_for(business.owner),
        )
    delivered = client.post(
        f"/api/business/orders/{order['id']}/pickup",
        json={"code": order["pickup_code"]},
        headers=token_for(business.owner),
    )
    assert delivered.status_code == 200, delivered.get_json()

    db.session.refresh(customer)
    # 100 from the scan plus 10 from the delivered order.
    assert customer.points_balance == 110


def test_window_can_be_disabled(client, app, make_user, business):
    # Set to 0 the check is off. Kept configurable because the right value
    # depends on how a real shop's point-of-sale behaves, and that is not
    # something to hardcode from a guess.
    app.config["SCAN_DUPLICATE_WINDOW_SECONDS"] = 0
    customer = make_user(email="c22@paseo.test")
    payload = {"qr_code": customer.qr_code, "amount_bs": 100}
    for _ in range(3):
        response = client.post(
            "/api/business/scan", json=payload, headers=token_for(business.owner)
        )
        assert response.status_code == 201, response.get_json()


def _product_for(shop: Business, price: Decimal):
    from models import Product

    product = Product(
        business_id=shop.id,
        name=f"Producto {price}",
        description="test",
        price_bs=price,
        stock=10,
        active=True,
    )
    db.session.add(product)
    db.session.commit()
    return product