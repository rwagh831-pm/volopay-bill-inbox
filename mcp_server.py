"""The MCP server: the three 'buttons' the customer's Claude can press.
No AI runs here. Claude does the thinking and calls these tools with clean,
structured data; this code just saves and reads it from our database."""
import os
from mcp.server.fastmcp import FastMCP
from sqlmodel import select
from db import get_session
from models import Bill

APP_BASE_URL = os.getenv("APP_BASE_URL", "http://localhost:8000")

mcp = FastMCP(
    "volopay-bills",
    stateless_http=True,
    streamable_http_path="/",
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

    vendor: who is being paid, e.g. 'AWS'. amount: numeric amount.
    due_date: optional ISO date 'YYYY-MM-DD'. currency: e.g. 'USD'.
    category: optional, e.g. 'cloud'. notes: optional free text."""
    with get_session() as s:
        b = Bill(vendor=vendor, amount=amount, due_date=due_date,
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
