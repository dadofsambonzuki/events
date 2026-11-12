"""Check-in bands: which colour to hand over, and the record of handing it over.

The two axes the door cares about: an ACCESS band is one per ticket, a ROLE band
(speaker, crew) is one per offer code — a speaker's code buys two tickets and
only one of the pair is the speaker.
"""
import asyncio
from datetime import datetime, timezone

import pytest

from events import crud
from events import models as M
from events import services as S

TODAY = datetime.now(timezone.utc).date().isoformat()
WEEKEND = "WKND"
ONEDAY = "DAY"
FRI = datetime(2026, 11, 20, 18, 0, tzinfo=timezone.utc)
SAT = datetime(2026, 11, 21, 18, 0, tzinfo=timezone.utc)


async def make_event(name="Bitfest", token="tok123"):
    ev = M.Event(
        id="EVB", wallet="w1", name=name, info="", canceled=False,
        event_start_date="2026-01-01", event_end_date="2026-12-31",
        currency="GBP", admin_email=None, allow_fiat=False, fiat_currency="GBP",
        amount_tickets=100, price_per_ticket=149, time=datetime.now(timezone.utc),
        sold=0, banner=None,
        extra=M.EventExtra(
            bands=[
                M.Band(id="white", name="White", label="Weekend", colour="#fff"),
                M.Band(id="purple", name="Purple", label="Fri 1 Day", colour="#74f"),
                M.Band(id="orange", name="Orange", label="Sat 1 Day", colour="#f80"),
                M.Band(id="yellow", name="Yellow", label="Speaker", colour="#fd0"),
            ],
            band_rules=[
                M.BandRule(band_ids=["white"], ticket_type_ids=[WEEKEND]),
                M.BandRule(band_ids=["purple"], ticket_type_ids=[ONEDAY], day="fri"),
                M.BandRule(band_ids=["orange"], ticket_type_ids=[ONEDAY], day="sat"),
                M.BandRule(
                    band_ids=["yellow"], per="redemption", promo_prefixes=["SPK-"]
                ),
            ],
            scanner_token=token,
        ),
    )
    await crud.create_event(ev)
    for tid, tname, price in (
        (WEEKEND, "Single - Weekend", 199),
        (ONEDAY, "Single - One Day Pass", 99),
    ):
        await crud.create_ticket_type(
            M.TicketType(
                id=tid, event_id="EVB", name=tname, price=price, max_tickets=500,
                available_from=TODAY, available_to="2026-12-31",
                sort_order=0, extra={},
            )
        )
    return ev


async def make_ticket(ticket_id, ticket_type_id, event="EVB", extra=None, basket=None):
    t = M.Ticket(
        id=ticket_id, wallet="w1", event=event, name="Holder", email="a@b.c",
        registered=False, paid=True, time=datetime.now(timezone.utc),
        reg_timestamp=None, ticket_type_id=ticket_type_id, basket_id=basket,
        extra=extra or M.TicketExtra(),
    )
    await crud.update_ticket(t)
    return t


def test_access_band_follows_type_and_day():
    async def run():
        ev = await make_event()
        weekend = await make_ticket("T1", WEEKEND)
        assert [b.id for b in S.resolve_bands(ev, weekend, [], FRI)[0]] == ["white"]
        assert [b.id for b in S.resolve_bands(ev, weekend, [], SAT)[0]] == ["white"]

        day = await make_ticket("T2", ONEDAY)
        # A one-day pass has no day of its own: the colour is the day it is used.
        assert [b.id for b in S.resolve_bands(ev, day, [], FRI)[0]] == ["purple"]
        assert [b.id for b in S.resolve_bands(ev, day, [], SAT)[0]] == ["orange"]
    asyncio.run(run())


def test_role_band_is_one_per_code_not_per_ticket():
    async def run():
        ev = await make_event()
        a = await make_ticket(
            "A", WEEKEND, basket="B1",
            extra=M.TicketExtra(applied_promo_code="SPK-AAAAAA"),
        )
        b = await make_ticket(
            "B", WEEKEND, basket="B1",
            extra=M.TicketExtra(applied_promo_code="SPK-AAAAAA"),
        )
        bands_a, _ = S.resolve_bands(ev, a, [b])
        assert [x.id for x in bands_a] == ["white", "yellow"], "speaker gets both"

        # Once handed over, the second ticket must not hand out another yellow.
        a.extra.bands_issued = ["white", "yellow"]
        bands_b, note = S.resolve_bands(ev, b, [a])
        assert [x.id for x in bands_b] == ["white"]
        assert "already issued" in note
    asyncio.run(run())


def test_checkin_records_the_hand_over_and_never_re_checks_in():
    async def run():
        ev = await make_event()
        await make_ticket("C1", WEEKEND)

        with pytest.raises(PermissionError):
            await S.checkin_view("C1", "wrong-token")
        with pytest.raises(ValueError):
            await S.checkin_view("NOPE", "tok123")

        first = await S.checkin_view("C1", "tok123", commit=True)
        assert first.already_checked_in is True
        assert first.ticket.extra.bands_issued == ["white"]
        assert first.checked_in_at is not None

        # An override on the confirm records what was actually handed over.
        again = await S.checkin_view("C1", "tok123", commit=True, bands=["white", "yellow"])
        assert again.ticket.extra.bands_issued == ["white"], "no re-check-in"
        assert again.bands and [b.id for b in again.bands] == ["white"]
    asyncio.run(run())


def test_house_rules_match_the_band_scheme():
    async def run():
        types = [
            M.TicketType(id="WT", event_id="E", name="Single - Weekend", price=1,
                         max_tickets=1, available_from=TODAY, available_to=TODAY,
                         sort_order=0, extra={}),
            M.TicketType(id="OD", event_id="E", name="Single - One Day Pass", price=1,
                         max_tickets=1, available_from=TODAY, available_to=TODAY,
                         sort_order=0, extra={}),
            M.TicketType(id="NME", event_id="E", name="New Music Economy Concert", price=1,
                         max_tickets=1, available_from=TODAY, available_to=TODAY,
                         sort_order=0, extra={}),
        ]
        fest = M.EventExtra()
        event = M.Event(
            id="E", wallet="w1", name="Bitfest 2026", info="", canceled=False,
            event_start_date="2026-01-01", event_end_date="2026-12-31",
            currency="GBP", admin_email=None, allow_fiat=False, fiat_currency="GBP",
            amount_tickets=1, price_per_ticket=1, time=datetime.now(timezone.utc),
            sold=0, banner=None, extra=fest,
        )
        rules = S.default_band_rules(event, types)
        pairs = [(r.band_ids, r.ticket_type_ids, r.day, r.per) for r in rules]
        assert (["white"], ["WT"], None, "ticket") in pairs
        assert (["purple"], ["OD"], "fri", "ticket") in pairs
        assert (["orange"], ["OD"], "sat", "ticket") in pairs
        assert (["red"], ["NME"], None, "ticket") in pairs
        assert (["yellow"], None, None, "redemption") in pairs

        expo = event.dict()
        expo["name"] = "Bitfest 2026 Expo"
        expo_event = M.Event(**{**expo, "extra": M.EventExtra()})
        expo_rules = S.default_band_rules(expo_event, types)
        assert (["white", "yellow"], ["WT", "OD", "NME"], None, "ticket") in [
            (r.band_ids, r.ticket_type_ids, r.day, r.per) for r in expo_rules
        ]
    asyncio.run(run())
