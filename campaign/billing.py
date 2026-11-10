"""Minutes ledger: the only code that changes a client's balance.

Balances are stored in seconds. The billing rule comes from the client's plan, so
changing how calls are billed never touches stored call data.
"""

import math
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Client, MinuteLedger, Plan


def billable_seconds(answered_seconds: int, increment_seconds: int = 1) -> int:
    """Answered time rounded up to the plan's billing increment."""
    if answered_seconds <= 0:
        return 0
    step = max(int(increment_seconds or 1), 1)
    return math.ceil(answered_seconds / step) * step


async def _change_balance(db: AsyncSession, client_id: str, seconds: int) -> int:
    await db.execute(update(Client).where(Client.id == client_id)
                     .values(balance_seconds=Client.balance_seconds + seconds))
    return (await db.execute(select(Client.balance_seconds).where(Client.id == client_id)
                             .execution_options(populate_existing=True))).scalar_one()


async def add_minutes(db: AsyncSession, client_id: str, minutes: int, entry_type: str, *,
                      note: Optional[str] = None, actor_id: Optional[str] = None) -> int:
    """Adds (or, with negative minutes, removes) minutes. Returns the new balance in seconds. Caller commits."""
    seconds = int(minutes) * 60
    balance = await _change_balance(db, client_id, seconds)
    db.add(MinuteLedger(client_id=client_id, entry_type=entry_type, seconds=seconds, balance_after_seconds=balance,
                        note=note, created_by=actor_id))
    return balance


async def debit_call(db: AsyncSession, client_id: str, call_id: str, answered_seconds: int) -> int:
    """Charges a finished call once. Returns the seconds charged. The balance may go slightly
    negative because a call in progress always finishes. Caller commits."""
    already = (await db.execute(select(MinuteLedger.seconds).where(MinuteLedger.call_id == call_id))).scalar()
    if already is not None:
        return -already
    row = (await db.execute(select(Plan.billing_increment_seconds, Plan.rate_per_minute_inr)
                            .join(Client, Client.plan_id == Plan.id).where(Client.id == client_id))).first()
    increment, rate = (row[0], row[1]) if row else (1, None)
    charged = billable_seconds(answered_seconds, increment)
    if charged == 0:
        return 0
    balance = await _change_balance(db, client_id, -charged)
    db.add(MinuteLedger(client_id=client_id, entry_type="call", seconds=-charged, balance_after_seconds=balance,
                        call_id=call_id, answered_seconds=answered_seconds, rate_per_minute_inr=rate))
    await db.flush()
    return charged
