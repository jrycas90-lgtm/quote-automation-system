"""
app.py

The Streamlit UI -- this is the direct replacement for the "Quote Template"
Excel workbook (scratch sheet + quotation tab). Two pages:

  1. New Quote: enter a service order number, account auto-populates from
     the synced ERP data, add part numbers with auto-looked-up pricing,
     generate a real PDF, mark it as sent.
  2. Dashboard: pipeline visibility that never existed in the spreadsheet
     workflow -- win rate, revenue by account, quotes needing follow-up.

Run with:
    streamlit run app.py
"""

import sys
from pathlib import Path
import yaml
import streamlit as st
import streamlit_authenticator as stauth
import pandas as pd

sys.path.append(str(Path(__file__).resolve().parent / "src"))
from quote_service import (
    start_quote_from_service_order, add_line_item, save_quote, mark_quote_sent,
    UnknownServiceOrderError, UnknownPartError,
)
from pdf_generator import generate_pdf
from db import get_connection, get_dict_cursor
import reporting
import follow_up
from config.branding import COMPANY_NAME

st.set_page_config(page_title="Quote Automation System", page_icon="📋", layout="wide")


def _secrets_section_to_dict(section) -> dict:
    """Recursively converts a Streamlit secrets section (or sub-section)
    into a plain, mutable Python dict/list structure. st.secrets objects
    are read-only, but streamlit-authenticator needs to mutate the
    credentials dict at runtime (e.g. tracking failed login attempts),
    so we can't hand it the secrets object directly."""
    if hasattr(section, "to_dict"):
        section = section.to_dict()
    if isinstance(section, dict):
        return {k: _secrets_section_to_dict(v) for k, v in section.items()}
    if isinstance(section, list):
        return [_secrets_section_to_dict(v) for v in section]
    return section


def load_authenticator() -> stauth.Authenticate:
    """Loads login credentials from one of two places, in priority order:

    1. Streamlit secrets (st.secrets) -- used for anything deployed (e.g.
       Streamlit Community Cloud), where credentials are entered in the
       app's Secrets panel and never touch git at all.
    2. config/auth_config.yaml -- used for local development. This file is
       gitignored -- see config/auth_config.example.yaml for the template
       and scripts/hash_password.py to generate password hashes.

    To use secrets-based auth, structure your app's Secrets like:

        [credentials.usernames.someuser]
        email = "someone@example.com"
        name = "Some Name"
        password = "$2b$12$..."

        [cookie]
        name = "quote_auth_cookie"
        key = "some-random-signing-string"
        expiry_days = 7
    """
    try:
        secrets_available = "credentials" in st.secrets and "cookie" in st.secrets
    except st.errors.StreamlitSecretNotFoundError:
        secrets_available = False

    if secrets_available:
        credentials = _secrets_section_to_dict(st.secrets["credentials"])
        cookie = _secrets_section_to_dict(st.secrets["cookie"])
        return stauth.Authenticate(
            credentials, cookie["name"], cookie["key"], cookie["expiry_days"],
        )

    config_path = Path(__file__).resolve().parent / "config" / "auth_config.yaml"
    if not config_path.exists():
        st.error(
            "No login credentials found. For local development, copy "
            "config/auth_config.example.yaml to config/auth_config.yaml, fill in "
            "real credentials (use scripts/hash_password.py to hash passwords), "
            "and restart the app. For a deployed app, add credentials to the "
            "Secrets panel instead -- see the load_authenticator() docstring "
            "in app.py for the expected format."
        )
        st.stop()

    with open(config_path) as f:
        auth_config = yaml.safe_load(f)

    return stauth.Authenticate(
        auth_config["credentials"],
        auth_config["cookie"]["name"],
        auth_config["cookie"]["key"],
        auth_config["cookie"]["expiry_days"],
    )


authenticator = load_authenticator()
authenticator.login(location="main", fields={"Form name": "Login"})
name = st.session_state.get("name")
auth_status = st.session_state.get("authentication_status")
username = st.session_state.get("username")

if auth_status is False:
    st.error("Username or password is incorrect.")
    st.stop()
elif auth_status is None:
    st.warning("Please enter your username and password.")
    st.stop()


def get_all_parts():
    conn = get_connection()
    cur = get_dict_cursor(conn)
    cur.execute("SELECT part_number, description FROM parts ORDER BY part_number")
    parts = cur.fetchall()
    cur.close()
    conn.close()
    return parts


def new_quote_page():
    st.title("📋 New Quote")
    st.caption("Replaces the scratch sheet: enter a service order number, everything else auto-populates.")

    if "draft" not in st.session_state:
        st.session_state.draft = None

    col1, col2 = st.columns([2, 1])
    with col1:
        service_order_no = st.text_input("Service Order Number", placeholder="e.g. 500125")
    with col2:
        st.write("")
        st.write("")
        if st.button("Look Up", type="primary"):
            try:
                st.session_state.draft = start_quote_from_service_order(service_order_no.strip())
            except UnknownServiceOrderError as e:
                st.error(str(e))
                st.session_state.draft = None

    draft = st.session_state.draft
    if draft is None:
        st.info("Enter a service order number above to start a quote. "
                 "Try one of the synced numbers, e.g. 500125, 500148, 500174.")
        return

    st.success(f"**{draft.account_name}**  |  {draft.contact_name} ({draft.contact_email})")
    if draft.site_address:
        st.caption(f"Site: {draft.site_address}")

    st.divider()
    st.subheader("Add Parts")

    parts = get_all_parts()
    part_options = {f"{p['part_number']} — {p['description']}": p["part_number"] for p in parts}

    col1, col2, col3 = st.columns([3, 1, 1])
    with col1:
        selected = st.selectbox("Part", options=list(part_options.keys()))
    with col2:
        qty = st.number_input("Qty", min_value=1, value=1, step=1)
    with col3:
        st.write("")
        st.write("")
        if st.button("Add Item"):
            part_number = part_options[selected]
            try:
                add_line_item(draft, part_number, qty)
            except UnknownPartError as e:
                st.error(str(e))

    if draft.line_items:
        st.subheader("Quote Detail")
        df = pd.DataFrame([{
            "Part #": li.part_number,
            "Description": li.description,
            "Qty": li.quantity,
            "Unit Price": f"${li.unit_price:,.2f}",
            "Line Total": f"${li.line_total:,.2f}",
        } for li in draft.line_items])
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.markdown(f"### Total: ${draft.total:,.2f}")

        col1, col2 = st.columns(2)
        with col1:
            created_by = st.text_input("Prepared by", value="Quote Admin")
        with col2:
            st.write("")

        if st.button("Generate Quote & PDF", type="primary"):
            quote_number = save_quote(draft, created_by=created_by)
            pdf_path = generate_pdf(quote_number, output_dir="output")
            st.session_state.last_quote_number = quote_number
            st.session_state.last_pdf_path = pdf_path
            st.session_state.draft = None
            st.rerun()
    else:
        st.caption("No line items yet -- add parts above.")

    if st.session_state.get("last_quote_number"):
        st.divider()
        st.success(f"Quote **{st.session_state.last_quote_number}** generated.")
        with open(st.session_state.last_pdf_path, "rb") as f:
            st.download_button(
                "Download PDF", f,
                file_name=f"{st.session_state.last_quote_number}.pdf",
                mime="application/pdf",
            )
        if st.button("Mark as Sent"):
            mark_quote_sent(st.session_state.last_quote_number, st.session_state.last_pdf_path)
            st.success("Marked as sent.")
            st.session_state.last_quote_number = None


def dashboard_page():
    st.title("📊 Pipeline Dashboard")
    st.caption("Visibility that didn't exist in the spreadsheet workflow.")

    win_rate = reporting.win_rate_pct()
    status_summary = reporting.win_rate_summary()

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Win Rate", f"{win_rate}%")

    status_map = {row["status"]: row for row in status_summary}
    col2.metric("Sent (awaiting response)", status_map.get("sent", {}).get("quote_count", 0))
    col3.metric("Accepted", status_map.get("accepted", {}).get("quote_count", 0))
    col4.metric("Declined / Expired",
                status_map.get("declined", {}).get("quote_count", 0) + status_map.get("expired", {}).get("quote_count", 0))

    st.divider()

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Revenue by Account (Accepted Quotes)")
        rev_df = pd.DataFrame(reporting.revenue_by_account())
        rev_df["accepted_revenue"] = rev_df["accepted_revenue"].fillna(0).astype(float)
        st.bar_chart(rev_df.set_index("account_name")["accepted_revenue"])

    with col2:
        st.subheader("Top Quoted Parts (by $ value)")
        parts_df = pd.DataFrame(reporting.top_quoted_parts(8))
        st.bar_chart(parts_df.set_index("part_number")["total_quoted_value"])

    st.divider()
    st.subheader("⚠️ Needs Follow-Up (sent 7+ days ago, no response)")
    follow_up_list = follow_up.get_quotes_needing_follow_up(days_since_sent=7)
    if follow_up_list:
        fu_df = pd.DataFrame(follow_up_list)
        fu_df["quote_total"] = fu_df["quote_total"].astype(float).map(lambda x: f"${x:,.2f}")
        st.dataframe(
            fu_df[["quote_number", "account_name", "contact_name", "contact_email",
                   "days_since_sent", "quote_total"]],
            use_container_width=True, hide_index=True,
        )
    else:
        st.caption("Nothing needs follow-up right now.")


def main():
    st.sidebar.title(COMPANY_NAME)
    st.sidebar.caption("Replaces the Master Price List + Quote Template workflow.")
    st.sidebar.write(f"Logged in as **{name}**")
    authenticator.logout("Log out", "sidebar")
    page = st.sidebar.radio("Navigate", ["New Quote", "Dashboard"])

    if page == "New Quote":
        new_quote_page()
    else:
        dashboard_page()


if __name__ == "__main__":
    main()
