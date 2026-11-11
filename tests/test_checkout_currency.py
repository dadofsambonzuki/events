"""Checkout must survive a currency total that is not a whole number.

``TicketExtra.sats_paid`` is an integer sats figure (it is what a refund pays
back over Lightning) while ``paid_value`` is the amount actually charged in the
event's currency. The checkout used to write the currency total into BOTH, so a
GBP basket ending in pence was rejected by pydantic v2 -- "Input should be a
valid integer, got a number with a fractional part" -- and the buyer got a 400
*after* the SatsPay charge had already been created.

Under pydantic v1 the same line silently truncated 157.21 to 157, which is why
this only surfaced when the extension was ported to pydantic 2.
"""
import asyncio
from datetime import datetime, timezone

import pytest

from events import crud
from events import models as M
from events import services as S

TODAY = datetime.now(timezone.utc).date().isoformat()

FAIR = "FAIR"
PENCE = 41.79  # two of these is 83.58: a total with a fractional part


async def make_event(event_id="EVF", currency="GBP", fiat_currency="GBP"):
    ev = M.Event(
        id=event_id, wallet="w1", name="Bitfest", info="", canceled=False,
        event_start_date="2026-01-01", event_end_date="2026-12-31",
        currency=currency, admin_email=None, allow_fiat=False,
        fiat_currency=fiat_currency, amount_tickets=100, price_per_ticket=0,
        time=datetime.now(timezone.utc), sold=0, banner=None,
        extra=M.EventExtra(
            payment_methods=["fiat"], fiat_provider="stripe", ln_wallet_id="w1"
        ),
    )
    await crud.create_event(ev)
    await crud.create_ticket_type(M.TicketType(
        id=FAIR, event_id=event_id, name="Club Orange", price=PENCE,
        max_tickets=100, available_from=TODAY, available_to="2026-12-31",
        sort_order=0,
    ))
    return ev


def stub_charge():
    """Capture the SatsPay charge payload instead of calling out to it."""
    seen = {}

    async def fake(api_key, data):
        seen.clear()
        seen.update(data)
        return {"id": "chg-1", "paid": False}

    S.create_satspay_charge = fake
    S._send_basket_notifications_in_background = lambda *a, **k: None
    return seen


def basket_data(quantity=2):
    return M.CreateBasket(
        name="Buyer", email="buyer@example.test",
        items=[M.BasketItem(ticket_type_id=FAIR, quantity=quantity)],
        payment_method="fiat", fiat_provider="stripe",
    )


def test_fractional_fiat_total_issues_tickets():
    async def run():
        ev = await make_event()
        seen = stub_charge()

        basket, tickets, totals, charge = await S.create_basket_with_charge(
            ev, basket_data(), "key", "https://example.test"
        )

        assert totals.total == 83.58, f"two {PENCE} tickets total {totals.total}"
        assert charge and charge["id"] == "chg-1", "the charge was created"
        assert seen["currency_amount"] == 83.58, "charged in the event currency"
        assert len(tickets) == 2, "both tickets were issued"

        for ticket in tickets:
            assert ticket.extra.sats_paid is None, (
                "a GBP amount is not a sats figure: sats_paid must stay unset "
                f"on a fiat event (got {ticket.extra.sats_paid!r})"
            )

        # The checkout RESPONSE carries the same figure, and feeding the
        # currency total into `onchain_amount_sat` (an int) was a second 400 on
        # the way out -- one that fired after the charge and tickets existed, so
        # the buyer saw an error for an order that had been created.
        response = M.TicketPaymentRequest(
            payment_hash=basket.id,
            onchain_amount_sat=S.sats_amount(ev, totals.total),
            satspay_charge_url=f"/satspay/{charge['id']}",
            basket_id=basket.id,
        )
        assert response.onchain_amount_sat is None

        # The currency amount is recorded on activation, which is where the
        # upgrade-credit basis comes from.
        await S._activate_basket_tickets(basket, tickets, ev)
        for ticket in tickets:
            refreshed = await crud.get_ticket(ticket.id)
            assert refreshed.paid is True, "the ticket is paid"
            assert refreshed.extra.paid_value == PENCE, (
                f"paid_value records the {PENCE} charged (got "
                f"{refreshed.extra.paid_value!r})"
            )

    asyncio.run(run())


def test_whole_number_fiat_total_still_issues_tickets():
    """The case that already worked must keep working."""

    async def run():
        ev = await make_event()
        await crud.create_ticket_type(M.TicketType(
            id="EVEN", event_id="EVF", name="Weekend", price=199, max_tickets=100,
            available_from=TODAY, available_to="2026-12-31", sort_order=1,
        ))
        stub_charge()
        data = M.CreateBasket(
            name="Buyer", email="buyer@example.test",
            items=[M.BasketItem(ticket_type_id="EVEN", quantity=1)],
            payment_method="fiat", fiat_provider="stripe",
        )
        _basket, tickets, totals, charge = await S.create_basket_with_charge(
            ev, data, "key", "https://example.test"
        )
        assert totals.total == 199
        assert len(tickets) == 1 and tickets[0].extra.sats_paid is None

    asyncio.run(run())


def test_sats_event_records_sats_paid():
    """A sats-priced event still fills the sats field -- it is what a Lightning
    refund pays back."""

    async def run():
        ev = await make_event(event_id="EVS", currency="sats", fiat_currency="")
        await crud.create_ticket_type(M.TicketType(
            id="SATS", event_id="EVS", name="Sats ticket", price=5000,
            max_tickets=100, available_from=TODAY, available_to="2026-12-31",
            sort_order=0,
        ))
        seen = stub_charge()
        data = M.CreateBasket(
            name="Buyer", email="buyer@example.test",
            items=[M.BasketItem(ticket_type_id="SATS", quantity=2)],
            payment_method="ln",
        )
        _basket, tickets, totals, _charge = await S.create_basket_with_charge(
            ev, data, "key", "https://example.test"
        )
        assert totals.total == 10000, f"sats total is {totals.total}"
        assert seen["amount"] == 10000, "the charge is denominated in sats"
        assert [t.extra.sats_paid for t in tickets] == [10000, 10000]
        assert S.sats_amount(ev, totals.total) == 10000, "sats stay sats"

    asyncio.run(run())


def test_pence_fixed_discount_is_accepted_and_applied():
    """A fixed discount is an amount in the event's currency, so a GBP offer of
    £12.50 must save with its pence intact. As an int field, pydantic v2
    rejected the admin's save outright (and v1 truncated 12.50 to 12)."""

    async def run():
        rule = M.PromoCode(code="TENOFF", discount_fixed=12.5)
        assert rule.discount_fixed == 12.5, "the pence survive parsing"

        ev = await make_event(event_id="EVD")
        ev.extra.promo_codes = [rule]
        await crud.update_event(ev)

        totals = await S.calculate_basket_total(
            ev, [{"ticket_type_id": FAIR, "quantity": 1}], ["TENOFF"]
        )
        assert totals.discount == 12.5, f"discount is {totals.discount}"
        assert totals.total == round(PENCE - 12.5, 2), f"total is {totals.total}"

    asyncio.run(run())


def test_sats_charge_amount_is_whole_sats():
    """A sats checkout must hand SatsPay a whole-satoshi INT amount, even with a
    percentage discount in play: `amount` is an int field, so a float total is
    rejected before any charge exists. `calculate_basket_total` floors a sats
    total already, and the payload is rounded as well so the boundary cannot
    regress."""

    async def run():
        ev = await make_event(event_id="EVSP", currency="sats", fiat_currency="")
        await crud.create_ticket_type(M.TicketType(
            id="ODD", event_id="EVSP", name="Odd sats", price=1001,
            max_tickets=100, available_from=TODAY, available_to="2026-12-31",
            sort_order=0,
        ))
        ev.extra.promo_codes = [M.PromoCode(code="TEN", discount_percent=10)]
        await crud.update_event(ev)

        seen = stub_charge()
        data = M.CreateBasket(
            name="Buyer", email="buyer@example.test",
            items=[M.BasketItem(ticket_type_id="ODD", quantity=1)],
            promo_codes=["TEN"], payment_method="ln",
        )
        _basket, tickets, totals, _charge = await S.create_basket_with_charge(
            ev, data, "key", "https://example.test"
        )
        assert totals.total == 900, f"10% off 1001 floors to {totals.total}"
        assert seen["amount"] == 900, f"whole sats, got {seen['amount']!r}"
        assert isinstance(seen["amount"], int), "SatsPay requires an int amount"
        assert tickets[0].extra.sats_paid == 900, "the ticket records whole sats"

    asyncio.run(run())


def test_paid_charge_without_tickets_is_refused():
    """The money-in-nothing-issued guard. A charge can settle for a basket whose
    tickets were never written (the checkout died after creating the charge), and
    activating that basket would mark it paid, issue nothing and leave no trace
    that a buyer is owed a ticket."""

    async def run():
        await make_event(event_id="EVG")
        basket = M.Basket(
            id="B-ORPHAN", event_id="EVG", wallet="w1",
            email="buyer@example.test", name="Buyer", total=83.58,
            satspay_charge_id="chg-orphan",
        )
        await crud.create_basket(basket)
        stub_charge()

        async def paid_charge(api_key, charge_id):
            return {"id": charge_id, "paid": True}

        S.get_satspay_charge = paid_charge

        with pytest.raises(ValueError, match="has no tickets"):
            await S.handle_basket_payment("B-ORPHAN", "key")
        assert (await crud.get_basket("B-ORPHAN")).paid is False, (
            "a ticketless paid basket must stay unpaid, not be quietly closed"
        )

        # Once the tickets exist, the same call activates as before.
        await crud.create_ticket(
            "T-ORPHAN", "w1", "EVG", "Buyer", "buyer@example.test",
            {"ticket_type_id": FAIR}, FAIR, "B-ORPHAN",
        )
        charge = await S.handle_basket_payment("B-ORPHAN", "key")
        assert charge and charge["paid"] is True
        assert (await crud.get_basket("B-ORPHAN")).paid is True
        assert (await crud.get_ticket("T-ORPHAN")).paid is True

    asyncio.run(run())
