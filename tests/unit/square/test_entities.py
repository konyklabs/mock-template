"""Stored shapes: the absence rule, and the round trip through the store."""

from __future__ import annotations

from vendorfake.square.entities import (
    COL,
    AuthorizationCodeEntity,
    CatalogObjectEntity,
    LocationEntity,
    MerchantEntity,
    Money,
    OrderEntity,
    OrderLineItem,
    Tender,
    TokenEntity,
)


def test_the_collection_names() -> None:
    assert COL.names() == ("merchants", "locations", "catalog_objects", "orders", "authorization_codes", "tokens")


def test_an_unset_optional_emits_no_key() -> None:
    """`None` is never stored to mean absent: the entity digest hashes stored
    fields and the journal's `changed` list compares present against absent, so
    a null-valued key is a different entity from one without the key."""
    entity = OrderEntity(id="CAIS1", location_id="L1", merchant_id="M1", currency="USD").to_entity()
    for absent in ("reference_id", "customer_id", "source_name", "ticket_name", "closed_at", "metadata"):
        assert absent not in entity
    assert None not in entity.values()


def test_created_at_is_omitted_so_the_store_supplies_it() -> None:
    entity = OrderEntity(id="CAIS1", location_id="L1", merchant_id="M1", currency="USD").to_entity()
    assert "created_at" not in entity
    assert "updated_at" not in entity
    assert entity["version"] == 1

    seeded = OrderEntity(
        id="CAIS1",
        location_id="L1",
        merchant_id="M1",
        currency="USD",
        version=4,
        created_at="2026-01-01T00:00:00.000Z",
    ).to_entity()
    assert seeded["created_at"] == "2026-01-01T00:00:00.000Z"
    assert seeded["version"] == 4


def test_an_order_round_trips() -> None:
    order = OrderEntity(
        id="CAIS1",
        location_id="L1",
        merchant_id="M1",
        currency="USD",
        state="OPEN",
        reference_id="ref-1",
        line_items=(
            OrderLineItem(uid="u1", quantity="2", base_price_money=Money(550, "USD"), name="Coffee", note="hot"),
        ),
        tenders=(
            Tender(
                id="t1",
                location_id="L1",
                transaction_id="x1",
                created_at="2026-01-01T00:00:00.000Z",
                amount_money=Money(1100, "USD"),
                type="CARD",
                payment_id="p1",
            ),
        ),
        metadata={"channel": "kiosk"},
        version=3,
    )
    assert OrderEntity.from_entity(order.to_entity()) == order


def test_reading_an_order_the_store_only_partly_filled() -> None:
    """A reader is tolerant on type and strict on presence: entities here are
    produced by this package, so raising from inside a projection would report
    the wrong culprit."""
    order = OrderEntity.from_entity({"id": "CAIS1"})
    assert order.id == "CAIS1"
    assert order.currency == "USD"
    assert order.state == "OPEN"
    assert order.line_items == ()
    assert order.tenders == ()
    assert order.reference_id is None


def test_the_other_entities_round_trip() -> None:
    merchant = MerchantEntity(id="M1", business_name="Bakery")
    assert MerchantEntity.from_entity(merchant.to_entity()) == merchant

    location = LocationEntity(id="L1", merchant_id="M1", name="Main", business_name="Bakery")
    assert LocationEntity.from_entity(location.to_entity()) == location
    assert "address" not in location.to_entity()

    variation = CatalogObjectEntity(
        id="V1",
        object_type="ITEM_VARIATION",
        item_id="I1",
        variation_name="Large",
        pricing_type="FIXED_PRICING",
        price_money=Money(550, "USD"),
    )
    assert CatalogObjectEntity.from_entity(variation.to_entity()) == variation
    assert variation.is_variation

    code = AuthorizationCodeEntity(
        id="sq0cgb-x", client_id="app", merchant_id="M1", expires_at="2026-01-01T00:05:00Z", scopes=("ORDERS_READ",)
    )
    assert AuthorizationCodeEntity.from_entity(code.to_entity()) == code
    assert "used_at" not in code.to_entity()

    token = TokenEntity(
        id="tok_1",
        access_token="EAAA",
        refresh_token="EQAA",
        client_id="app",
        merchant_id="M1",
        expires_at="2026-02-01T00:00:00Z",
        scopes=("ORDERS_READ",),
    )
    assert TokenEntity.from_entity(token.to_entity()) == token
    assert token.active
    assert "revoked_at" not in token.to_entity()
    assert "superseded_at" not in token.to_entity()


def test_a_superseded_or_revoked_token_is_not_active() -> None:
    """Supersession is what keeps the refresh-token lookup single-valued once
    code-flow refresh stops revoking the previous access token."""
    base = TokenEntity(
        id="tok_1",
        access_token="EAAA",
        refresh_token="EQAA",
        client_id="app",
        merchant_id="M1",
        expires_at="2026-02-01T00:00:00Z",
    )
    assert base.active
    assert not TokenEntity.from_entity({**base.to_entity(), "superseded_at": "2026-01-02T00:00:00Z"}).active
    assert not TokenEntity.from_entity({**base.to_entity(), "revoked_at": "2026-01-02T00:00:00Z"}).active
