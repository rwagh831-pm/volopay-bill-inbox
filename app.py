"""The web app: the human side of the product.
Serves the Bill Approval Inbox UI at '/', and mounts the MCP server at '/mcp'
so the customer's Claude can connect to the same data."""
import os
from datetime import date
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import select
from db import init_db, get_session
from models import Bill
from mcp_server import mcp

templates = Jinja2Templates(directory="templates")

VALID_STATUSES = {"pending", "approved", "paid", "rejected"}

# The lifecycle: which quick actions are allowed FROM which status.
# action -> (new status, {statuses it may be applied to})
TRANSITIONS = {
    "approve": ("approved", {"pending"}),
    "reject":  ("rejected", {"pending"}),
    "pay":     ("paid", {"approved"}),
    "revert":  ("pending", {"approved"}),   # un-approve, back to the queue
    "restore": ("pending", {"rejected"}),   # bring a rejected bill back
}
# A record may only be hard-deleted while pending or rejected — never once
# it is approved or paid (those stay as an audit trail).
DELETABLE = {"pending", "rejected"}


def _summary(bills):
    """Finance-useful rollups for the top-of-page tiles."""
    today = date.today().isoformat()

    def totals(rows):
        out = {}
        for b in rows:
            out[b.currency] = out.get(b.currency, 0.0) + b.amount
        return out

    pending = [b for b in bills if b.status == "pending"]
    approved = [b for b in bills if b.status == "approved"]
    overdue = [b for b in bills
               if b.due_date and b.due_date < today
               and b.status in ("pending", "approved")]
    return {
        "today": today,
        "pending_n": len(pending),
        "pending_tot": totals(pending),
        "approved_n": len(approved),
        "approved_tot": totals(approved),
        "overdue_n": len(overdue),
        "overdue_tot": totals(overdue),
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()                                 # create tables on startup
    async with mcp.session_manager.run():     # start the MCP transport
        yield

app = FastAPI(title="Volopay Bill Approval Inbox", lifespan=lifespan)
app.mount("/mcp", mcp.streamable_http_app())  # Claude connects here


def _all_bills(status: str | None = None):
    with get_session() as s:
        q = select(Bill)
        if status:
            q = q.where(Bill.status == status)
        return s.exec(q.order_by(Bill.created_at.desc())).all()


@app.get("/", response_class=HTMLResponse)
def index(request: Request, status: str | None = None):
    bills = _all_bills(status)
    with get_session() as s:
        everything = s.exec(select(Bill)).all()
    counts = {k: sum(1 for b in everything if b.status == k)
              for k in ["pending", "approved", "paid", "rejected"]}
    return templates.TemplateResponse(request, "index.html", {
        "bills": bills, "status": status,
        "counts": counts, "total": len(everything),
        "summary": _summary(everything)})


@app.get("/bill/{bill_id}/edit", response_class=HTMLResponse)
def edit_form(request: Request, bill_id: int):
    with get_session() as s:
        bill = s.get(Bill, bill_id)
    return templates.TemplateResponse(request, "edit.html", {"bill": bill})


@app.post("/bill/{bill_id}/edit")
def edit_save(bill_id: int, vendor: str = Form(...), amount: float = Form(...),
              currency: str = Form("USD"), due_date: str = Form(""),
              category: str = Form(""), notes: str = Form(""), status: str = Form("pending")):
    # Guardrails on the human form too, not just the AI path.
    vendor = vendor.strip()
    if not vendor or amount <= 0 or status not in VALID_STATUSES:
        return RedirectResponse(f"/bill/{bill_id}/edit?error=1", status_code=303)
    with get_session() as s:
        bill = s.get(Bill, bill_id)
        if bill:
            bill.vendor, bill.amount, bill.currency = vendor, amount, currency.strip().upper()[:4] or "USD"
            bill.due_date, bill.category = due_date or None, category or None
            bill.notes, bill.status = notes or None, status
            s.add(bill)
            s.commit()
    return RedirectResponse("/", status_code=303)


@app.post("/bill/{bill_id}/{action}")
def quick_action(bill_id: int, action: str):
    with get_session() as s:
        bill = s.get(Bill, bill_id)
        if not bill:
            return RedirectResponse("/", status_code=303)
        if action == "delete":
            if bill.status in DELETABLE:          # never hard-delete approved/paid
                s.delete(bill)
                s.commit()
        elif action in TRANSITIONS:
            new_status, allowed_from = TRANSITIONS[action]
            if bill.status in allowed_from:        # only valid lifecycle moves
                bill.status = new_status
                s.add(bill)
                s.commit()
    return RedirectResponse("/", status_code=303)
