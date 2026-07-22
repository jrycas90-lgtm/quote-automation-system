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
    start_quote_from_service_order, add_line_item, add_custom_line_item, save_quote,
    mark_quote_sent, UnknownServiceOrderError, UnknownPartError,
)
from pdf_generator import generate_pdf
from db import get_connection, get_dict_cursor
import reporting
import follow_up
from config.branding import load_branding, save_branding_override, save_logo

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


def load_authenticator() -> tuple[stauth.Authenticate, dict]:
    """Loads login credentials from one of two places, in priority order:

    1. Streamlit secrets (st.secrets) -- used for anything deployed (e.g.
       Streamlit Community Cloud), where credentials are entered in the
       app's Secrets panel and never touch git at all.
    2. config/auth_config.yaml -- used for local development. This file is
       gitignored -- see config/auth_config.example.yaml for the template
       and scripts/hash_password.py to generate password hashes.

    Returns (authenticator, credentials_dict). The credentials dict is
    returned alongside the authenticator so the app can look up each
    user's "role" field (a custom addition on top of what
    streamlit-authenticator itself uses) to gate access to admin-only
    pages like Settings.

    To use secrets-based auth, structure your app's Secrets like:

        [credentials.usernames.someuser]
        email = "someone@example.com"
        name = "Some Name"
        password = "$2b$12$..."
        role = "admin"

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
        auth = stauth.Authenticate(
            credentials, cookie["name"], cookie["key"], cookie["expiry_days"],
        )
        return auth, credentials

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

    auth = stauth.Authenticate(
        auth_config["credentials"],
        auth_config["cookie"]["name"],
        auth_config["cookie"]["key"],
        auth_config["cookie"]["expiry_days"],
    )
    return auth, auth_config["credentials"]


def get_user_role(credentials: dict, username: str | None) -> str:
    """Returns the role for a given username, e.g. 'admin', 'supervisor',
    or 'user'. Defaults to 'user' if no role is set (so existing accounts
    created before roles existed still work, just without elevated access)."""
    if not username:
        return "user"
    user_entry = credentials.get("usernames", {}).get(username, {})
    return user_entry.get("role", "user")


ADMIN_ROLES = {"admin", "supervisor"}


authenticator, _credentials = load_authenticator()
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

user_role = get_user_role(_credentials, username)


def inject_custom_css(brand_color: str) -> None:
    """Applies light visual polish -- a cleaner font, styled primary
    buttons and metric cards using the configured brand color, and a
    slightly refined sidebar -- without touching Streamlit's underlying
    layout engine. Safe to call on every page load since it's just CSS."""
    st.markdown(
        f"""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

        html, body, [class^="css"], [class*=" css"] {{
            font-family: 'Inter', -apple-system, sans-serif;
        }}

        .stButton > button[kind="primary"] {{
            background-color: {brand_color};
            border-color: {brand_color};
            font-weight: 600;
        }}
        .stButton > button[kind="primary"]:hover {{
            background-color: {brand_color};
            border-color: {brand_color};
            opacity: 0.88;
        }}

        div[data-testid="stMetric"] {{
            background-color: rgba(151, 166, 195, 0.08);
            border: 1px solid rgba(151, 166, 195, 0.22);
            border-radius: 10px;
            padding: 14px 16px 10px 16px;
        }}

        section[data-testid="stSidebar"] {{
            border-right: 1px solid rgba(151, 166, 195, 0.25);
        }}

        h1, h2, h3 {{
            font-weight: 700;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


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

    # If a quote was just generated, show its download/send section first --
    # regardless of draft state -- so it's never hidden behind the lookup
    # form. The "Start a New Quote" button is the only way back to lookup
    # from here, which keeps the completed quote visible until the user is
    # done with it.
    if st.session_state.get("last_quote_number"):
        st.success(f"Quote **{st.session_state.last_quote_number}** generated.")
        with open(st.session_state.last_pdf_path, "rb") as f:
            st.download_button(
                "Download PDF", f,
                file_name=f"{st.session_state.last_quote_number}.pdf",
                mime="application/pdf",
            )
        col1, col2 = st.columns(2)
        with col1:
            if st.button("Mark as Sent"):
                mark_quote_sent(st.session_state.last_quote_number, st.session_state.last_pdf_path)
                st.success("Marked as sent.")
        with col2:
            if st.button("Start a New Quote", type="primary"):
                st.session_state.last_quote_number = None
                st.session_state.last_pdf_path = None
                st.rerun()
        return

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

    with st.expander("Add a Custom Item"):
        st.caption("For items not in the standard parts catalog.")
        custom_col1, custom_col2, custom_col3 = st.columns([3, 1, 1])
        with custom_col1:
            custom_description = st.text_input("Description", key="custom_part_description")
        with custom_col2:
            custom_qty = st.number_input("Qty", min_value=1, value=1, step=1, key="custom_part_qty")
        with custom_col3:
            custom_price = st.number_input("Unit Price ($)", min_value=0.0, value=0.0, step=0.01, key="custom_part_price")
        if st.button("Add Custom Item"):
            if not custom_description.strip():
                st.error("Enter a description for the custom item.")
            else:
                add_custom_line_item(draft, custom_description.strip(), custom_qty, custom_price)
                st.rerun()

    if draft.line_items:
        st.subheader("Quote Detail")
        df = pd.DataFrame([{
            "Part #": li.part_number or "(custom)",
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
        fu_df = fu_df.rename(columns={"created_by": "Prepared By"})
        st.dataframe(
            fu_df[["quote_number", "account_name", "contact_name", "contact_email",
                   "days_since_sent", "quote_total", "Prepared By"]],
            use_container_width=True, hide_index=True,
        )
    else:
        st.caption("Nothing needs follow-up right now.")


def reports_page():
    if user_role not in ADMIN_ROLES:
        st.error("You don't have permission to view this page.")
        st.stop()

    st.title("📈 Reports")
    st.caption("Run a report, review it on screen, then download it as a CSV for management.")

    report_options = [
        "Revenue by Account (Accepted Quotes)",
        "Total Quoted Value by Account (All Quotes)",
        "Top Quoted Parts",
        "Least Quoted Parts",
        "Pipeline Status Breakdown",
        "Follow-Up by Employee",
    ]
    report_choice = st.selectbox("Report Type", report_options)
    st.divider()

    report_df = pd.DataFrame()
    report_filename = "report.csv"

    if report_choice == "Revenue by Account (Accepted Quotes)":
        data = reporting.revenue_by_account()
        report_df = pd.DataFrame(data)
        if not report_df.empty:
            report_df["accepted_revenue"] = report_df["accepted_revenue"].fillna(0).astype(float)
        report_filename = "revenue_by_account.csv"
        st.dataframe(report_df, use_container_width=True, hide_index=True)

    elif report_choice == "Total Quoted Value by Account (All Quotes)":
        data = reporting.total_quoted_value_by_account()
        report_df = pd.DataFrame(data)
        report_filename = "total_quoted_value_by_account.csv"
        st.dataframe(report_df, use_container_width=True, hide_index=True)

    elif report_choice == "Top Quoted Parts":
        n = st.slider("Number of parts to show", 5, 25, 10)
        data = reporting.top_quoted_parts(n)
        report_df = pd.DataFrame(data)
        report_filename = "top_quoted_parts.csv"
        st.dataframe(report_df, use_container_width=True, hide_index=True)

    elif report_choice == "Least Quoted Parts":
        n = st.slider("Number of parts to show", 5, 25, 10)
        data = reporting.least_quoted_parts(n)
        report_df = pd.DataFrame(data)
        report_filename = "least_quoted_parts.csv"
        st.dataframe(report_df, use_container_width=True, hide_index=True)

    elif report_choice == "Pipeline Status Breakdown":
        st.metric("Overall Win Rate", f"{reporting.win_rate_pct()}%")
        data = reporting.win_rate_summary()
        report_df = pd.DataFrame(data)
        report_filename = "pipeline_status_breakdown.csv"
        st.dataframe(report_df, use_container_width=True, hide_index=True)

    elif report_choice == "Follow-Up by Employee":
        days = st.slider("Days since sent (threshold)", 1, 30, 7)

        st.subheader("Summary by Employee")
        summary_data = follow_up.get_follow_up_summary_by_employee(days_since_sent=days)
        summary_df = pd.DataFrame(summary_data)
        st.dataframe(summary_df, use_container_width=True, hide_index=True)

        st.subheader("Detail")
        detail_data = follow_up.get_quotes_needing_follow_up(days_since_sent=days)
        detail_df = pd.DataFrame(detail_data)
        if not detail_df.empty:
            employees = ["All"] + sorted(detail_df["created_by"].dropna().unique().tolist())
            selected_employee = st.selectbox("Filter by employee", employees)
            if selected_employee != "All":
                detail_df = detail_df[detail_df["created_by"] == selected_employee]
            detail_df = detail_df.rename(columns={"created_by": "Prepared By"})
        st.dataframe(detail_df, use_container_width=True, hide_index=True)
        report_df = detail_df
        report_filename = "follow_up_by_employee.csv"

    if not report_df.empty:
        csv_bytes = report_df.to_csv(index=False).encode("utf-8")
        st.download_button("Download as CSV", csv_bytes, file_name=report_filename, mime="text/csv")
    else:
        st.caption("No data available for this report yet.")


def settings_page():
    if user_role not in ADMIN_ROLES:
        st.error("You don't have permission to view this page.")
        st.stop()

    st.title("⚙️ Settings")
    st.caption("Customize branding for this deployment. Changes apply immediately -- no restart needed.")

    current = load_branding()

    st.subheader("Company Info")
    with st.form("branding_form"):
        col1, col2 = st.columns(2)
        with col1:
            company_name = st.text_input("Company Name", value=current["company_name"])
            company_phone = st.text_input("Phone", value=current["company_phone"])
        with col2:
            company_email = st.text_input("Email", value=current["company_email"])
            brand_color = st.color_picker("Brand Color (used on PDF quotes)", value=current["brand_color"])
        company_address = st.text_input("Address", value=current["company_address"])

        submitted = st.form_submit_button("Save Branding", type="primary")
        if submitted:
            save_branding_override(
                company_name=company_name,
                company_address=company_address,
                company_phone=company_phone,
                company_email=company_email,
                brand_color=brand_color,
            )
            st.success("Branding saved.")
            st.rerun()

    st.divider()
    st.subheader("Logo")

    if current.get("logo_path") and Path(current["logo_path"]).exists():
        st.image(current["logo_path"], width=140, caption="Current logo")
    else:
        st.caption("No logo uploaded yet -- quotes will show the company name only.")

    uploaded = st.file_uploader("Upload a logo (PNG or JPG)", type=["png", "jpg", "jpeg"])
    if uploaded is not None:
        suffix = ".png" if uploaded.type == "image/png" else ".jpg"
        logo_path = save_logo(uploaded.getvalue(), suffix=suffix)
        save_branding_override(logo_path=logo_path)
        st.success("Logo uploaded.")
        st.rerun()

    st.caption(
        "Note: on a deployed app, uploaded files live on that instance's disk and "
        "may not survive a redeploy or restart. For a permanent logo on a live "
        "deployment, set the QUOTE_COMPANY_LOGO path via Secrets/environment "
        "variables instead, pointing at a file committed to the repo."
    )


def main():
    branding = load_branding()
    inject_custom_css(branding["brand_color"])

    if branding.get("logo_path") and Path(branding["logo_path"]).exists():
        st.sidebar.image(branding["logo_path"], width=100)
    st.sidebar.title(branding["company_name"])
    st.sidebar.caption("Replaces the Master Price List + Quote Template workflow.")
    st.sidebar.write(f"Logged in as **{name}**")
    authenticator.logout("Log out", "sidebar")

    nav_options = ["New Quote", "Dashboard"]
    is_admin = user_role in ADMIN_ROLES
    if is_admin:
        nav_options.extend(["Reports", "Settings"])

    page = st.sidebar.radio("Navigate", nav_options)

    if page == "New Quote":
        new_quote_page()
    elif page == "Dashboard":
        dashboard_page()
    elif page == "Reports" and is_admin:
        reports_page()
    elif page == "Settings" and is_admin:
        settings_page()


if __name__ == "__main__":
    main()
