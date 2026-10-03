"""Business logic layer.

Routes stay thin: they parse input, call a service, and shape a response. Every
rule that touches money or points lives here instead, which means the rules can
be tested without going through HTTP.

The two functions that mutate the points ledger -- credit_purchase() and
spend_points() -- both run inside a single database transaction together with
whatever else must succeed or fail with them (stock decrement, coupon issue,
level update). A partial write would be a real bug: points credited without a
sale, or stock sold without an order.
"""

from services.errors import (
    BusinessRuleError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)
from services.points import (
    LEVEL_THRESHOLDS,
    adjust_points,
    credit_purchase,
    get_balance,
    get_level_progress,
    level_for_balance,
    refund_points,
    spend_points,
)
from services.rewards_service import (
    calculate_coupon_discount,
    issue_coupon,
    list_rewards,
    redeem_reward,
    validate_coupon,
)
from services.catalog import (
    create_product,
    get_product,
    list_businesses,
    list_categories,
    list_products,
    search_products,
    update_product,
)
from services.orders_service import (
    advance_order_status,
    cancel_order,
    create_order,
    get_order,
    list_customer_orders,
    list_business_orders,
    mark_customer_arrived,
    validate_pickup,
)
from services.business_service import (
    get_business_for_owner,
    get_business_or_404,
    list_business_transactions,
    register_business,
    update_business,
)

__all__ = [
    "BusinessRuleError",
    "ConflictError",
    "ForbiddenError",
    "NotFoundError",
    "ValidationError",
    "LEVEL_THRESHOLDS",
    "adjust_points",
    "credit_purchase",
    "get_balance",
    "get_level_progress",
    "level_for_balance",
    "refund_points",
    "spend_points",
    "calculate_coupon_discount",
    "issue_coupon",
    "list_rewards",
    "redeem_reward",
    "validate_coupon",
    "create_product",
    "get_product",
    "list_businesses",
    "list_categories",
    "list_products",
    "search_products",
    "update_product",
    "advance_order_status",
    "cancel_order",
    "create_order",
    "get_order",
    "list_customer_orders",
    "list_business_orders",
    "mark_customer_arrived",
    "validate_pickup",
    "get_business_for_owner",
    "get_business_or_404",
    "list_business_transactions",
    "register_business",
    "update_business",
]
