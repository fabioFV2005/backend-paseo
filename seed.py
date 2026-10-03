"""Demo data for `flask seed-demo`.

Everything here goes through the service layer rather than writing rows
directly, for two reasons: it exercises the same code paths the API uses, and
the demo balances are then genuinely derivable from the ledger instead of being
numbers typed into a column.

The users are created with the `google_sub` a real Google sign-in would produce,
but Google is never contacted. That means the demo works without OAuth
credentials configured -- and these accounts cannot be signed into through the
real Google flow either, which is what you want in data about to be shown to
anyone.

Re-running is safe: everything is matched on its natural key (email, shop
name, reward title) and updated in place, so `seed-demo` can be repeated after
adding a column.
"""

from decimal import Decimal

import click

import config

DEMO_LOGIN_NOTE = (
    "These accounts have no password: login is Google-only. To try the flow "
    "without configuring Google, use 'flask dev-token <email>'."
)


def _as_uuid(value):
    if value is None:
        return None
    import uuid

    return uuid.UUID(value) if isinstance(value, str) else value


def _upsert_user(email: str, name: str, role, *, sub: str | None = None):
    """Find-or-create by email, returning ``(user, created)``.

    Email is the natural key because it is UNIQUE in the schema.

    The role of an existing user is deliberately left alone: silently promoting
    or demoting a real account because someone re-ran a seed would be a nasty
    surprise. Only brand-new rows get the requested role.
    """
    from extensions import db
    from models import find_user_by_email, get_or_create_google_user
    from roles import UserRole

    existing = find_user_by_email(email)
    if existing is not None:
        return existing, False

    user = get_or_create_google_user(
        google_sub=sub or f"demo-{email}",
        email=email,
        name=name,
    )
    user.role = role.value if isinstance(role, UserRole) else role
    db.session.commit()
    return user, True


def _upsert_business(owner, name: str, category: str, description: str, rate: Decimal):
    """Find-or-create the single shop belonging to ``owner``.

    Returns ``(business, created)``; ``created`` means "no products yet", which
    is the signal to fill its catalog.
    """
    from extensions import db
    from models import Business

    business = db.session.execute(
        db.select(Business).where(Business.user_id == owner.id)
    ).scalar_one_or_none()
    if business is not None:
        return business, False

    business = Business(
        user_id=owner.id,
        name=name,
        category=category,
        description=description,
        location="Paseo Aranjuez",
        points_per_bs=rate,
        # Inactive, like every self-registered shop. The caller approves it, so
        # the demo does not quietly bypass the rule the API enforces.
        active=False,
    )
    db.session.add(business)
    db.session.commit()
    return business, True


def _seed_points(user, points: int, note: str) -> None:
    """Give a demo user a starting balance, once.

    Skipped when the user already has a balance so that re-running the seed
    does not add the amount again every time.
    """
    from extensions import db
    from services.points import adjust_points

    if points > 0 and user.points_balance == 0:
        adjust_points(user, points, note=note)


def run() -> None:
    from enums import DiscountType
    from extensions import db
    from models import Business, Product, Reward
    from roles import UserRole
    from services.rewards_service import create_reward

    admin, _ = _upsert_user("admin@paseo.test", "Admin Paseo", UserRole.ADMIN)
    customer, _ = _upsert_user("cliente@paseo.test", "Cliente Demo", UserRole.USER)
    owner_cafe, _ = _upsert_user("cafe@paseo.test", "Dueño Cafe", UserRole.SELLER)
    owner_tech, _ = _upsert_user("tech@paseo.test", "Dueño Tech", UserRole.SELLER)

    _seed_points(customer, 1200, "Saldo inicial de demo")

    cafe, cafe_new = _upsert_business(
        owner_cafe, "Cafe Aranjuez", "Comida",
        "Cafe de especialidad y pastelería.", Decimal("1.00"),
    )
    tech, tech_new = _upsert_business(
        owner_tech, "Tech Aranjuez", "Tecnología",
        "Accesorios y electrónica.", Decimal("0.50"),
    )

    # Both approved, so the public catalog has something in it straight away.
    cafe.active = True
    tech.active = True

    if cafe_new:
        for name, price, stock in (
            ("Cafe Espresso", "4.50", 40),
            ("Empanada de carne", "8.00", 25),
            ("Batido de fresa", "12.00", 15),
        ):
            db.session.add(
                Product(
                    business_id=cafe.id,
                    name=name,
                    description="Producto de demo",
                    price_bs=Decimal(price),
                    stock=stock,
                    active=True,
                )
            )

    if tech_new:
        for name, price, stock in (
            ("Audifonos BT", "45.00", 12),
            ("Cargador magnetico", "25.00", 20),
            ("Funda de celular", "12.00", 30),
        ):
            db.session.add(
                Product(
                    business_id=tech.id,
                    name=name,
                    description="Producto de demo",
                    price_bs=Decimal(price),
                    stock=stock,
                    active=True,
                )
            )

    db.session.commit()

    existing_titles = {
        title for (title,) in db.session.execute(db.select(Reward.title)).all()
    }
    demo_rewards = (
        (
            cafe,
            {
                "title": "10% en Cafes de especialidad",
                "description": "Descuento del 10%, hasta Bs 30.",
                "discount_type": DiscountType.PERCENT.value,
                "discount_value": Decimal("10"),
                "max_discount_bs": Decimal("30"),
                "points_cost": 300,
                "stock": 50,
            },
        ),
        (
            tech,
            {
                "title": "Bs 15 de descuento en tecnologia",
                "description": "Descuento fijo en cualquier producto de la tienda.",
                "discount_type": DiscountType.FIXED.value,
                "discount_value": Decimal("15"),
                "points_cost": 250,
                "stock": 30,
            },
        ),
        (
            # No business: sponsored by Paseo, therefore valid in every shop.
            None,
            {
                "title": "Bs 5 de descuento en el Paseo",
                "description": "Promocion patrocinada por Paseo Aranjuez.",
                "discount_type": DiscountType.FIXED.value,
                "discount_value": Decimal("5"),
                "points_cost": 100,
                "stock": None,  # unlimited
            },
        ),
    )

    created_rewards = 0
    for business, payload in demo_rewards:
        if payload["title"] in existing_titles:
            continue
        create_reward(dict(payload), business=business)
        created_rewards += 1

    db.session.commit()

    click.echo("")
    click.echo("Datos de demo listos.")
    click.echo(f"  ADMIN    {admin.email}")
    click.echo(f"  CLIENTE  {customer.email}   (1200 puntos)")
    click.echo(f"  CAFE     {owner_cafe.email}  -> Cafe Aranjuez (activo)")
    click.echo(f"  TECH     {owner_tech.email}  -> Tech Aranjuez (activo)")
    click.echo("")
    click.echo(f"  Recompensas creadas ahora: {created_rewards}")
    click.echo("")
    click.echo(f"  {DEMO_LOGIN_NOTE}")


def register(app) -> None:
    @app.cli.command("seed-demo")
    def seed_demo():
        """Fill the database with a coherent demo dataset.

        Idempotent: re-running updates instead of duplicating.
        """
        run()

    @app.cli.command("reset-demo")
    @click.option("--yes", is_flag=True, help="Skip the confirmation prompt.")
    def reset_demo(yes):
        """Drop every table, recreate and seed. Destructive."""
        if not yes:
            click.confirm("This deletes ALL data and re-seeds. Continue?", abort=True)

        from extensions import db

        db.drop_all()
        db.create_all()
        run()
        click.echo("Base de datos reiniciada.")

    @app.cli.command("dev-token")
    @click.argument("email")
    def dev_token(email):
        """Print a ready-to-use JWT for an existing user. Development only.

        Exists so the API can be exercised end to end without configuring
        Google OAuth. It mints a token for a user that already exists, so it
        cannot create or elevate an account on its own -- but it bypasses the
        Google identity check, which is exactly why it must never be deployed.
        """
        if config.IS_PRODUCTION:
            raise click.ClickException(
                "dev-token is disabled when FLASK_ENV=production"
            )

        from auth import create_token
        from extensions import db
        from models import find_user_by_email

        user = find_user_by_email(email)
        if user is None:
            raise click.ClickException(f"No user with email {email!r}. Run 'flask seed-demo'.")

        click.echo(create_token(user))
        click.echo(f"# role={user.role} points={user.points_balance}", err=True)