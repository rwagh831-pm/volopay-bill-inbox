"""The MCP server: the three 'buttons' the customer's Claude can press.
No AI runs here. Claude does the thinking and calls these tools with clean,
structured data; this code just saves and reads it from our database."""
import os
from datetime import datetime
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from sqlmodel import select
from db import get_session
from models import Bill

APP_BASE_URL = os.getenv("APP_BASE_URL", "http://localhost:8000")
VALID_STATUSES = {"pending", "approved", "paid", "rejected"}


def _check_amount(amount: float) -> str | None:
    """Return an error message if the amount is not a sane, positive number."""
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return f"amount must be a number (got {amount!r})."
    if amount <= 0:
        return f"amount must be greater than 0 (got {amount})."
    if amount > 10_000_000:
        return f"amount {amount} looks too large — please confirm before adding."
    return None


def _check_date(due_date: str | None) -> str | None:
    """Return an error message if due_date is present but not a real YYYY-MM-DD date."""
    if not due_date:
        return None
    try:
        datetime.strptime(due_date, "%Y-%m-%d")
    except ValueError:
        return (f"due_date must be an ISO date like 2026-11-30 (got {due_date!r}). "
                "Resolve relative dates (e.g. 'next Friday') before calling.")
    return None

mcp = FastMCP(
    "volopay-bills",
    stateless_http=True,
    streamable_http_path="/",
    # This is a PUBLIC endpoint any Claude client must reach over HTTPS, so we
    # turn off the SDK's localhost-only DNS-rebinding host check (it otherwise
    # rejects the Render domain with "Invalid Host header"). Safe here because
    # the server exposes no localhost-privileged actions.
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False
    ),
    instructions=(
        "Volopay Bill Approval Inbox. Use these tools to add vendor bills a "
        "finance team must approve, list them, and update their status."
    ),
)


def _fmt(b: Bill) -> str:
    return (f"#{b.id} {b.vendor} — {b.currency} {b.amount:.2f} "
            f"due {b.due_date or 'n/a'} [{b.status}]")


@mcp.tool()
def create_bill(vendor: str, amount: float, due_date: str | None = None,
                currency: str = "USD", category: str | None = None,
                notes: str | None = None) -> str:
    """Add a new vendor bill to the approval inbox (status starts as 'pending').

    Only use this for an actual vendor bill or invoice the finance team must pay.
    Do NOT invent a bill from an unrelated or ambiguous request.

    vendor: who is being paid, e.g. 'AWS'. amount: numeric amount, must be > 0.
    due_date: optional ISO date 'YYYY-MM-DD' (resolve relative dates first).
    currency: 3-letter code, e.g. 'USD'. category: optional. notes: optional."""
    # --- Guardrails: AI may propose a bill, but only clean data gets written. ---
    vendor = (vendor or "").strip()
    if not vendor:
        return "⚠️ Bill not created: a vendor name is required."
    err = _check_amount(amount)
    if err:
        return f"⚠️ Bill not created: {err}"
    err = _check_date(due_date)
    if err:
        return f"⚠️ Bill not created: {err}"
    currency = (currency or "USD").strip().upper()[:4] or "USD"
    # ---------------------------------------------------------------------------
    with get_session() as s:
        b = Bill(vendor=vendor, amount=float(amount), due_date=due_date,
                 currency=currency, category=category, notes=notes,
                 status="pending", source="claude")
        s.add(b)
        s.commit()
        s.refresh(b)
        return f"Created bill {_fmt(b)}. Review and edit it at {APP_BASE_URL}/"


@mcp.tool()
def list_bills(status: str | None = None) -> str:
    """List bills, optionally filtered by status: pending, approved, paid, rejected."""
    with get_session() as s:
        q = select(Bill)
        if status:
            q = q.where(Bill.status == status)
        bills = s.exec(q.order_by(Bill.created_at.desc())).all()
        if not bills:
            return "No bills found."
        return "\n".join(_fmt(b) for b in bills)


@mcp.tool()
def update_bill(bill_id: int, status: str | None = None, vendor: str | None = None,
                amount: float | None = None, due_date: str | None = None,
                category: str | None = None, notes: str | None = None) -> str:
    """Update a bill by id. Set status to pending, approved, paid or rejected,
    or change vendor, amount, due_date, category or notes."""
    # --- Guardrails: validate every incoming field before touching the record. ---
    if status is not None and status not in VALID_STATUSES:
        return (f"⚠️ Bill not updated: status must be one of "
                f"{', '.join(sorted(VALID_STATUSES))} (got {status!r}).")
    if amount is not None:
        err = _check_amount(amount)
        if err:
            return f"⚠️ Bill not updated: {err}"
    err = _check_date(due_date)
    if err:
        return f"⚠️ Bill not updated: {err}"
    if vendor is not None and not vendor.strip():
        return "⚠️ Bill not updated: vendor name cannot be blank."
    # ----------------------------------------------------------------------------
    with get_session() as s:
        b = s.get(Bill, bill_id)
        if not b:
            return f"No bill with id {bill_id}."
        for field, val in [("status", status), ("vendor", vendor),
                           ("amount", amount), ("due_date", due_date),
                           ("category", category), ("notes", notes)]:
            if val is not None:
                setattr(b, field, val)
        s.add(b)
        s.commit()
        s.refresh(b)
        return f"Updated bill {_fmt(b)}."
