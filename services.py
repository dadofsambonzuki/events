from __future__ import annotations

import smtplib
from asyncio.tasks import create_task
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape
from typing import Any

import httpx
from lnbits.core.models.users import UserNotifications
from lnbits.core.services.notifications import send_user_notification
from lnbits.helpers import is_valid_email_address, urlsafe_short_hash
from lnbits.settings import settings
from lnurl import execute
from loguru import logger

from .crud import (
    create_basket,
    create_ticket,
    get_basket,
    get_basket_tickets,
    get_event,
    get_event_tickets,
    get_ticket,
    get_ticket_type,
    get_ticket_types,
    purge_unpaid_tickets,
    update_basket,
    update_event,
    update_ticket,
    update_ticket_type,
)
from .models import (
    Basket,
    BasketDiscount,
    BasketTotals,
    CreateBasket,
    Event,
    EventExtra,
    NotificationDeliveryResult,
    Ticket,
    TicketResendResult,
    TicketType,
)


async def create_satspay_charge(api_key: str, data: dict) -> dict[str, Any]:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            url=f"http://{settings.host}:{settings.port}/satspay/api/v1/charge",
            headers={"X-API-KEY": api_key},
            json=data,
        )
        resp.raise_for_status()
        return resp.json()


async def get_satspay_charge(api_key: str, charge_id: str) -> dict[str, Any]:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            url=f"http://{settings.host}:{settings.port}/satspay/api/v1/charge/{charge_id}",
            headers={"X-API-KEY": api_key},
        )
        resp.raise_for_status()
        return resp.json()


def _effective_discount(promo, price: int) -> int:
    discount = 0
    if promo.discount_percent is not None:
        discount = int(price * promo.discount_percent / 100)
    if promo.discount_fixed is not None:
        discount += promo.discount_fixed
    return min(discount, price)


def _apply_promo_codes(
    promos: list, price: int
) -> tuple[int, list[BasketDiscount]]:
    discounts_applied: list[BasketDiscount] = []
    current_price = price
    total_discount = 0

    for promo in promos:
        if not promo.active:
            continue
        if promo.max_uses is not None and promo.used_count >= promo.max_uses:
            continue
        discount_amount = _effective_discount(promo, current_price)
        if discount_amount <= 0:
            continue
        total_discount += discount_amount
        current_price -= discount_amount
        discounts_applied.append(
            BasketDiscount(
                code=promo.code,
                discount_percent=promo.discount_percent,
                discount_fixed=promo.discount_fixed,
                amount_saved=discount_amount,
            )
        )
        if not promo.combinable:
            break

    return total_discount, discounts_applied


async def _increment_promo_uses(event: Event, promo_codes: list[BasketDiscount]) -> None:
    if not promo_codes:
        return
    applied_codes = {d.code for d in promo_codes}
    modified = False
    for promo in event.extra.promo_codes:
        if promo.code in applied_codes:
            promo.used_count += 1
            modified = True
    if modified:
        await update_event(event)


async def calculate_basket_total(
    event: Event, items: list[dict], promo_codes: list[str]
) -> BasketTotals:
    ticket_types = await get_ticket_types(event.id)
    types_by_id = {tt.id: tt for tt in ticket_types}

    subtotal = 0
    for item in items:
        tt = types_by_id.get(item["ticket_type_id"])
        if not tt:
            continue
        subtotal += int(tt.price) * item.get("quantity", 1)

    promos = [
        p
        for p in event.extra.promo_codes
        if p.code in [c.upper() for c in promo_codes]
    ]
    promos.sort(
        key=lambda p: _effective_discount(p, subtotal),
        reverse=True,
    )

    total_discount, discounts_applied = _apply_promo_codes(promos, subtotal)

    return BasketTotals(
        subtotal=subtotal,
        discount=total_discount,
        total=max(0, subtotal - total_discount),
        discounts_applied=discounts_applied,
    )


async def create_basket_with_charge(
    event: Event,
    data: CreateBasket,
    wallet_inkey: str,
    base_url: str,
) -> tuple[Basket, list[Ticket], BasketTotals, dict | None]:
    today = __import__("datetime").datetime.utcnow().date()
    from .models import _parse_date

    ticket_types = await get_ticket_types(event.id)
    active_types = [
        tt for tt in ticket_types
        if _parse_date(tt.available_from) <= today <= _parse_date(tt.available_to)
        and (tt.max_tickets == 0 or tt.sold < tt.max_tickets)
    ]
    active_ids = {tt.id for tt in active_types}

    for item in data.items:
        if item.ticket_type_id not in active_ids:
            raise ValueError(f"Ticket type {item.ticket_type_id} is not available.")
        tt = next(tt for tt in active_types if tt.id == item.ticket_type_id)
        remaining = tt.max_tickets - tt.sold if tt.max_tickets > 0 else float("inf")
        if item.quantity > remaining:
            raise ValueError(
                f"Only {remaining} tickets available for '{tt.name}'."
            )

    totals = await calculate_basket_total(
        event,
        [{"ticket_type_id": i.ticket_type_id, "quantity": i.quantity} for i in data.items],
        data.promo_codes,
    )

    basket_id = urlsafe_short_hash()
    promo_code_strings = [c.upper() for c in data.promo_codes]

    basket = Basket(
        id=basket_id,
        event_id=event.id,
        wallet=event.wallet,
        email=data.email,
        name=data.name,
        promo_codes=promo_code_strings,
        payment_method=data.payment_method,
        fiat_provider=data.fiat_provider,
        nostr_identifier=data.nostr_identifier,
        refund_address=data.refund_address,
    )

    charge = None
    tickets: list[Ticket] = []

    if totals.total > 0 and data.payment_method == "onchain":
        internal_base = f"http://{settings.host}:{settings.port}"
        webhook_url = f"{internal_base}/events/api/v1/baskets/webhook"
        complete_url = f"{base_url}/events/basket/{basket_id}"

        charge = await create_satspay_charge(
            api_key=wallet_inkey,
            data={
                "amount": totals.total,
                "description": f"Tickets for {event.name}",
                "name": data.name,
                "webhook": webhook_url,
                "completelink": complete_url,
                "completelinktext": "View your tickets",
                "time": 1440,
            },
        )
        basket.satspay_charge_id = charge["id"]

    await create_basket_from_model(basket)

    for item in data.items:
        tt = next(tt for tt in ticket_types if tt.id == item.ticket_type_id)
        for _ in range(item.quantity):
            ticket_id = urlsafe_short_hash()
            ticket = await create_ticket(
                payment_hash=ticket_id,
                wallet=event.wallet,
                event=event.id,
                name=data.name,
                email=data.email,
                extra={
                    "ticket_type_id": tt.id,
                    "ticket_wave_title": tt.name,
                    "refund_address": data.refund_address,
                    "nostr_identifier": data.nostr_identifier,
                    "ticket_base_url": base_url,
                    "sats_paid": totals.total,
                },
                ticket_type_id=tt.id,
                basket_id=basket_id,
            )
            tickets.append(ticket)

    if totals.total == 0:
        await _activate_basket_tickets(basket, tickets, event)

    return basket, tickets, totals, charge


async def create_basket_from_model(basket: Basket) -> Basket:
    return await create_basket(basket)


async def _activate_basket_tickets(
    basket: Basket, tickets: list[Ticket], event: Event
) -> None:
    for ticket in tickets:
        await set_ticket_paid(ticket)
    basket.paid = True
    await update_basket(basket)
    await _increment_promo_uses_from_basket(event, basket)
    for ticket in tickets:
        send_ticket_notification_in_background(ticket)
    if event.admin_email:
        create_task(_send_admin_sale_notification(event, basket, tickets))


async def handle_basket_payment(
    basket_id: str, wallet_inkey: str
) -> dict | None:
    basket = await get_basket(basket_id)
    if not basket:
        logger.warning(f"Basket {basket_id} not found.")
        return None
    if basket.paid:
        logger.warning(f"Basket {basket_id} already paid.")
        return None
    if not basket.satspay_charge_id:
        raise ValueError("Basket has no SatsPay charge.")

    charge = await get_satspay_charge(wallet_inkey, basket.satspay_charge_id)
    if not charge.get("paid"):
        logger.warning(f"Charge {basket.satspay_charge_id} not paid.")
        return charge

    event = await get_event(basket.event_id)
    if not event:
        raise ValueError("Event not found for basket.")

    tickets = await get_basket_tickets(basket_id)
    await _activate_basket_tickets(basket, tickets, event)

    return charge


async def _send_admin_sale_notification(
    event: Event, basket: Basket, tickets: list[Ticket]
) -> None:
    if not event.admin_email or not settings.lnbits_email_notifications_enabled:
        return

    ticket_details = "\n".join(
        f"- {t.name} ({t.email}) — {t.id}" for t in tickets
    )
    message = (
        f"New ticket sale for '{event.name}'\n\n"
        f"Basket: {basket.id}\n"
        f"Buyer: {basket.name} ({basket.email})\n"
        f"Tickets: {len(tickets)}\n\n"
        f"{ticket_details}"
    )
    subject = f"New sale: {len(tickets)} ticket(s) for '{event.name}'"

    try:
        await _send_ticket_email_notification(
            [event.admin_email], message, subject
        )
    except Exception as exc:
        logger.warning(f"Failed to send admin notification: {exc}")


async def _increment_promo_uses_from_basket(
    event: Event, basket: Basket
) -> None:
    if not basket.promo_codes:
        return
    applied_codes = set(basket.promo_codes)
    modified = False
    for promo in event.extra.promo_codes:
        if promo.code in applied_codes:
            promo.used_count += 1
            modified = True
    if modified:
        await update_event(event)


async def set_ticket_paid(ticket: Ticket) -> Ticket:
    if ticket.paid:
        return ticket

    ticket.paid = True
    await update_ticket(ticket)

    event = await get_event(ticket.event)
    assert event, "Couldn't get event from ticket being paid"
    event.sold += 1

    if ticket.ticket_type_id:
        ticket_type = await get_ticket_type(ticket.ticket_type_id)
        if ticket_type:
            ticket_type.sold += 1
            await update_ticket_type(ticket_type)

    await update_event(event)

    return ticket


async def send_bulk_ticket_emails(
    event_id: str, base_url: str
) -> list[TicketResendResult]:
    tickets = await get_event_tickets(event_id)
    results: list[TicketResendResult] = []
    for ticket in tickets:
        if not ticket.paid:
            continue
        try:
            result = await resend_ticket_email_notification(
                ticket, base_url.rstrip("/")
            )
            results.append(result)
        except Exception as exc:
            logger.warning(f"Failed to resend ticket {ticket.id}: {exc}")
            results.append(
                TicketResendResult(
                    ticket=ticket,
                    email=NotificationDeliveryResult(
                        attempted=True, sent=False, error=str(exc)
                    ),
                )
            )
    return results


async def send_bulk_message(
    event_id: str, subject: str, message: str
) -> list[dict]:
    tickets = await get_event_tickets(event_id)
    results: list[dict] = []
    for ticket in tickets:
        if not ticket.email or not ticket.paid:
            continue
        try:
            await _send_ticket_email_notification(
                [ticket.email], message, subject
            )
            results.append({"ticket_id": ticket.id, "sent": True})
        except Exception as exc:
            logger.warning(f"Failed to send message for ticket {ticket.id}: {exc}")
            results.append(
                {"ticket_id": ticket.id, "sent": False, "error": str(exc)}
            )
    return results


def send_ticket_notification_in_background(ticket: Ticket) -> None:
    create_task(_send_ticket_notification(ticket))


async def _send_ticket_notification(ticket: Ticket) -> None:
    event = await get_event(ticket.event)
    if not event:
        logger.warning(f"Event {ticket.event} not found for ticket notification.")
        return

    await _deliver_ticket_notifications(ticket, event)


async def resend_ticket_email_notification(
    ticket: Ticket, base_url: str | None = None
) -> TicketResendResult:
    event = await get_event(ticket.event)
    if not event:
        raise ValueError("Event does not exist.")
    if not settings.lnbits_email_notifications_enabled:
        raise ValueError("Email notifications are not enabled.")
    if not ticket.email:
        raise ValueError("Ticket does not have an email address.")
    if base_url:
        ticket.extra.ticket_base_url = base_url.rstrip("/")

    return await _deliver_ticket_notifications(ticket, event)


def _ticket_notification_message(ticket: Ticket, event: Event) -> tuple[str, str]:
    ticket_url = _ticket_url(ticket)
    subject = (
        event.extra.notification_subject.strip()
        or f"Your ticket for '{event.name}' is ready"
    )
    body = (
        event.extra.notification_body.strip()
        or f"Your ticket for '{event.name}' is ready."
    )

    return subject, f"{body}\n\nOpen it here: {ticket_url}"


def _ticket_delivery_message(ticket: Ticket, event: Event, base_message: str) -> str:
    ticket_image_url = _ticket_image_url(ticket, event)
    if not ticket_image_url:
        return base_message

    return f"{base_message}\n\nTicket image: {ticket_image_url}"


def _ticket_email_html_message(ticket: Ticket, event: Event, base_message: str) -> str:
    text_message = _ticket_delivery_message(ticket, event, base_message)
    html_message = f"<p>{escape(text_message).replace(chr(10), '<br />')}</p>"
    ticket_image_url = _ticket_image_url(ticket, event)
    if not ticket_image_url:
        return html_message

    return (
        f"{html_message}"
        f'<p><img src="{escape(ticket_image_url, quote=True)}" alt="Ticket image" '
        'style="max-width: 200px; height: auto;" /></p>'
    )


def _ticket_notification_payload(ticket: Ticket, event: Event) -> tuple[str, str, str]:
    subject, base_message = _ticket_notification_message(ticket, event)
    text_message = _ticket_delivery_message(ticket, event, base_message)
    html_message = _ticket_email_html_message(ticket, event, base_message)
    return subject, text_message, html_message


def _supports_nostr_delivery(identifier: str | None) -> bool:
    return bool(identifier and "@" in identifier)


async def _deliver_ticket_notifications(
    ticket: Ticket, event: Event
) -> TicketResendResult:
    subject, text_message, html_message = _ticket_notification_payload(ticket, event)
    updated = False
    result = TicketResendResult(
        ticket=ticket,
        email=NotificationDeliveryResult(
            attempted=bool(
                event.extra.email_notifications
                and settings.lnbits_email_notifications_enabled
                and ticket.email
            )
        ),
        nostr=NotificationDeliveryResult(
            attempted=bool(
                event.extra.nostr_notifications
                and settings.is_nostr_notifications_configured()
                and ticket.extra.nostr_identifier
            )
        ),
    )

    if result.email.attempted:
        try:
            await _send_ticket_email_notification(
                [ticket.email], text_message, subject, html_message
            )
            ticket.extra.email_notification_sent = True
            result.email.sent = True
            updated = True
        except Exception as exc:
            logger.warning(f"Failed to email ticket {ticket.id}: {exc}")
            result.email.error = str(exc)

    if result.nostr.attempted and not _supports_nostr_delivery(
        ticket.extra.nostr_identifier
    ):
        result.nostr.error = "Only NIP-05 Nostr identifiers are supported."
    elif result.nostr.attempted:
        try:
            identifier = ticket.extra.nostr_identifier
            assert identifier is not None
            await _send_nostr_ticket_notification(identifier, text_message)
            ticket.extra.nostr_notification_sent = True
            result.nostr.sent = True
            updated = True
        except Exception as exc:
            logger.warning(f"Failed to send nostr DM for ticket {ticket.id}: {exc}")
            result.nostr.error = str(exc)

    if updated:
        result.ticket = await update_ticket(ticket)
    return result


async def _send_nostr_ticket_notification(identifier: str, message: str) -> None:
    await send_user_notification(
        UserNotifications(nostr_identifier=identifier),
        message,
        "text_message",
    )


async def _send_ticket_email_notification(
    to_emails: list[str],
    message: str,
    subject: str,
    html_message: str | None = None,
) -> None:
    if not settings.lnbits_email_notifications_enabled:
        raise ValueError("Email notifications are disabled")
    if not is_valid_email_address(settings.lnbits_email_notifications_email):
        raise ValueError(
            f"Invalid from email address: {settings.lnbits_email_notifications_email}"
        )
    if not to_emails:
        raise ValueError("No email addresses provided")
    for email in to_emails:
        if not is_valid_email_address(email):
            raise ValueError(f"Invalid email address: {email}")

    msg = MIMEMultipart("alternative")
    msg["From"] = settings.lnbits_email_notifications_email
    msg["To"] = ", ".join(to_emails)
    msg["Subject"] = subject
    msg.attach(MIMEText(message, "plain"))
    if html_message:
        msg.attach(MIMEText(html_message, "html"))

    username = (
        settings.lnbits_email_notifications_username
        or settings.lnbits_email_notifications_email
    )
    with smtplib.SMTP(
        settings.lnbits_email_notifications_server,
        settings.lnbits_email_notifications_port,
    ) as smtp_server:
        smtp_server.starttls()
        smtp_server.login(username, settings.lnbits_email_notifications_password)
        smtp_server.sendmail(
            settings.lnbits_email_notifications_email,
            to_emails,
            msg.as_string(),
        )


def _ticket_url(ticket: Ticket) -> str:
    base_url = (ticket.extra.ticket_base_url or settings.lnbits_baseurl).rstrip("/")
    return f"{base_url}/events/ticket/{ticket.id}"


def _ticket_image_url(ticket: Ticket, event: Event) -> str | None:
    base_url = (ticket.extra.ticket_base_url or settings.lnbits_baseurl).rstrip("/")
    return f"{base_url}/events/api/v1/qr/{ticket.id}"


async def refund_tickets(event_id: str):
    await purge_unpaid_tickets(event_id)
    tickets = await get_event_tickets(event_id)

    if not tickets:
        return

    for ticket in tickets:
        if ticket.extra.refunded:
            continue
        if ticket.paid and ticket.extra.refund_address and ticket.extra.sats_paid:
            try:
                res = await execute(
                    ticket.extra.refund_address, str(ticket.extra.sats_paid)
                )
                if res:
                    ticket.extra.refunded = True
                    await update_ticket(ticket)
            except Exception as e:
                logger.error(f"Error refunding ticket {ticket.id}: {e}")
