"""
main.py

REST API wrapper around the quote automation system. This exposes the same
business logic that powers the Streamlit app (src/quote_service.py,
src/pdf_generator.py, src/follow_up.py, src/reporting.py) as a documented,
typed HTTP API instead -- so the quoting system can be integrated into
other tools (a CRM, a scheduled job, a different frontend) without needing
to run the Streamlit UI at all.

Run with:
    uvicorn api.main:app --reload

Interactive docs (auto-generated from the schemas below) are then at:
    http://127.0.0.1:8000/docs
"""

from __future__ import annotations
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))
sys.path.append(str(Path(__file__).resolve().parent.parent / "src"))

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

import quote_service as qs
import pdf_generator as pdfgen
import follow_up
import reporting
import queries

from schemas import (
    ServiceOrderLookupResponse, PartOut, PriceLookupResponse,
    QuoteCreateRequest, QuoteCreateResponse, LineItemOut,
    QuoteDetailResponse, QuoteListResponse, QuoteSummary, MarkSentResponse,
    PipelineReportResponse, StatusBreakdownItem, RevenueByAccountItem, TopPartItem,
    FollowUpResponse, FollowUpItem,
)

app = FastAPI(
    title="Quote Automation API",
    description=(
        "REST API for the quote automation system -- account/pricing lookup, "
        "quote creation, PDF generation, follow-up tracking, and pipeline "
        "reporting. Wraps the same logic used by the Streamlit app in app.py."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["Health"])
def health_check():
    return {"status": "ok"}


# ---------- Service orders / accounts / parts ----------

@app.get(
    "/service-orders/{service_order_no}",
    response_model=ServiceOrderLookupResponse,
    tags=["Service Orders"],
    summary="Look up a service order and its account (the auto-populate step)",
)
def get_service_order(service_order_no: str):
    try:
        draft = qs.start_quote_from_service_order(service_order_no)
    except qs.UnknownServiceOrderError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return ServiceOrderLookupResponse(
        service_order_no=draft.service_order_no,
        account_id=draft.account_id,
        account_name=draft.account_name,
        contact_name=draft.contact_name,
        contact_email=draft.contact_email,
        site_address=draft.site_address,
    )


@app.get("/parts", response_model=list[PartOut], tags=["Parts & Pricing"])
def get_parts():
    return queries.list_parts()


@app.get(
    "/accounts/{account_id}/pricing/{part_number}",
    response_model=PriceLookupResponse,
    tags=["Parts & Pricing"],
    summary="Look up an account's negotiated price for a part (falls back to list price)",
)
def get_price(account_id: int, part_number: str):
    try:
        price, description = qs.lookup_price(account_id, part_number)
    except qs.UnknownPartError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return PriceLookupResponse(
        account_id=account_id, part_number=part_number, description=description, price=price,
    )


# ---------- Quotes ----------

@app.post(
    "/quotes",
    response_model=QuoteCreateResponse,
    status_code=201,
    tags=["Quotes"],
    summary="Create a quote from a service order number and a list of parts",
)
def create_quote(payload: QuoteCreateRequest):
    try:
        draft = qs.start_quote_from_service_order(payload.service_order_no)
    except qs.UnknownServiceOrderError as e:
        raise HTTPException(status_code=404, detail=str(e))

    if not payload.line_items:
        raise HTTPException(status_code=422, detail="A quote needs at least one line item.")

    for item in payload.line_items:
        try:
            qs.add_line_item(draft, item.part_number, item.quantity)
        except qs.UnknownPartError as e:
            raise HTTPException(status_code=404, detail=str(e))

    quote_number = qs.save_quote(draft, created_by=payload.created_by, expires_in_days=payload.expires_in_days)

    return QuoteCreateResponse(
        quote_number=quote_number,
        account_id=draft.account_id,
        account_name=draft.account_name,
        line_items=[
            LineItemOut(part_number=li.part_number, description=li.description,
                        quantity=li.quantity, unit_price=li.unit_price, line_total=li.line_total)
            for li in draft.line_items
        ],
        total=draft.total,
        status="draft",
    )


@app.get("/quotes", response_model=QuoteListResponse, tags=["Quotes"])
def list_quotes_endpoint(
    status: str | None = Query(default=None, description="Filter by status: draft, sent, accepted, declined, expired"),
    account_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
):
    rows = queries.list_quotes(status=status, account_id=account_id, limit=limit)
    return QuoteListResponse(
        count=len(rows),
        quotes=[
            QuoteSummary(
                quote_number=r["quote_number"], account_name=r["account_name"],
                status=r["status"], created_at=r["created_at"], quote_total=float(r["quote_total"]),
            )
            for r in rows
        ],
    )


@app.get("/quotes/{quote_number}", response_model=QuoteDetailResponse, tags=["Quotes"])
def get_quote(quote_number: str):
    detail = queries.get_quote_detail(quote_number)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Quote {quote_number} not found.")

    return QuoteDetailResponse(
        quote_number=detail["quote_number"],
        service_order_no=detail["service_order_no"],
        account_name=detail["account_name"],
        contact_name=detail["contact_name"],
        contact_email=detail["contact_email"],
        site_address=detail["site_address"],
        status=detail["status"],
        created_at=detail["created_at"],
        sent_at=detail["sent_at"],
        expires_at=detail["expires_at"],
        line_items=[
            LineItemOut(
                part_number=li["part_number"], description=li["description"],
                quantity=li["quantity"], unit_price=float(li["unit_price"]),
                line_total=float(li["line_total"]),
            )
            for li in detail["line_items"]
        ],
        total=detail["total"],
    )


@app.post(
    "/quotes/{quote_number}/pdf",
    tags=["Quotes"],
    summary="Generate (or regenerate) the PDF for a quote and return the file",
)
def generate_quote_pdf(quote_number: str):
    try:
        path = pdfgen.generate_pdf(quote_number, output_dir="output")
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return FileResponse(path, media_type="application/pdf", filename=f"{quote_number}.pdf")


@app.post(
    "/quotes/{quote_number}/send",
    response_model=MarkSentResponse,
    tags=["Quotes"],
    summary="Generate the PDF (if needed) and mark the quote as sent",
)
def send_quote(quote_number: str):
    detail = queries.get_quote_detail(quote_number)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Quote {quote_number} not found.")

    pdf_path = pdfgen.generate_pdf(quote_number, output_dir="output")
    qs.mark_quote_sent(quote_number, pdf_path)

    return MarkSentResponse(quote_number=quote_number, status="sent", pdf_path=pdf_path)


# ---------- Reporting ----------

@app.get("/reports/pipeline", response_model=PipelineReportResponse, tags=["Reporting"])
def get_pipeline_report():
    timing = reporting.avg_time_to_close()

    return PipelineReportResponse(
        win_rate_pct=reporting.win_rate_pct(),
        status_breakdown=[
            StatusBreakdownItem(
                status=r["status"], quote_count=r["quote_count"],
                pct_of_total=float(r["pct_of_total"]), total_value=float(r["total_value"]),
            )
            for r in reporting.win_rate_summary()
        ],
        avg_days_to_accept=float(timing["avg_days_to_accept"]) if timing["avg_days_to_accept"] is not None else None,
        avg_days_to_decline=float(timing["avg_days_to_decline"]) if timing["avg_days_to_decline"] is not None else None,
        revenue_by_account=[
            RevenueByAccountItem(
                account_name=r["account_name"], accepted_quotes=r["accepted_quotes"] or 0,
                accepted_revenue=float(r["accepted_revenue"] or 0), total_quotes=r["total_quotes"],
            )
            for r in reporting.revenue_by_account()
        ],
        top_parts=[
            TopPartItem(
                part_number=r["part_number"], description=r["description"],
                times_quoted=r["times_quoted"], total_quantity=r["total_quantity"],
                total_quoted_value=float(r["total_quoted_value"]),
            )
            for r in reporting.top_quoted_parts(10)
        ],
    )


@app.get("/reports/follow-up", response_model=FollowUpResponse, tags=["Reporting"])
def get_follow_up(days: int = Query(default=7, ge=1, le=90)):
    rows = follow_up.get_quotes_needing_follow_up(days_since_sent=days)
    return FollowUpResponse(
        count=len(rows),
        days_threshold=days,
        quotes=[
            FollowUpItem(
                quote_number=r["quote_number"], account_name=r["account_name"],
                contact_name=r["contact_name"], contact_email=r["contact_email"],
                days_since_sent=r["days_since_sent"], quote_total=float(r["quote_total"]),
            )
            for r in rows
        ],
    )
