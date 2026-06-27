from datetime import date, datetime, timezone
from uuid import uuid4

from fastapi import Query
from lnbits.db import FilterModel
from pydantic import BaseModel, EmailStr, Field, root_validator, validator


class PromoCode(BaseModel):
    code: str
    discount_percent: float | None = None
    discount_fixed: int | None = None
    active: bool = True
    combinable: bool = True
    max_uses: int | None = None
    used_count: int = 0

    @validator("code")
    def uppercase_code(cls, v):
        return v.upper()

    @validator("discount_percent")
    def validate_discount_percent(cls, v):
        if v is not None:
            assert 0 <= v <= 100, "Discount must be between 0 and 100."
        return v

    @validator("discount_fixed")
    def validate_discount_fixed(cls, v):
        if v is not None:
            assert v >= 0, "Fixed discount must be >= 0."
        return v

    @root_validator
    def validate_discount_exclusive(cls, values):
        percent = values.get("discount_percent")
        fixed = values.get("discount_fixed")
        if percent is not None and fixed is not None:
            values["discount_fixed"] = None
        return values


class TicketType(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex[:8])
    event_id: str
    name: str = "General Admission"
    description: str = ""
    image_url: str | None = None
    price: float = Field(default=0, ge=0)
    max_tickets: int = Field(default=0, ge=0)
    sold: int = 0
    available_from: str
    available_to: str
    allow_fiat: bool = False
    sort_order: int = 0
    extra: dict = Field(default_factory=dict)


class EventExtra(BaseModel):
    promo_codes: list[PromoCode] = Field(default_factory=list)
    conditional: bool = False
    min_tickets: int = 1
    email_notifications: bool = False
    nostr_notifications: bool = False
    notification_subject: str = ""
    notification_body: str = ""
    payment_methods: list[str] = Field(default_factory=list)
    ln_wallet_id: str | None = None
    onchain_wallet_id: str | None = None


class CreateEvent(BaseModel):
    wallet: str
    name: str
    info: str
    closing_date: str
    event_start_date: str
    event_end_date: str
    currency: str = "sat"
    allow_fiat: bool = False
    fiat_currency: str = "GBP"
    admin_email: str | None = None
    amount_tickets: int = Query(default=0, ge=0)
    price_per_ticket: float = Query(default=0, ge=0)
    banner: str | None = None
    extra: EventExtra = Field(default_factory=EventExtra)


class Event(BaseModel):
    id: str
    wallet: str
    name: str
    info: str
    closing_date: str
    canceled: bool = False
    event_start_date: str
    event_end_date: str
    currency: str
    admin_email: str | None = None
    allow_fiat: bool = False
    fiat_currency: str = "GBP"
    amount_tickets: int
    price_per_ticket: float
    time: datetime
    sold: int = 0
    banner: str | None = None
    extra: EventExtra = Field(default_factory=EventExtra)


class PublicEvent(BaseModel):
    id: str
    name: str
    info: str
    closing_date: str
    canceled: bool
    event_start_date: str
    event_end_date: str
    currency: str
    admin_email: str | None = None
    allow_fiat: bool = False
    fiat_currency: str = "GBP"
    price_per_ticket: float
    banner: str | None
    extra: EventExtra = Field(default_factory=EventExtra)


class TicketExtra(BaseModel):
    applied_promo_code: str | None = None
    ticket_wave_id: str | None = None
    ticket_wave_title: str | None = None
    ticket_type_id: str | None = None
    sats_paid: int | None = None
    refund_address: str | None = None
    nostr_identifier: str | None = None
    ticket_base_url: str | None = None
    email_notification_sent: bool = False
    nostr_notification_sent: bool = False
    refunded: bool = False
    satspay_charge_id: str | None = None


class CreateTicket(BaseModel):
    name: str
    email: EmailStr
    ticket_type_id: str | None = None
    ticket_wave_id: str | None = None
    promo_code: str | None = None
    refund_address: str | None = None
    nostr_identifier: str | None = None
    payment_method: str | None = None
    fiat_provider: str | None = None


class Ticket(BaseModel):
    id: str
    wallet: str
    event: str
    name: str
    email: str
    registered: bool
    paid: bool
    time: datetime
    reg_timestamp: datetime
    ticket_type_id: str | None = None
    basket_id: str | None = None
    extra: TicketExtra = Field(default_factory=TicketExtra)


class NotificationDeliveryResult(BaseModel):
    attempted: bool = False
    sent: bool = False
    error: str | None = None


class TicketResendResult(BaseModel):
    ticket: Ticket
    email: NotificationDeliveryResult = Field(
        default_factory=NotificationDeliveryResult
    )
    nostr: NotificationDeliveryResult = Field(
        default_factory=NotificationDeliveryResult
    )


class PublicTicket(BaseModel):
    event: str
    name: str
    registered: bool
    paid: bool
    time: datetime
    reg_timestamp: datetime


class TicketPaymentRequest(BaseModel):
    payment_hash: str
    payment_request: str | None = None
    fiat_payment_request: str | None = None
    fiat_provider: str | None = None
    is_fiat: bool = False
    onchain_amount_sat: int | None = None
    satspay_charge_url: str | None = None
    basket_id: str | None = None


class TicketFilters(FilterModel):
    __search_fields__ = ["event", "name", "email", "id"]
    __sort_fields__ = [
        "time",
        "event",
        "name",
        "email",
        "registered",
        "id",
    ]

    event: str | None = None
    name: str | None = None
    email: str | None = None
    registered: bool | None = None
    paid: bool | None = None
    id: str | None = None


class BasketItem(BaseModel):
    ticket_type_id: str
    quantity: int = 1


class CreateBasket(BaseModel):
    name: str
    email: EmailStr
    items: list[BasketItem]
    promo_codes: list[str] = Field(default_factory=list)
    payment_method: str | None = None
    fiat_provider: str | None = None
    nostr_identifier: str | None = None
    refund_address: str | None = None


class BasketDiscount(BaseModel):
    code: str
    discount_percent: float | None = None
    discount_fixed: int | None = None
    amount_saved: int = 0


class BasketTotals(BaseModel):
    subtotal: int = 0
    discount: int = 0
    total: int = 0
    discounts_applied: list[BasketDiscount] = Field(default_factory=list)


class PromoValidateRequest(BaseModel):
    codes: list[str] = Field(default_factory=list)
    items: list[dict] = Field(default_factory=list)


class Basket(BaseModel):
    id: str
    event_id: str
    wallet: str
    email: str
    name: str
    promo_codes: list[str] = Field(default_factory=list)
    payment_method: str | None = None
    fiat_provider: str | None = None
    nostr_identifier: str | None = None
    refund_address: str | None = None
    satspay_charge_id: str | None = None
    paid: bool = False
    time: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class BasketResponse(BaseModel):
    basket: Basket
    tickets: list[Ticket]
    totals: BasketTotals
    payment_request: TicketPaymentRequest | None = None


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def get_active_ticket_types(
    ticket_types: list[TicketType], today: date | None = None
) -> list[TicketType]:
    current_day = today or datetime.utcnow().date()
    return [
        tt
        for tt in ticket_types
        if _parse_date(tt.available_from)
        <= current_day
        <= _parse_date(tt.available_to)
        and (tt.max_tickets == 0 or tt.sold < tt.max_tickets)
    ]


def sync_event_from_ticket_types(
    event: Event | CreateEvent, ticket_types: list[TicketType]
) -> Event | CreateEvent:
    if not ticket_types:
        return event

    event.amount_tickets = sum(tt.max_tickets for tt in ticket_types)
    event.price_per_ticket = ticket_types[0].price
    event.allow_fiat = any(tt.allow_fiat for tt in ticket_types)
    event.closing_date = max(tt.available_to for tt in ticket_types)

    return event
