from probe.handlers import ROUTES, dispatch


def test_orders_route_is_registered():
    assert "/v2/orders" in ROUTES


def test_orders_route_requires_a_merchant_token():
    # Contract: every merchant-scoped route declares the token guard.
    assert "require_merchant_token" in ROUTES["/v2/orders"]["guards"]


def test_orders_returns_a_cursor_field():
    body = dispatch("/v2/orders", {"headers": {"authorization": "Bearer t"}})
    assert body["cursor"] is None
