"""
pdf_generator.py

Renders a branded quote PDF -- replaces the manual "export the Quotation
tab as a PDF" step in the original workflow. Pulls the quote and its line
items straight from the database, so the PDF is always generated from the
same data that's stored and auditable, not from whatever happened to be
on screen at export time.

Usage:
    python src/pdf_generator.py --quote Q-2026-00001
"""

from __future__ import annotations
import argparse
import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

sys.path.append(str(Path(__file__).resolve().parent))
sys.path.append(str(Path(__file__).resolve().parent.parent))
from db import get_connection, get_dict_cursor
from config.branding import load_branding


def fetch_quote_data(quote_number: str) -> dict:
    conn = get_connection()
    cur = get_dict_cursor(conn)

    cur.execute(
        """
        SELECT q.quote_number, q.service_order_no, q.created_at, q.expires_at, q.status,
               a.account_name, a.contact_name, a.contact_email,
               so.site_address
        FROM quotes q
        JOIN accounts a ON a.account_id = q.account_id
        LEFT JOIN service_orders so ON so.service_order_no = q.service_order_no
        WHERE q.quote_number = %s
        """,
        (quote_number,),
    )
    quote = cur.fetchone()
    if quote is None:
        cur.close()
        conn.close()
        raise ValueError(f"Quote {quote_number} not found.")

    cur.execute(
        """
        SELECT part_number, description, quantity, unit_price, line_total
        FROM quote_line_items
        WHERE quote_id = (SELECT quote_id FROM quotes WHERE quote_number = %s)
        ORDER BY id
        """,
        (quote_number,),
    )
    line_items = cur.fetchall()

    cur.close()
    conn.close()

    return {"quote": quote, "line_items": line_items}


def generate_pdf(quote_number: str, output_dir: str = "output") -> str:
    data = fetch_quote_data(quote_number)
    quote = data["quote"]
    line_items = data["line_items"]
    branding = load_branding()

    Path(output_dir).mkdir(parents=True, exist_ok=True)
    output_path = str(Path(output_dir) / f"{quote_number}.pdf")

    doc = SimpleDocTemplate(
        output_path, pagesize=letter,
        topMargin=0.6 * inch, bottomMargin=0.6 * inch,
        leftMargin=0.6 * inch, rightMargin=0.6 * inch,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("QuoteTitle", parent=styles["Heading1"], fontSize=20, spaceAfter=4)
    small_style = ParagraphStyle("Small", parent=styles["Normal"], fontSize=9, textColor=colors.grey)
    section_style = ParagraphStyle("Section", parent=styles["Heading3"], spaceBefore=12, spaceAfter=4)

    elements = []

    logo_path = branding.get("logo_path")
    if logo_path and Path(logo_path).exists():
        try:
            logo = Image(logo_path, width=1.4 * inch, height=1.4 * inch, kind="proportional")
            header_table = Table(
                [[logo, Paragraph(branding["company_name"], title_style)]],
                colWidths=[1.6 * inch, 4.9 * inch],
            )
            header_table.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ]))
            elements.append(header_table)
        except Exception:
            # If the logo file is unreadable/corrupt for any reason, fall
            # back to text-only rather than failing the whole PDF.
            elements.append(Paragraph(branding["company_name"], title_style))
    else:
        elements.append(Paragraph(branding["company_name"], title_style))

    contact_line = f"{branding['company_address']} | {branding['company_phone']} | {branding['company_email']}"
    elements.append(Paragraph(contact_line, small_style))
    elements.append(Spacer(1, 16))

    meta_table_data = [
        ["Quote #:", quote["quote_number"], "Date:", quote["created_at"].strftime("%B %d, %Y")],
        ["Service Order:", quote["service_order_no"] or "-", "Expires:", quote["expires_at"].strftime("%B %d, %Y") if quote["expires_at"] else "-"],
    ]
    meta_table = Table(meta_table_data, colWidths=[1.1 * inch, 1.9 * inch, 1.0 * inch, 1.9 * inch])
    meta_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    elements.append(meta_table)
    elements.append(Spacer(1, 12))

    elements.append(Paragraph("Prepared For", section_style))
    bill_to_lines = [quote["account_name"]]
    if quote["contact_name"]:
        bill_to_lines.append(quote["contact_name"])
    if quote["contact_email"]:
        bill_to_lines.append(quote["contact_email"])
    if quote["site_address"]:
        bill_to_lines.append(f"Site: {quote['site_address']}")
    elements.append(Paragraph("<br/>".join(bill_to_lines), styles["Normal"]))
    elements.append(Spacer(1, 12))

    elements.append(Paragraph("Quote Detail", section_style))
    table_data = [["Description", "Qty", "Unit Price", "Total"]]
    total = 0.0
    for li in line_items:
        table_data.append([
            li["description"],
            str(li["quantity"]),
            f"${li['unit_price']:.2f}",
            f"${li['line_total']:.2f}",
        ])
        total += float(li["line_total"])

    table_data.append(["", "", "Total", f"${total:.2f}"])

    line_items_table = Table(
        table_data,
        colWidths=[3.7 * inch, 0.6 * inch, 1.0 * inch, 1.0 * inch],
    )
    line_items_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(branding["brand_color"])),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("GRID", (0, 0), (-1, -2), 0.5, colors.HexColor("#CCCCCC")),
        ("LINEABOVE", (0, -1), (-1, -1), 1, colors.HexColor(branding["brand_color"])),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    elements.append(line_items_table)
    elements.append(Spacer(1, 20))

    terms = (
        "This quote is valid until the expiration date listed above. Pricing reflects your "
        "account's negotiated rates. Please contact us to accept this quote or if you have "
        "any questions."
    )
    elements.append(Paragraph(terms, small_style))

    doc.build(elements)
    return output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a quote PDF")
    parser.add_argument("--quote", required=True, help="Quote number, e.g. Q-2026-00001")
    parser.add_argument("--output", default="output")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    path = generate_pdf(args.quote, args.output)
    print(f"Generated PDF -> {path}")
