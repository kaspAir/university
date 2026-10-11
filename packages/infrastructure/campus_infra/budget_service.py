"""Budget tree with row locks (chapter 16, T03, A05).

Allocation model: an account's free amount = limit - sum(child limits) - consumed direct reservations.
A child limit is a covered allocation of its parent; a reservation on a child only consumes the child.
All operations lock the account chain root -> target with SELECT ... FOR UPDATE in that fixed order.
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from campus_domain.budget import available_minor, check_child_limit, check_reservation
from campus_domain.errors import CampusError, ErrorCode
from campus_domain.money import Money

from .ids import new_id
from .models import BudgetAccount, Reservation

CONSUMING_STATES = ("reserved", "open_unknown", "settled")


def _chain(session: Session, account_id: str) -> list[BudgetAccount]:
    """Return the chain root..account, locked FOR UPDATE in root-first order."""
    ids: list[str] = []
    current = session.get(BudgetAccount, account_id)
    if current is None:
        raise CampusError(ErrorCode.NOT_FOUND, "Budgetkonto nicht gefunden", blocked_action="budget.reserve")
    while current is not None:
        ids.append(current.id)
        current = session.get(BudgetAccount, current.parent_id) if current.parent_id else None
    ids.reverse()
    locked = session.execute(
        select(BudgetAccount).where(BudgetAccount.id.in_(ids)).order_by(BudgetAccount.depth).with_for_update()
    ).scalars().all()
    by_id = {a.id: a for a in locked}
    return [by_id[i] for i in ids]


def consumed_minor(session: Session, account: BudgetAccount) -> int:
    """Direct reservations (settled amount where settled, reserved amount otherwise) plus child limits."""
    rows = session.execute(
        select(Reservation.state, Reservation.amount_minor, Reservation.settled_minor).where(
            Reservation.account_id == account.id, Reservation.state.in_(CONSUMING_STATES)
        )
    ).all()
    total = 0
    for state, amount, settled in rows:
        total += settled if (state == "settled" and settled is not None) else amount
    children = session.execute(
        select(func.coalesce(func.sum(BudgetAccount.limit_minor), 0)).where(BudgetAccount.parent_id == account.id)
    ).scalar_one()
    return int(total) + int(children)


def free_minor(session: Session, account: BudgetAccount) -> int:
    return available_minor(account.limit_minor, consumed_minor(session, account), 0)


def reserve(session: Session, *, account_id: str, amount: Money, task_id: str | None, purpose: str) -> Reservation:
    """Atomically reserve on one account. Caller owns the transaction."""
    chain = _chain(session, account_id)
    account = chain[-1]
    if account.currency != amount.currency:
        raise CampusError(ErrorCode.VALIDATION_ERROR, "Währung der Reservierung passt nicht zum Konto")
    check_reservation(account.id, free_minor(session, account), amount.amount_minor)
    reservation = Reservation(
        id=new_id("RES"),
        workspace_id=account.workspace_id,
        account_id=account.id,
        task_id=task_id,
        purpose=purpose,
        amount_minor=amount.amount_minor,
        currency=amount.currency,
        state="reserved",
    )
    session.add(reservation)
    session.flush()
    return reservation


def create_child(
    session: Session,
    *,
    parent_id: str,
    name: str,
    limit: Money,
    protected_kind: str | None = None,
    mandate_version_id: str | None = None,
) -> BudgetAccount:
    chain = _chain(session, parent_id)
    parent = chain[-1]
    if parent.currency != limit.currency:
        raise CampusError(ErrorCode.VALIDATION_ERROR, "Währung des Kindbudgets passt nicht zum Elternkonto")
    check_child_limit(parent.id, free_minor(session, parent), limit.amount_minor)
    child = BudgetAccount(
        id=new_id("B"),
        workspace_id=parent.workspace_id,
        parent_id=parent.id,
        mandate_version_id=mandate_version_id or parent.mandate_version_id,
        name=name,
        limit_minor=limit.amount_minor,
        currency=limit.currency,
        protected_kind=protected_kind,
        depth=parent.depth + 1,
    )
    session.add(child)
    session.flush()
    return child


def settle(session: Session, *, reservation_id: str, actual: Money | None) -> Reservation:
    """Settlement replaces the reservation by the actual cost; None keeps it open as unknown (A14)."""
    reservation = session.get(Reservation, reservation_id, with_for_update=True)
    if reservation is None:
        raise CampusError(ErrorCode.NOT_FOUND, "Reservierung nicht gefunden")
    if actual is None:
        reservation.state = "open_unknown"
    else:
        if actual.currency != reservation.currency:
            raise CampusError(ErrorCode.VALIDATION_ERROR, "Währung der Abrechnung passt nicht zur Reservierung")
        reservation.state = "settled"
        reservation.settled_minor = actual.amount_minor
    session.flush()
    return reservation


def tree(session: Session, workspace_id: str) -> list[dict]:
    accounts = session.execute(
        select(BudgetAccount).where(BudgetAccount.workspace_id == workspace_id).order_by(BudgetAccount.depth, BudgetAccount.name)
    ).scalars().all()
    return [
        {
            "id": a.id,
            "parent_id": a.parent_id,
            "name": a.name,
            "depth": a.depth,
            "limit": Money(a.limit_minor, a.currency).format(),
            "limit_minor": a.limit_minor,
            "consumed_minor": consumed_minor(session, a),
            "free_minor": free_minor(session, a),
            "currency": a.currency,
            "protected_kind": a.protected_kind,
        }
        for a in accounts
    ]
