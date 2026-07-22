"""
schemas.py

Pydantic models defining the API's request and response shapes. Keeping
these separate from the ORM/DB layer (quote_service.py etc.) means the API
contract is explicit and self-documenting -- FastAPI turns these straight
into the OpenAPI schema at /docs, so anyone consuming this API can see
exactly what's required and what's returned without reading the source.
"""

from __future__ import annotations
from datetime import date, datetime
from typing import Optional
from pydantic import BaseModel, Field


# ---------- Service orders / accounts ----------

class ServiceOrderLookupResponse(BaseModel):
    service_order_no: str
    account_id: int
    account_name: str
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    site_address: Optional[str] = None


class PartOut(BaseModel):
    part_number: str
    description: str
    category: Optional[str] = None
    list_price: float


class PriceLookupResponse(BaseModel):
    account_id: int
    part_number: str
    description: str
    price: float


# ---------- Quotes ----------

class LineItemIn(BaseModel):
    part_number: str = Field(..., examples=["HW-2210"])
    quantity: int = Field(..., gt=0, examples=[4])


class QuoteCreateRequest(BaseModel):
    service_order_no: str = Field(..., examples=["500125"])
    created_by: str = Field(..., examples=["J. Rycas"])
    line_items: list[LineItemIn]
    expires_in_days: int = Field(default=30, ge=1, le=365)


class LineItemOut(BaseModel):
    part_number: str
    description: str
    quantity: int
    unit_price: float
    line_total: float


class QuoteCreateResponse(BaseModel):
    quote_number: str
    account_id: int
    account_name: str
    line_items: list[LineItemOut]
    total: float
    status: str


class QuoteDetailResponse(BaseModel):
    quote_number: str
    service_order_no: Optional[str] = None
    account_name: str
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    site_address: Optional[str] = None
    status: str
    created_at: datetime
    sent_at: Optional[datetime] = None
    expires_at: Optional[date] = None
    line_items: list[LineItemOut]
    total: float


class QuoteSummary(BaseModel):
    quote_number: str
    account_name: str
    status: str
    created_at: datetime
    quote_total: float


class QuoteListResponse(BaseModel):
    count: int
    quotes: list[QuoteSummary]


class MarkSentResponse(BaseModel):
    quote_number: str
    status: str
    pdf_path: str


# ---------- Reporting ----------

class StatusBreakdownItem(BaseModel):
    status: str
    quote_count: int
    pct_of_total: float
    total_value: float


class RevenueByAccountItem(BaseModel):
    account_name: str
    accepted_quotes: int
    accepted_revenue: float
    total_quotes: int


class TopPartItem(BaseModel):
    part_number: str
    description: str
    times_quoted: int
    total_quantity: int
    total_quoted_value: float


class PipelineReportResponse(BaseModel):
    win_rate_pct: float
    status_breakdown: list[StatusBreakdownItem]
    avg_days_to_accept: Optional[float] = None
    avg_days_to_decline: Optional[float] = None
    revenue_by_account: list[RevenueByAccountItem]
    top_parts: list[TopPartItem]


class FollowUpItem(BaseModel):
    quote_number: str
    account_name: str
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    days_since_sent: int
    quote_total: float


class FollowUpResponse(BaseModel):
    count: int
    days_threshold: int
    quotes: list[FollowUpItem]


class ErrorResponse(BaseModel):
    detail: str
