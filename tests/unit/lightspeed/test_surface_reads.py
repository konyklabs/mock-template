"""The three read-only surfaces: the retailer, the outlets, the payment types."""

from __future__ import annotations

from tests.unit.lightspeed.harness import Harness
from vendorfake.lightspeed.seed import constants as c

# -- the retailer ------------------------------------------------------------


def test_the_retailer_answers_the_single_record_wrapper(h: Harness) -> None:
    body = h.get(h.path("/retailer")).json()
    assert set(body) == {"data"}
    assert body["data"]["id"] == c.SEED_RETAILER_ID


def test_the_retailer_carries_its_currency_as_the_documented_object(h: Harness) -> None:
    """``RetailerCurrency`` is ``{code, symbol}`` -- not a bare string."""
    retailer = h.get(h.path("/retailer")).json()["data"]
    assert retailer["currency"] == {"code": "NZD", "symbol": "$"}
    assert retailer["domain_prefix"] == c.SEED_DOMAIN_PREFIX
    assert retailer["timezone"] == "Pacific/Auckland"


def test_the_retailers_version_is_a_string(h: Harness) -> None:
    """``Retailer.version`` is typed ``string`` where every other resource's is
    ``format: int64`` -- a real inconsistency in the vendor's own document,
    reproduced rather than corrected."""
    retailer = h.get(h.path("/retailer")).json()["data"]
    assert isinstance(retailer["version"], str)
    assert retailer["version"].isdigit()


def test_the_seeds_uncomputed_blocks_reach_the_wire(h: Harness) -> None:
    retailer = h.get(h.path("/retailer")).json()["data"]
    assert retailer["gift_cards"] == {"enabled": False, "never_enabled": True}
    assert retailer["on_account"] == {"default_limit": None}
    assert retailer["embedded_barcode_option"] == "none"


# -- outlets -----------------------------------------------------------------


def test_an_outlet_carries_every_required_member(h: Harness) -> None:
    """``Outlet`` declares nine required members."""
    outlet = h.get(h.path(f"/outlets/{c.SEED_OUTLET_MAIN_ID}")).json()["data"]
    required = {
        "id",
        "name",
        "default_tax_id",
        "currency",
        "display_prices",
        "time_zone",
        "currency_symbol",
        "attributes",
        "version",
    }
    assert required <= set(outlet)


def test_outlet_attributes_are_the_documented_key_value_rows(h: Harness) -> None:
    outlet = h.get(h.path(f"/outlets/{c.SEED_OUTLET_MAIN_ID}")).json()["data"]
    assert outlet["attributes"] == [
        {"key": "order_reference_prefix", "value": "MAIN"},
        {"key": "order_reference", "value": "1"},
    ]


def test_an_absent_optional_member_is_an_absent_key(h: Harness) -> None:
    """The second outlet's seed names no state or postcode; absence is absence,
    not an explicit null."""
    outlet = h.get(h.path(f"/outlets/{c.SEED_OUTLET_SECOND_ID}")).json()["data"]
    assert "physical_state" not in outlet
    assert "physical_postcode" not in outlet


def test_an_unknown_outlet_is_a_404_naming_the_parameter(h: Harness) -> None:
    answered = h.get(h.path("/outlets/nope"))
    assert answered.status == 404
    assert answered.json()["unit_error"]["field"] == "outlet_id"


def test_outlets_need_their_documented_scope(h: Harness) -> None:
    answered = h.get(h.path("/outlets"), headers=h.restricted_token("registers:read"))
    assert answered.status == 403
    assert answered.json()["unit_error"]["missing"] == ["outlets:read"]


# -- payment types -----------------------------------------------------------


def test_internal_payment_types_are_excluded(h: Harness) -> None:
    """The scope's own wording: ``payment_types:read`` is "Read payment types,
    **excluding internal payment types**"."""
    ids = {row["id"] for row in h.get(h.path("/payment_types")).json()["data"]}
    assert ids == {c.SEED_PAYMENT_TYPE_CASH_ID, c.SEED_PAYMENT_TYPE_CARD_ID}
    assert c.SEED_PAYMENT_TYPE_INTERNAL_ID not in ids


def test_a_payment_type_carries_its_required_members(h: Harness) -> None:
    rows = h.get(h.path("/payment_types")).json()["data"]
    cash = next(row for row in rows if row["id"] == c.SEED_PAYMENT_TYPE_CASH_ID)
    assert {"id", "name", "type_id", "version", "disabled", "internal"} <= set(cash)
    assert cash["name"] == "Cash"
    assert cash["type_id"] == 1


def test_a_config_block_is_carried_verbatim(h: Harness) -> None:
    """``PaymentType.config`` is ``additionalProperties: true`` -- "Shape varies
    by payment type" -- so it is stored and answered as the seed supplied it."""
    rows = h.get(h.path("/payment_types")).json()["data"]
    card = next(row for row in rows if row["id"] == c.SEED_PAYMENT_TYPE_CARD_ID)
    assert card["config"] == {"print": True}
    assert card["gateway"] is True


def test_the_outlet_filter_selects(h: Harness) -> None:
    """``outlet_id`` is documented as "Filters payment types by outlet"; the
    feature flag its description mentions is not readable anywhere in this API,
    so the filter is always effective here."""
    at_main = {
        row["id"] for row in h.get(h.path("/payment_types"), query={"outlet_id": c.SEED_OUTLET_MAIN_ID}).json()["data"]
    }
    assert at_main == {c.SEED_PAYMENT_TYPE_CASH_ID, c.SEED_PAYMENT_TYPE_CARD_ID}

    at_second = {
        row["id"]
        for row in h.get(h.path("/payment_types"), query={"outlet_id": c.SEED_OUTLET_SECOND_ID}).json()["data"]
    }
    # Cash names no outlets, which means every outlet; the card is scoped to
    # the main outlet only.
    assert at_second == {c.SEED_PAYMENT_TYPE_CASH_ID}


def test_the_unmodelled_filters_are_accepted_and_change_nothing(h: Harness) -> None:
    """``currency`` and ``only_lspay`` have nothing in the documented schema to
    select on -- ``PaymentType`` carries no currency member and no LSPay
    marker -- so both are accepted and recorded as unmodelled."""
    baseline = h.get(h.path("/payment_types")).json()["data"]
    filtered = h.get(h.path("/payment_types"), query={"currency": "NZD", "only_lspay": "true"}).json()["data"]
    assert filtered == baseline
