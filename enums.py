"""Domain enumerations.

Every enum inherits from ``str`` so a member can be stored in a TEXT column,
serialized to JSON and compared with ``==`` without any conversion. The string
values are part of the database contract: they appear in CHECK constraints and
in API responses, so they must not be renamed without a migration.

``roles.UserRole`` is intentionally NOT defined here: authorization roles are a
separate concern from the rest of the domain vocabulary.

Casing convention, because it is a rule and not an accident:

- ``CustomerLevel`` uses UPPERCASE because those strings are shown to a person
  as the name of their tier ("you are GOLD").
- Every other enum uses lowercase because those strings are machine tokens the
  frontend switches on: statuses, transaction types, coupon states, payment
  methods. ``/api/me/transactions`` therefore returns ``"earn"``, not
  ``"EARN"``.

Both appear verbatim in API responses, so a frontend must not assume one casing
for the whole domain -- it switches on each field's documented values.
"""

from enum import Enum


class StrEnum(str, Enum):
    """Base for enums that serialize as plain strings."""

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value

    @classmethod
    def values(cls) -> list[str]:
        """All valid string values, for building CHECK constraints."""
        return [member.value for member in cls]

    @classmethod
    def parse(cls, raw: str):
        """Return the member matching ``raw``, or None if there is no match."""
        try:
            return cls(raw)
        except ValueError:
            return None


class CustomerLevel(StrEnum):
    """Loyalty tier. Thresholds live in ``services.points.LEVEL_THRESHOLDS``."""

    BRONZE = "BRONZE"
    SILVER = "SILVER"
    GOLD = "GOLD"
    PLATINUM = "PLATINUM"


class DeliveryType(StrEnum):
    """Fulfillment method for orders."""

    PICKUP = "pickup"
    DELIVERY = "delivery"


class OrderStatus(StrEnum):
    """Order lifecycle.

    Supports both in-store pickup and home delivery.
    Delivering is what triggers crediting the loyalty points.
    """

    RECEIVED = "received"
    CONFIRMED = "confirmed"
    PREPARING = "preparing"
    READY_FOR_PICKUP = "ready_for_pickup"
    ON_THE_WAY = "on_the_way"
    CUSTOMER_ARRIVED = "customer_arrived"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"

    @classmethod
    def open_states(cls) -> list[str]:
        """States from which the order can still progress or be cancelled."""
        return [
            cls.RECEIVED.value,
            cls.CONFIRMED.value,
            cls.PREPARING.value,
            cls.READY_FOR_PICKUP.value,
            cls.ON_THE_WAY.value,
            cls.CUSTOMER_ARRIVED.value,
        ]


class TransactionType(StrEnum):
    """The only kinds of movement the points ledger accepts.

    The ledger is append-only: a balance is never updated in place, it is the
    SUM of its rows. EARN/REDEEM/REFUND/BONUS are all positive/negative flows
    with a known cause; ADJUST is the manual admin correction escape hatch.
    """

    EARN = "earn"
    REDEEM = "redeem"
    REFUND = "refund"
    ADJUST = "adjust"
    BONUS = "bonus"


class DiscountType(StrEnum):
    PERCENT = "percent"
    FIXED = "fixed"


class CouponStatus(StrEnum):
    ACTIVE = "active"
    USED = "used"
    CANCELLED = "cancelled"


class PaymentMethod(StrEnum):
    """How the customer paid. The Paseo never touches the money: the business
    collects it however it normally does, and only confirms the order here."""

    CASH = "cash"
    TRANSFER = "transfer"
    CARD = "card"
    OTHER = "other"
