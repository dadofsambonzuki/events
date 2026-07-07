from datetime import datetime, timezone
from http import HTTPStatus
from io import BytesIO

import pyqrcode  # type: ignore[import-untyped]
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
)
from fastapi.responses import StreamingResponse
from lnbits.core.crud import get_user
from lnbits.core.models import WalletTypeInfo
from lnbits.decorators import (
    require_admin_key,
    require_invoice_key,
)
from lnbits.helpers import urlsafe_short_hash
from lnbits.settings import settings
from loguru import logger
from PIL import Image, ImageDraw

from .crud import (
    create_event as create_event_crud,
    create_ticket_type,
    delete_event as delete_event_crud,
    delete_event_ticket_types,
    delete_event_tickets,
    delete_ticket,
    delete_ticket_type,
    get_basket,
    get_event,
    get_ticket,
    get_ticket_types,
    get_tickets,
    get_tickets_paginated,
    purge_unpaid_tickets,
    update_event as update_event_crud,
    update_ticket,
    update_ticket_type,
    get_events,
)
from .models import (
    BasketItem,
    BasketResponse,
    BasketTotals,
    CreateBasket,
    CreateEvent,
    CreateTicket,
    Event,
    PromoValidateRequest,
    PublicEvent,
    PublicTicket,
    Ticket,
    TicketPaymentRequest,
    TicketType,
)
from .services import (
    calculate_basket_total,
    create_basket_with_charge,
    handle_basket_payment,
    refund_tickets,
    resend_ticket_email_notification,
    send_bulk_message,
    send_bulk_ticket_emails,
)

events_api_router = APIRouter(prefix="/api/v1")
qr_api_router = APIRouter(prefix="/api/v1")


def _make_qr_png(data: str, size: int = 235, border: int = 4) -> Image.Image:
    qr = pyqrcode.create(data)
    matrix = qr.code
    modules = len(matrix)

    total_modules = modules + border * 2
    box_size = max(1, size // total_modules)
    img_size = total_modules * box_size

    img = Image.new("RGBA", (img_size, img_size), "white")
    draw = ImageDraw.Draw(img)

    for y, row in enumerate(matrix):
        for x, cell in enumerate(row):
            if cell:
                x0 = (x + border) * box_size
                y0 = (y + border) * box_size
                draw.rectangle(
                    [x0, y0, x0 + box_size - 1, y0 + box_size - 1],
                    fill="black",
                )

    if img_size != size:
        img = img.resize((size, size), Image.Resampling.NEAREST)

    return img


@events_api_router.post("/events")
async def api_create_event(
    data: CreateEvent,
    wallet: WalletTypeInfo = Depends(require_admin_key),
) -> Event:
    event = await create_event_crud(data)
    if data.amount_tickets > 0 or data.price_per_ticket > 0:
        default_tt = TicketType(
            event_id=event.id,
            name="General Admission",
            price=data.price_per_ticket,
            max_tickets=data.amount_tickets,
            available_from=data.event_start_date,
            available_to=data.closing_date,
        )
        await create_ticket_type(default_tt)
    return event


@events_api_router.put("/events/{event_id}")
async def api_update_event(
    event_id: str,
    data: CreateEvent,
    wallet: WalletTypeInfo = Depends(require_admin_key),
) -> Event:
    event = await get_event(event_id)
    if not event:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Event does not exist."
        )
    if event.wallet != wallet.wallet.id:
        raise HTTPException(
            status_code=HTTPStatus.FORBIDDEN, detail="Not your event."
        )
    event = Event(
        **{
            **event.dict(),
            **data.dict(),
            "id": event.id,
            "wallet": event.wallet,
            "time": event.time,
            "sold": event.sold,
            "canceled": event.canceled,
        }
    )
    event = await update_event_crud(event)
    return event


@events_api_router.get("/events")
async def api_events(
    all_wallets: bool = Query(False),
    wallet: WalletTypeInfo = Depends(require_invoice_key),
) -> list[Event]:
    wallet_ids = [wallet.wallet.id]
    if all_wallets:
        user = await get_user(wallet.wallet.user)
        wallet_ids = user.wallet_ids if user else []
    return await get_events(wallet_ids)


@events_api_router.get("/events/{event_id}", response_model=PublicEvent)
async def api_get_event(event_id: str) -> Event:
    event = await get_event(event_id)
    if not event:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Event does not exist."
        )
    if event.canceled:
        raise HTTPException(status_code=HTTPStatus.GONE, detail="Event is canceled.")

    await purge_unpaid_tickets(event_id)
    event = await get_event(event_id)
    if not event:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Event does not exist."
        )

    ticket_types = await get_ticket_types(event_id)
    if not ticket_types:
        raise HTTPException(status_code=HTTPStatus.GONE, detail="Event is sold out.")
    today_date = datetime.now(timezone.utc).date()
    available_types = [
        tt for tt in ticket_types
        if datetime.strptime(tt.available_from, "%Y-%m-%d").date() <= today_date
        and datetime.strptime(tt.available_to, "%Y-%m-%d").date() >= today_date
        and (tt.max_tickets == 0 or tt.sold < tt.max_tickets)
    ]
    if not available_types:
        raise HTTPException(status_code=HTTPStatus.GONE, detail="Event is sold out.")

    is_sales_closed = (
        today_date > datetime.strptime(event.closing_date, "%Y-%m-%d").date()
    )
    is_min_tickets_met = (
        event.sold >= event.extra.min_tickets if event.extra.conditional else True
    )
    if event.extra.conditional and not is_min_tickets_met and is_sales_closed:
        event.canceled = True
        await update_event_crud(event)
        await refund_tickets(event_id)
        raise HTTPException(status_code=HTTPStatus.GONE, detail="Event canceled.")

    if is_sales_closed:
        raise HTTPException(
            status_code=HTTPStatus.GONE, detail="Ticket closing date has passed."
        )

    return event


@events_api_router.put("/events/{event_id}/cancel")
async def api_event_cancel(
    event_id: str,
    wallet: WalletTypeInfo = Depends(require_admin_key),
) -> Event:
    event = await get_event(event_id)
    if not event:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Event does not exist."
        )
    if event.wallet != wallet.wallet.id:
        raise HTTPException(status_code=HTTPStatus.FORBIDDEN, detail="Not your event.")
    event.canceled = True
    event = await update_event_crud(event)
    await refund_tickets(event.id)
    return event


@events_api_router.delete("/events/{event_id}")
async def api_event_delete(
    event_id: str, wallet: WalletTypeInfo = Depends(require_admin_key)
) -> None:
    event = await get_event(event_id)
    if not event:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Event does not exist."
        )
    if event.wallet != wallet.wallet.id:
        raise HTTPException(status_code=HTTPStatus.FORBIDDEN, detail="Not your event.")
    await delete_event_tickets(event_id)
    await delete_event_ticket_types(event_id)
    await delete_event_crud(event_id)


@events_api_router.post("/events/{event_id}/email-tickets")
async def api_event_email_tickets(
    event_id: str,
    request: Request,
    wallet: WalletTypeInfo = Depends(require_admin_key),
    subject: str | None = Query(None),
    body: str | None = Query(None),
):
    event = await get_event(event_id)
    if not event:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Event does not exist."
        )
    if event.wallet != wallet.wallet.id:
        raise HTTPException(status_code=HTTPStatus.FORBIDDEN, detail="Not your event.")
    base_url = str(request.base_url).rstrip("/")
    results = await send_bulk_ticket_emails(event_id, base_url)
    return {"results": [r.dict() for r in results]}


@events_api_router.post("/events/{event_id}/email-message")
async def api_event_email_message(
    event_id: str,
    body: dict,
    wallet: WalletTypeInfo = Depends(require_admin_key),
):
    event = await get_event(event_id)
    if not event:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Event does not exist."
        )
    if event.wallet != wallet.wallet.id:
        raise HTTPException(status_code=HTTPStatus.FORBIDDEN, detail="Not your event.")
    subject = body.get("subject")
    message = body.get("message")
    if not subject or not message:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail="Both 'subject' and 'message' are required.",
        )
    results = await send_bulk_message(event_id, subject, message)
    return {"results": results}


@events_api_router.get("/ticket-types/{event_id}")
async def api_ticket_types(event_id: str) -> list[TicketType]:
    return await get_ticket_types(event_id)


@events_api_router.post("/ticket-types/{event_id}")
async def api_create_ticket_type(
    event_id: str,
    data: TicketType,
    wallet: WalletTypeInfo = Depends(require_admin_key),
) -> TicketType:
    event = await get_event(event_id)
    if not event:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Event does not exist."
        )
    if event.wallet != wallet.wallet.id:
        raise HTTPException(status_code=HTTPStatus.FORBIDDEN, detail="Not your event.")
    data.event_id = event_id
    return await create_ticket_type(data)


@events_api_router.put("/ticket-types/{event_id}/{type_id}")
async def api_update_ticket_type(
    event_id: str,
    type_id: str,
    data: TicketType,
    wallet: WalletTypeInfo = Depends(require_admin_key),
) -> TicketType:
    event = await get_event(event_id)
    if not event:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Event does not exist."
        )
    if event.wallet != wallet.wallet.id:
        raise HTTPException(status_code=HTTPStatus.FORBIDDEN, detail="Not your event.")
    data.event_id = event_id
    data.id = type_id
    return await update_ticket_type(data)


@events_api_router.delete("/ticket-types/{event_id}/{type_id}")
async def api_delete_ticket_type(
    event_id: str,
    type_id: str,
    wallet: WalletTypeInfo = Depends(require_admin_key),
) -> None:
    event = await get_event(event_id)
    if not event:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Event does not exist."
        )
    if event.wallet != wallet.wallet.id:
        raise HTTPException(status_code=HTTPStatus.FORBIDDEN, detail="Not your event.")
    await delete_ticket_type(type_id)


@events_api_router.post("/baskets/{event_id}")
async def api_create_basket(
    event_id: str,
    data: CreateBasket,
    request: Request,
) -> BasketResponse:
    event = await get_event(event_id)
    if not event:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Event does not exist."
        )
    if event.canceled:
        raise HTTPException(status_code=HTTPStatus.GONE, detail="Event is canceled.")

    from lnbits.core.crud.wallets import get_wallet as get_wallet_crud

    wallet_record = await get_wallet_crud(event.wallet)
    if not wallet_record:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Wallet does not exist."
        )

    try:
        basket, tickets, totals, charge = await create_basket_with_charge(
            event=event,
            data=data,
            wallet_inkey=wallet_record.inkey,
            base_url=str(request.base_url).rstrip("/"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc))

    payment_request = None
    if charge:
        payment_request = TicketPaymentRequest(
            payment_hash=basket.id,
            onchain_amount_sat=totals.total,
            satspay_charge_url=f"/satspay/{charge['id']}",
            basket_id=basket.id,
        )

    return BasketResponse(
        basket=basket,
        tickets=tickets,
        totals=totals,
        payment_request=payment_request,
    )


@events_api_router.get("/baskets/{basket_id}")
async def api_get_basket(basket_id: str) -> BasketResponse:
    basket = await get_basket(basket_id)
    if not basket:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Basket does not exist."
        )

    event = await get_event(basket.event_id)
    if not event:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Event does not exist."
        )

    from .crud import get_basket_tickets as get_basket_tickets_crud
    from .services import calculate_basket_total

    tickets = await get_basket_tickets_crud(basket_id)
    totals = await calculate_basket_total(
        event,
        [{"ticket_type_id": t.ticket_type_id or "", "quantity": 1}
         for t in tickets],
        basket.promo_codes,
    )

    return BasketResponse(
        basket=basket,
        tickets=tickets,
        totals=totals,
        payment_request=None,
    )


@events_api_router.post("/promo/validate/{event_id}")
async def api_validate_promo_codes(
    event_id: str, data: PromoValidateRequest
) -> BasketTotals:
    event = await get_event(event_id)
    if not event:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Event does not exist."
        )
    return await calculate_basket_total(event, data.items, data.codes)


@events_api_router.post("/baskets/{basket_id}/satspay-webhook")
async def api_basket_satspay_webhook(
    basket_id: str,
    request: Request,
):
    body = await request.json()
    charge_id = body.get("charge_id")
    logger.debug(f"SatsPay webhook for basket {basket_id}, charge {charge_id}")

    basket = await get_basket(basket_id)
    if not basket:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Basket does not exist."
        )

    event = await get_event(basket.event_id)
    if not event:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Event does not exist."
        )

    from lnbits.core.crud.wallets import get_wallet as get_wallet_crud

    wallet_record = await get_wallet_crud(event.wallet)
    if not wallet_record:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Wallet does not exist."
        )

    await handle_basket_payment(basket_id, wallet_record.inkey)
    return {"status": "ok"}


@events_api_router.get("/tickets")
async def api_tickets(
    all_wallets: bool = Query(False),
    key_info: WalletTypeInfo = Depends(require_invoice_key),
) -> list[Ticket]:
    wallet_ids = [key_info.wallet.id]
    if all_wallets:
        user = await get_user(key_info.wallet.user)
        wallet_ids = user.wallet_ids if user else []
    return await get_tickets(wallet_ids)


@events_api_router.get("/tickets/paginated")
async def api_tickets_paginated(
    all_wallets: bool = Query(False),
    key_info: WalletTypeInfo = Depends(require_invoice_key),
):
    wallet_ids = [key_info.wallet.id]
    if all_wallets:
        user = await get_user(key_info.wallet.user)
        wallet_ids = user.wallet_ids if user else []
    return await get_tickets_paginated(wallet_ids)


@events_api_router.get("/tickets/{ticket_id}", response_model=PublicTicket)
async def api_get_ticket(ticket_id: str) -> Ticket:
    ticket = await get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Ticket does not exist."
        )
    event = await get_event(ticket.event)
    if not event:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Event does not exist."
        )
    return ticket


@events_api_router.post("/tickets/{event_id}")
async def api_ticket_create_legacy(
    event_id: str, data: CreateTicket, request: Request
) -> TicketPaymentRequest:
    event = await get_event(event_id)
    if not event:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Event does not exist."
        )
    if event.canceled:
        raise HTTPException(status_code=HTTPStatus.GONE, detail="Event is canceled.")

    ticket_types = await get_ticket_types(event_id)
    if not ticket_types:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST, detail="No ticket types available."
        )

    selected_tt_id = data.ticket_type_id
    if not selected_tt_id:
        selected_tt_id = ticket_types[0].id

    basket_data = CreateBasket(
        name=data.name,
        email=data.email,
        items=[BasketItem(ticket_type_id=selected_tt_id, quantity=1)],
        promo_codes=[data.promo_code] if data.promo_code else [],
        payment_method=data.payment_method,
        fiat_provider=data.fiat_provider,
        nostr_identifier=data.nostr_identifier,
        refund_address=data.refund_address,
    )

    from lnbits.core.crud.wallets import get_wallet as get_wallet_crud

    wallet_record = await get_wallet_crud(event.wallet)
    if not wallet_record:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Wallet does not exist."
        )

    try:
        basket, tickets, totals, charge = await create_basket_with_charge(
            event=event,
            data=basket_data,
            wallet_inkey=wallet_record.inkey,
            base_url=str(request.base_url).rstrip("/"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc))

    if charge:
        return TicketPaymentRequest(
            payment_hash=basket.id,
            onchain_amount_sat=totals.total,
            satspay_charge_url=f"/satspay/{charge['id']}",
            basket_id=basket.id,
        )

    return TicketPaymentRequest(
        payment_hash=basket.id,
        basket_id=basket.id,
    )


@events_api_router.delete("/tickets/{ticket_id}")
async def api_ticket_delete(
    ticket_id: str, wallet: WalletTypeInfo = Depends(require_admin_key)
) -> None:
    ticket = await get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Ticket does not exist."
        )
    if ticket.wallet != wallet.wallet.id:
        raise HTTPException(status_code=HTTPStatus.FORBIDDEN, detail="Not your ticket.")
    await delete_ticket(ticket_id)


@events_api_router.put("/tickets/register/{ticket_id}")
async def api_event_register_ticket(ticket_id: str) -> Ticket:
    ticket = await get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Ticket does not exist."
        )
    if not ticket.paid:
        raise HTTPException(
            status_code=HTTPStatus.FORBIDDEN, detail="Ticket not paid for."
        )
    if ticket.registered:
        raise HTTPException(
            status_code=HTTPStatus.FORBIDDEN, detail="Ticket already registered."
        )
    ticket.registered = True
    ticket.reg_timestamp = datetime.now(timezone.utc)
    ticket = await update_ticket(ticket)
    return ticket


@events_api_router.post("/tickets/{ticket_id}/resend-email")
async def api_ticket_resend_email(
    ticket_id: str,
    request: Request,
    wallet: WalletTypeInfo = Depends(require_admin_key),
):
    ticket = await get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Ticket does not exist."
        )
    if ticket.wallet != wallet.wallet.id:
        raise HTTPException(status_code=HTTPStatus.FORBIDDEN, detail="Not your ticket.")
    if not ticket.paid:
        raise HTTPException(
            status_code=HTTPStatus.FORBIDDEN,
            detail="Only paid tickets can be resent by email.",
        )
    try:
        result = await resend_ticket_email_notification(
            ticket, str(request.base_url).rstrip("/")
        )
        return result.dict()
    except ValueError as exc:
        raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc))


@qr_api_router.get("/qr/{ticket_id}", response_class=StreamingResponse)
async def api_ticket_qr(ticket_id: str):
    ticket = await get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Ticket does not exist."
        )

    event = await get_event(ticket.event)
    if not event:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Event does not exist."
        )

    qr_img = _make_qr_png(f"ticket://{ticket_id}", size=157)
    output = BytesIO()
    qr_img.save(output, format="PNG")
    output.seek(0)

    return StreamingResponse(
        output,
        media_type="image/png",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )
