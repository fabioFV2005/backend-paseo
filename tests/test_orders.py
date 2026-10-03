"""End-to-end tests for PaseoYa orders (reto 5).

PaseoYa is pickup-only and single-shop: one order belongs to exactly one
business, the customer walks into the mall, and points are credited when the
order is handed over -- not when it is paid for.

The rules worth defending here are the ones a customer would notice if they
broke:

  - the price charged is the price in the database, never a price sent by the
    client, even if the client insists;
  - stock cannot go negative under any interleaving of requests;
  - points are credited exactly once, on delivery;
  - a coupon applies only to its own shop and only above its minimum.
"""

from decimal import Decimal

import pytest

from extensions import db
from models import Business, Coupon, Product, Transaction, User
from roles import UserRole


@pytest.fixture()
def shop(app):
    """An active shop with three products."""
    owner = User(
        email="vendedor@paseo.test",
        name="Vendedor",
        google_sub="sub-vendedor",
        role=UserRole.SELLER.value,
    )
    db.session.add(owner)
    db.session.flush()
    business = Business(
        user_id=owner.id,
        name="Cafe Aranjuez",
        category="Comida",
        points_per_bs=Decimal("1.00"),
        active=True,
    )
    db.session.add(business)
    db.session.flush()

    products = {}
    for name, price, stock in (
        ("Cafe", Decimal("5.00"), 10),
        ("Empanada", Decimal("8.50"), 2),
        ("Batido", Decimal("12.00"), 5),
    ):
        product = Product(
            business_id=business.id,
            name=name,
            price_bs=price,
            stock=stock,
            active=True,
        )
        db.session.add(product)
        products[name] = product
    db.session.commit()
    return business, products


def token_for(user):
    from auth import create_token

    return {"Authorization": f"Bearer {create_token(user)}"}


def place(client, customer, products, **overrides):
    payload = {
        "items": [
            {"product_id": str(products["Cafe"].id), "quantity": 1},
            {"product_id": str(products["Empanada"].id), "quantity": 1},
        ]
    }
    payload.update(overrides)
    return client.post("/api/orders", json=payload, headers=token_for(customer))


# --- Creating an order --------------------------------------------------------


def test_order_total_is_computed_server_side(client, app, make_user, shop):
    business, products = shop
    customer = make_user(email="p1@paseo.test")
    response = place(client, customer, products)
    assert response.status_code == 201, response.get_json()

    order = response.get_json()["order"]
    assert order["total_bs"] == pytest.approx(13.50)  # 5.00 + 8.50
    assert order["status"] == "received"
    assert order["business_name"] == "Cafe Aranjuez"
    assert len(order["items"]) == 2


def test_client_cannot_invent_a_price(client, app, make_user, shop):
    business, products = shop
    customer = make_user(email="p2@paseo.test")
    response = place(
        client,
        customer,
        products,
        items=[{"product_id": str(products["Cafe"].id), "quantity": 1, "price_bs": 0.01}],
    )
    assert response.status_code == 201
    order = response.get_json()["order"]
    assert order["total_bs"] == pytest.approx(5.00)  # the 0.01 was ignored
    assert order["items"][0]["unit_price_bs"] == pytest.approx(5.00)


def test_stock_is_decremented_atomically(client, app, make_user, shop):
    business, products = shop
    customer = make_user(email="p3@paseo.test")
    place(client, customer, products, items=[{"product_id": str(products["Empanada"].id), "quantity": 2}])
    db.session.refresh(products["Empanada"])
    assert products["Empanada"].stock == 0  # had 2, took both


def test_order_beyond_stock_is_rejected_and_leaves_stock_intact(client, app, make_user, shop):
    business, products = shop
    customer = make_user(email="p4@paseo.test")
    response = place(
        client, customer, products,
        items=[{"product_id": str(products["Empanada"].id), "quantity": 3}],  # only 2 in stock
    )
    assert response.status_code == 409
    db.session.refresh(products["Empanada"])
    assert products["Empanada"].stock == 2  # untouched


def test_two_customers_cannot_oversell_the_last_unit(client, app, make_user, shop):
    # The conditional UPDATE is the guarantee: stock only decrements if it is
    # still positive, so the second request loses instead of going negative.
    business, products = shop
    empanada = products["Empanada"]
    empanada.stock = 1
    db.session.commit()

    first = make_user(email="p5@paseo.test")
    second = make_user(email="p6@paseo.test")
    cart = [{"product_id": str(empanada.id), "quantity": 1}]

    a = client.post("/api/orders", json={"items": cart}, headers=token_for(first))
    b = client.post("/api/orders", json={"items": cart}, headers=token_for(second))

    assert a.status_code == 201
    assert b.status_code == 409
    db.session.refresh(empanada)
    assert empanada.stock == 0


def test_an_order_must_have_items(client, app, make_user, shop):
    business, products = shop
    customer = make_user(email="p7@paseo.test")
    assert place(client, customer, products, items=[]).status_code == 400


def test_all_items_must_belong_to_one_shop(client, app, make_user, shop):
    # A single order belongs to a single business: no cross-shop basket.
    business, products = shop
    other_owner = User(
        email="otro@paseo.test", name="Otro", google_sub="sub-otro", role=UserRole.SELLER.value
    )
    db.session.add(other_owner)
    db.session.flush()
    other = Business(
        user_id=other_owner.id, name="Otro Cafe", points_per_bs=Decimal("1.00"), active=True
    )
    db.session.add(other)
    db.session.flush()
    foreign = Product(business_id=other.id, name="Foreign", price_bs=Decimal("99.00"), stock=5, active=True)
    db.session.add(foreign)
    db.session.commit()

    customer = make_user(email="p8@paseo.test")
    response = client.post(
        "/api/orders",
        json={
            "items": [
                {"product_id": str(products["Cafe"].id), "quantity": 1},
                {"product_id": str(foreign.id), "quantity": 1},
            ]
        },
        headers=token_for(customer),
    )
    # 422: the request is well formed, it just breaks a domain rule (one shop
    # per order, because pickup happens in a single visit).
    assert response.status_code == 422


def test_inactive_product_cannot_be_ordered(client, app, make_user, shop):
    business, products = shop
    products["Cafe"].active = False
    db.session.commit()
    customer = make_user(email="p9@paseo.test")
    response = place(
        client, customer, products,
        items=[{"product_id": str(products["Cafe"].id), "quantity": 1}],
    )
    # 422, not 404: the product exists, it is simply no longer orderable.
    assert response.status_code == 422


# --- Lifecycle and points -----------------------------------------------------


def test_points_are_credited_on_delivery_not_on_payment(client, app, make_user, shop):
    business, products = shop
    customer = make_user(email="p10@paseo.test")
    order = place(
        client, customer, products,
        items=[{"product_id": str(products["Cafe"].id), "quantity": 1}],
    ).get_json()["order"]
    order_id, pickup_code = order["id"], order["pickup_code"]

    # Paid, but not handed over yet.
    db.session.refresh(customer)
    assert customer.points_balance == 0

    seller = token_for(business.owner)
    for status in ("confirmed", "preparing", "ready_for_pickup", "customer_arrived"):
        r = client.patch(f"/api/business/orders/{order_id}/status", json={"status": status}, headers=seller)
        assert r.status_code == 200, (status, r.get_json())
        db.session.refresh(customer)
        assert customer.points_balance == 0, f"credited too early at {status}"

    r = client.post(
        f"/api/business/orders/{order_id}/pickup",
        json={"code": pickup_code},
        headers=seller,
    )
    assert r.status_code == 200, r.get_json()

    db.session.refresh(customer)
    assert customer.points_balance == 5  # 5.00 Bs at 1 point/Bs
    assert customer.level == "BRONZE"


def test_wrong_pickup_code_does_not_hand_over_the_order(client, app, make_user, shop):
    business, products = shop
    customer = make_user(email="p18@paseo.test")
    order = place(
        client, customer, products,
        items=[{"product_id": str(products["Cafe"].id), "quantity": 1}],
    ).get_json()["order"]
    order_id = order["id"]

    seller = token_for(business.owner)
    for status in ("confirmed", "preparing", "ready_for_pickup"):
        client.patch(f"/api/business/orders/{order_id}/status", json={"status": status}, headers=seller)

    wrong = client.post(
        f"/api/business/orders/{order_id}/pickup",
        json={"code": "000000" if order["pickup_code"] != "000000" else "111111"},
        headers=seller,
    )
    assert wrong.status_code in (403, 409, 422)
    db.session.refresh(customer)
    assert customer.points_balance == 0  # no points for a refused handover


def test_pickup_credits_points_only_once(client, app, make_user, shop):
    business, products = shop
    customer = make_user(email="p11@paseo.test")
    order = place(
        client, customer, products,
        items=[{"product_id": str(products["Cafe"].id), "quantity": 1}],
    ).get_json()["order"]
    order_id, pickup_code = order["id"], order["pickup_code"]

    seller = token_for(business.owner)
    for status in ("confirmed", "preparing", "ready_for_pickup"):
        client.patch(f"/api/business/orders/{order_id}/status", json={"status": status}, headers=seller)
    assert client.post(
        f"/api/business/orders/{order_id}/pickup",
        json={"code": pickup_code},
        headers=seller,
    ).status_code == 200

    # A second scan of the same order must not mint more points.
    repeat = client.post(
        f"/api/business/orders/{order_id}/pickup",
        json={"code": pickup_code},
        headers=seller,
    )
    assert repeat.status_code in (409, 422)

    db.session.refresh(customer)
    assert customer.points_balance == 5
    earnings = db.session.execute(
        db.select(Transaction).where(Transaction.type == "earn")
    ).scalars().all()
    assert len(earnings) == 1


def test_order_cannot_skip_backwards(client, app, make_user, shop):
    business, products = shop
    customer = make_user(email="p12@paseo.test")
    order_id = place(
        client, customer, products,
        items=[{"product_id": str(products["Cafe"].id), "quantity": 1}],
    ).get_json()["order"]["id"]

    seller = token_for(business.owner)
    client.patch(f"/api/business/orders/{order_id}/status", json={"status": "confirmed"}, headers=seller)
    back = client.patch(f"/api/business/orders/{order_id}/status", json={"status": "received"}, headers=seller)
    assert back.status_code == 422


def test_cancelling_restores_stock_and_credits_nothing(client, app, make_user, shop):
    business, products = shop
    customer = make_user(email="p13@paseo.test")
    order_id = place(
        client, customer, products,
        items=[{"product_id": str(products["Empanada"].id), "quantity": 2}],
    ).get_json()["order"]["id"]
    db.session.refresh(products["Empanada"])
    assert products["Empanada"].stock == 0

    response = client.post(
        f"/api/business/orders/{order_id}/cancel",
        json={"reason": "El cliente no vino"},
        headers=token_for(business.owner),
    )
    assert response.status_code == 200, response.get_json()

    db.session.refresh(products["Empanada"])
    assert products["Empanada"].stock == 2
    db.session.refresh(customer)
    assert customer.points_balance == 0


def test_cannot_hand_over_an_order_that_is_not_ready(client, app, make_user, shop):
    # The customer standing at the counter is not evidence the food exists. If a
    # cashier could deliver straight from `received`, the customer would get a
    # drink that was never made and the points would be credited for it.
    business, products = shop
    customer = make_user(email="p22@paseo.test")
    order = place(
        client, customer, products,
        items=[{"product_id": str(products["Cafe"].id), "quantity": 1}],
    ).get_json()["order"]

    response = client.post(
        f"/api/business/orders/{order['id']}/pickup",
        json={"code": order["pickup_code"]},
        headers=token_for(business.owner),
    )
    assert response.status_code == 422
    db.session.refresh(customer)
    assert customer.points_balance == 0


def test_cancelling_an_order_with_a_coupon_refunds_the_points(client, app, coupon_shop):
    # The customer already spent points to buy the coupon at checkout. If the
    # order is then cancelled, those points have bought nothing, so they go
    # back -- as a NEW ledger row, so the statement still adds up.
    business, products, reward, customer = coupon_shop
    coupon_id = client.post(
        f"/api/rewards/{reward.id}/redeem", json={}, headers=token_for(customer)
    ).get_json()["coupon"]["id"]

    db.session.refresh(customer)
    after_redeem = customer.points_balance

    order = client.post(
        "/api/orders",
        json={
            "items": [{"product_id": str(products["Cafe"].id), "quantity": 1}],
            "coupon_id": coupon_id,
        },
        headers=token_for(customer),
    ).get_json()["order"]
    db.session.refresh(customer)
    assert customer.points_balance == after_redeem

    response = client.post(
        f"/api/business/orders/{order['id']}/cancel",
        json={"reason": "El cliente se arrepintio"},
        headers=token_for(business.owner),
    )
    assert response.status_code == 200, response.get_json()

    db.session.refresh(customer)
    assert customer.points_balance == after_redeem + reward.points_cost

    # The refund is a new ledger row, not an edit of the original, so the
    # statement still adds up.
    rows = db.session.execute(db.select(Transaction.type)).scalars().all()
    assert "refund" in rows

    # The coupon never really took effect, so it must be spendable again on a
    # second order. Checking 'active' alone would not be enough: it proves the
    # label, not that a checkout actually accepts it.
    from models import Coupon

    import uuid as _uuid

    row = db.session.get(Coupon, _uuid.UUID(coupon_id))
    db.session.refresh(row)
    assert row.status == "active"
    assert row.used_at is None

    reused = client.post(
        "/api/orders",
        json={
            "items": [{"product_id": str(products["Cafe"].id), "quantity": 1}],
            "coupon_id": coupon_id,
        },
        headers=token_for(customer),
    )
    assert reused.status_code == 201, reused.get_json()
    db.session.refresh(row)
    assert row.status == "used"


def test_another_business_cannot_touch_the_order(client, app, make_user, shop):
    business, products = shop
    intruder_owner = User(
        email="intruso@paseo.test", name="Intruso", google_sub="sub-intruso", role=UserRole.SELLER.value
    )
    db.session.add(intruder_owner)
    db.session.flush()
    intruder = Business(
        user_id=intruder_owner.id, name="Intruso", points_per_bs=Decimal("1.00"), active=True
    )
    db.session.add(intruder)
    db.session.commit()

    customer = make_user(email="p14@paseo.test")
    order = place(
        client, customer, products,
        items=[{"product_id": str(products["Cafe"].id), "quantity": 1}],
    ).get_json()["order"]

    # Crucially it must not credit points from someone else's sale either.
    # The pickup code is valid; what is wrong is who is presenting it.
    response = client.post(
        f"/api/business/orders/{order['id']}/pickup",
        json={"code": order["pickup_code"]},
        headers=token_for(intruder_owner),
    )
    assert response.status_code == 403
    db.session.refresh(customer)
    assert customer.points_balance == 0


def test_customer_sees_their_orders_and_only_theirs(client, app, make_user, shop):
    business, products = shop
    customer = make_user(email="p15@paseo.test")
    other = make_user(email="p16@paseo.test")
    place(client, customer, products)
    place(client, other, products)

    mine = client.get("/api/me/orders", headers=token_for(customer)).get_json()["items"]
    assert len(mine) == 1

    detail = client.get(
        f"/api/me/orders/{mine[0]['id']}", headers=token_for(other)
    )
    assert detail.status_code == 404  # not 403: do not confirm it exists


def test_customer_announces_arrival(client, app, make_user, shop):
    business, products = shop
    customer = make_user(email="p17@paseo.test")
    order = place(
        client, customer, products,
        items=[{"product_id": str(products["Cafe"].id), "quantity": 1}],
    ).get_json()["order"]

    # Walking in before the shop has even accepted the order is refused: arrival
    # is a step in the lifecycle, not a shortcut to delivered.
    early = client.post(f"/api/me/orders/{order['id']}/arrive", json={}, headers=token_for(customer))
    assert early.status_code == 422

    seller = token_for(business.owner)
    for status in ("confirmed", "preparing", "ready_for_pickup"):
        client.patch(
            f"/api/business/orders/{order['id']}/status",
            json={"status": status},
            headers=seller,
        )

    response = client.post(f"/api/me/orders/{order['id']}/arrive", json={}, headers=token_for(customer))
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["order"]["status"] == "customer_arrived"


def test_customer_cannot_arrive_for_someone_elses_order(client, app, make_user, shop):
    business, products = shop
    customer = make_user(email="p19@paseo.test")
    other = make_user(email="p20@paseo.test")
    order = place(
        client, customer, products,
        items=[{"product_id": str(products["Cafe"].id), "quantity": 1}],
    ).get_json()["order"]
    response = client.post(f"/api/me/orders/{order['id']}/arrive", json={}, headers=token_for(other))
    assert response.status_code == 404


# --- Coupons ------------------------------------------------------------------


@pytest.fixture()
def coupon_shop(app, shop):
    """The shop plus a 10% reward costing 300 points, and a funded customer."""
    business, products = shop

    from services.rewards_service import create_reward

    reward = create_reward(
        {
            "business_id": str(business.id),
            "title": "10% de descuento",
            "discount_type": "percent",
            "discount_value": Decimal("10"),
            "max_discount_bs": Decimal("30"),
            "points_cost": 300,
            "valid_days": 30,
            "stock": 10,
        },
        business=business,
    )

    customer = User(email="cupon@paseo.test", name="Cliente Cupon", google_sub="sub-cupon")
    db.session.add(customer)
    db.session.commit()

    from services.points import adjust_points

    adjust_points(customer, 1000, note="saldo inicial")
    return business, products, reward, customer


def test_redeem_spends_points_and_issues_a_coupon(client, app, coupon_shop):
    business, products, reward, customer = coupon_shop
    db.session.refresh(customer)
    before = customer.points_balance

    response = client.post(f"/api/rewards/{reward.id}/redeem", json={}, headers=token_for(customer))
    assert response.status_code == 201, response.get_json()
    body = response.get_json()

    db.session.refresh(customer)
    assert customer.points_balance == before - reward.points_cost
    assert body["coupon"]["status"] == "active"


def test_redeeming_costs_more_points_than_the_customer_has(client, app, coupon_shop):
    business, products, reward, customer = coupon_shop

    from services.points import adjust_points

    adjust_points(customer, -800, note="dejar sin saldo")  # 1000 - 800 = 200 < 300
    db.session.refresh(customer)

    response = client.post(f"/api/rewards/{reward.id}/redeem", json={}, headers=token_for(customer))
    assert response.status_code == 409
    db.session.refresh(customer)
    assert customer.points_balance == 200  # nothing was taken


def test_coupon_discount_is_capped_and_calculated_server_side(client, app, coupon_shop):
    business, products, reward, customer = coupon_shop
    coupon_response = client.post(
        f"/api/rewards/{reward.id}/redeem", json={}, headers=token_for(customer)
    ).get_json()
    coupon_id = coupon_response["coupon"]["id"]

    # Bs 500 of Cafe would be 10% = 50, but max_discount_bs caps it at 30.
    products["Cafe"].stock = 100
    db.session.commit()
    # 99 is the per-line maximum the API allows, so 99 x 5.00 = Bs 495.
    response = client.post(
        "/api/orders",
        json={
            "items": [{"product_id": str(products["Cafe"].id), "quantity": 99}],
            "coupon_id": coupon_id,
        },
        headers=token_for(customer),
    )
    assert response.status_code == 201, response.get_json()
    order = response.get_json()["order"]

    assert order["subtotal_bs"] == pytest.approx(495.00)
    assert order["discount_bs"] == pytest.approx(30.00)  # 10% would be 49.50, capped
    assert order["total_bs"] == pytest.approx(465.00)


def test_per_line_quantity_is_capped(client, app, make_user, shop):
    # Unbounded quantities are how a single request turns into a Bs 5000 charge
    # and a stock table full of nonsense.
    business, products = shop
    customer = make_user(email="c21@paseo.test")
    response = place(
        client, customer, products,
        items=[{"product_id": str(products["Cafe"].id), "quantity": 100}],
    )
    assert response.status_code == 400


def test_coupon_is_consumed_on_use(client, app, coupon_shop):
    business, products, reward, customer = coupon_shop
    coupon_id = client.post(
        f"/api/rewards/{reward.id}/redeem", json={}, headers=token_for(customer)
    ).get_json()["coupon"]["id"]

    cart = {"items": [{"product_id": str(products["Cafe"].id), "quantity": 1}]}
    assert client.post(
        "/api/orders", json={**cart, "coupon_id": coupon_id}, headers=token_for(customer)
    ).status_code == 201
    assert client.post(
        "/api/orders", json={**cart, "coupon_id": coupon_id}, headers=token_for(customer)
    ).status_code == 422  # already used


def test_another_customers_coupon_cannot_be_used(client, app, coupon_shop):
    business, products, reward, customer = coupon_shop
    coupon_id = client.post(
        f"/api/rewards/{reward.id}/redeem", json={}, headers=token_for(customer)
    ).get_json()["coupon"]["id"]

    from models import get_or_create_google_user

    thief = get_or_create_google_user("sub-thief", "thief@paseo.test", "Ladrón")
    response = client.post(
        "/api/orders",
        json={
            "items": [{"product_id": str(products["Cafe"].id), "quantity": 1}],
            "coupon_id": coupon_id,
        },
        headers=token_for(thief),
    )
    assert response.status_code in (403, 404, 409)


def test_coupon_below_its_minimum_is_rejected(client, app, coupon_shop):
    business, products, reward, customer = coupon_shop

    # Set the minimum BEFORE issuing: the coupon snapshots the terms it was
    # issued with, so editing the reward afterwards must not retroactively
    # change an outstanding coupon. That is the point of the snapshot.
    reward.min_purchase_bs = Decimal("500.00")
    db.session.commit()

    from services.rewards_service import issue_coupon

    coupon = issue_coupon(customer, reward, commit=True)

    # A Bs 5.00 Cafe cannot use a coupon that needs Bs 500.
    response = client.post(
        "/api/orders",
        json={
            "items": [{"product_id": str(products["Cafe"].id), "quantity": 1}],
            "coupon_id": str(coupon.id),
        },
        headers=token_for(customer),
    )
    assert response.status_code == 422
    # The coupon must survive a rejected attempt.
    db.session.refresh(coupon)
    assert coupon.status == "active"


def test_editing_a_reward_does_not_change_an_outstanding_coupon(client, app, coupon_shop):
    # The same snapshot rule seen from the other side: the shop raises the
    # reward's cost, and a coupon the customer already holds keeps the discount
    # it was promised.
    business, products, reward, customer = coupon_shop
    from services.rewards_service import issue_coupon

    coupon = issue_coupon(customer, reward, commit=True)
    original_value = coupon.discount_value

    reward.discount_value = Decimal("90")
    reward.min_purchase_bs = Decimal("9999.00")
    db.session.commit()

    db.session.refresh(coupon)
    assert coupon.discount_value == original_value
    assert float(coupon.min_purchase_bs) == 0.0


def test_rewards_catalog_hides_inactive_and_other_shops(client, app, coupon_shop):
    business, products, reward, customer = coupon_shop
    listed = client.get("/api/rewards").get_json()["items"]
    assert any(item["id"] == str(reward.id) for item in listed)

    reward.active = False
    db.session.commit()
    listed = client.get("/api/rewards").get_json()["items"]
    assert not any(item["id"] == str(reward.id) for item in listed)


def test_out_of_stock_reward_cannot_be_redeemed(client, app, coupon_shop):
    business, products, reward, customer = coupon_shop
    reward.stock = 0
    db.session.commit()

    from services.points import adjust_points

    adjust_points(customer, 1000, note="saldo")
    response = client.post(f"/api/rewards/{reward.id}/redeem", json={}, headers=token_for(customer))
    assert response.status_code == 409


# --- Home delivery (pedidos a domicilio) ----------------------------------------


def test_order_with_delivery_successful(client, app, shop, make_user):
    business, products = shop
    business.offers_delivery = True
    business.delivery_fee_bs = Decimal("12.50")
    business.delivery_info = "Envios en zona norte"
    db.session.commit()

    customer = make_user(email="delivery.client@paseo.test")
    response = client.post(
        "/api/orders",
        json={
            "items": [{"product_id": str(products["Cafe"].id), "quantity": 2}],
            "delivery_type": "delivery",
            "delivery_address": "Av. America #456, Edif. Paseo Depto 3B",
            "delivery_phone": "70712345",
            "delivery_instructions": "Llamar al llegar",
        },
        headers=token_for(customer),
    )
    assert response.status_code == 201
    order = response.get_json()["order"]
    assert order["delivery_type"] == "delivery"
    assert order["delivery_address"] == "Av. America #456, Edif. Paseo Depto 3B"
    assert order["delivery_phone"] == "70712345"
    assert order["delivery_instructions"] == "Llamar al llegar"
    assert order["subtotal_bs"] == 10.0  # 2 * 5.00
    assert order["delivery_fee_bs"] == 12.50
    assert order["total_bs"] == 22.50  # 10.0 + 12.50


def test_order_with_delivery_rejected_if_business_does_not_offer_it(client, app, shop, make_user):
    business, products = shop
    business.offers_delivery = False
    db.session.commit()

    customer = make_user(email="delivery.rejected@paseo.test")
    response = client.post(
        "/api/orders",
        json={
            "items": [{"product_id": str(products["Cafe"].id), "quantity": 1}],
            "delivery_type": "delivery",
            "delivery_address": "Calle Falsa 123",
        },
        headers=token_for(customer),
    )
    assert response.status_code == 422
    assert "no ofrece envíos a domicilio" in response.get_json()["error"]


def test_order_with_delivery_requires_address(client, app, shop, make_user):
    business, products = shop
    business.offers_delivery = True
    db.session.commit()

    customer = make_user(email="delivery.noaddress@paseo.test")
    response = client.post(
        "/api/orders",
        json={
            "items": [{"product_id": str(products["Cafe"].id), "quantity": 1}],
            "delivery_type": "delivery",
            "delivery_address": "   ",
        },
        headers=token_for(customer),
    )
    assert response.status_code == 400
    assert "delivery_address" in response.get_json()["error"]


def test_order_delivery_lifecycle_credits_points(client, app, shop, make_user):
    from services.orders_service import advance_order_status, create_order
    from services.points import get_balance

    business, products = shop
    business.offers_delivery = True
    business.delivery_fee_bs = Decimal("10.00")
    db.session.commit()

    customer = make_user(email="delivery.points@paseo.test")
    order = create_order(
        customer,
        [{"product_id": str(products["Cafe"].id), "quantity": 2}],
        delivery_type="delivery",
        delivery_address="Calle Mayor 10",
    )
    assert order.total_bs == Decimal("20.00")  # 10 subtotal + 10 fee
    assert order.status == "received"

    # Business advances: received -> confirmed -> preparing -> on_the_way -> delivered
    advance_order_status(order, "confirmed", business)
    assert order.status == "confirmed"

    advance_order_status(order, "preparing", business)
    assert order.status == "preparing"
    assert "on_the_way" in order.allowed_transitions()

    advance_order_status(order, "on_the_way", business)
    assert order.status == "on_the_way"

    advance_order_status(order, "delivered", business)
    assert order.status == "delivered"

    # Points must be credited upon delivery
    balance = get_balance(customer)
    assert balance == 20