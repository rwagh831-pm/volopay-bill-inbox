"""The web app: the human side of the product.
Serves the Bill Approval Inbox UI at '/', and mounts the MCP server at '/mcp'
so the customer's Claude can connect to the same data."""
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import select
from db import init_db, get_session
from models import Bill
from mcp_server import mcp

templates = Jinja2Templates(directory="templates")


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
        "counts": counts, "total": len(everything)})


@app.get("/bill/{bill_id}/edit", response_class=HTMLResponse)
def edit_form(request: Request, bill_id: int):
    with get_session() as s:
        bill = s.get(Bill, bill_id)
    return templates.TemplateResponse(request, "edit.html", {"bill": bill})


@app.post("/bill/{bill_id}/edit")
def edit_save(bill_id: int, vendor: str = Form(...), amount: float = Form(...),
              currency: str = Form("USD"), due_date: str = Form(""),
              category: str = Form(""), notes: str = Form(""), status: str = Form("pending")):
    with get_session() as s:
        bill = s.get(Bill, bill_id)
        if bill:
            bill.vendor, bill.amount, bill.currency = vendor, amount, currency
            bill.due_date, bill.category = due_date or None, category or None
            bill.notes, bill.status = notes or None, status
            s.add(bill)
            s.commit()
    return RedirectResponse("/", status_code=303)


@app.post("/bill/{bill_id}/{action}")
def quick_action(bill_id: int, action: str):
    mapping = {"approve": "approved", "pay": "paid", "reject": "rejected"}
    with get_session() as s:
        bill = s.get(Bill, bill_id)
        if bill and action in mapping:
            bill.status = mapping[action]
            s.add(bill)
            s.commit()
        elif bill and action == "delete":
            s.delete(bill)
            s.commit()
    return RedirectResponse("/", status_code=303)
