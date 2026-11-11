import asyncio

from .models import Ticket

payment_listeners: dict[str, list[asyncio.Queue[Ticket]]] = {}


def register_payment_listener(payment_hash, queue: asyncio.Queue[Ticket]) -> None:
    if payment_hash not in payment_listeners:
        payment_listeners[payment_hash] = []
    payment_listeners[payment_hash].append(queue)


def deregister_payment_listener(payment_hash, queue: asyncio.Queue[Ticket]) -> None:
    if payment_hash in payment_listeners:
        payment_listeners[payment_hash].remove(queue)
        if not payment_listeners[payment_hash]:
            del payment_listeners[payment_hash]


# The reconcile sweep is the guarantee behind the satspay webhook: a delivery
# can be refused (loopback target), lost on restart, or never sent, and the
# buyer is not obliged to reopen the basket page.
RECONCILE_INTERVAL_SECONDS = 30


async def wait_for_paid_baskets():
    from lnbits.settings import settings
    from loguru import logger

    from .services import reconcile_unpaid_baskets

    # Extension tasks start before the app accepts connections, and this sweep
    # talks to satspay over loopback: sweep once the server is actually up.
    await asyncio.sleep(RECONCILE_INTERVAL_SECONDS)
    while settings.lnbits_running:
        try:
            await reconcile_unpaid_baskets()
        except Exception as exc:
            logger.warning(f"Basket reconcile sweep failed: {exc}")
        await asyncio.sleep(RECONCILE_INTERVAL_SECONDS)
