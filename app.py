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
    mark_quote_sent, UnknownServiceOrderError, UnknownPartError, UnknownQuoteError,
    remove_line_item, compute_pretax_subtotal, apply_state_tax, remove_tax,
    get_linked_service_orders, get_quotes_for_service_order,
    find_open_quotes_for_job, search_quotes,
    get_revisions, get_current_revision, load_draft_from_quote, save_revision,
)
import activity
from pdf_generator import generate_pdf
from db import get_connection, get_dict_cursor
import reporting
import follow_up
import tax
import account_alerts
import intake
import parts_parser
import pricing_checks
import technicians as tech_mod
import contractors as gc_mod
import templates as tpl_mod
from config.branding import load_branding, save_branding_override, save_logo
from config.themes import DEFAULT_THEME, apply_theme, build_themed_bar_chart

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

# Friendly display names for raw database column names. Streamlit renders
# dataframe headers on a canvas in a fixed muted style that no theme
# setting or injected CSS can darken or embolden, so clear, properly
# capitalized wording is the main lever available for readability.
COLUMN_LABELS = {
    "quote_number": "Quote #",
    "company_name": "Contractor",
    "customer_total": "Customer Total",
    "contractor_total": "Contractor Cost",
    "margin": "Margin",
    "jobs": "Jobs",
    "full_name": "Technician",
    "employee_code": "Employee #",
    "region": "Region",
    "service_order_no": "Service Order",
    "account_name": "Account",
    "contact_name": "Contact",
    "contact_email": "Email",
    "days_since_sent": "Days Since Sent",
    "quote_total": "Total",
    "created_by": "Prepared By",
    "part_number": "Part #",
    "description": "Description",
    "times_quoted": "Times Quoted",
    "total_quantity": "Total Qty",
    "total_quoted_value": "Total Quoted Value",
    "accepted_quotes": "Accepted Quotes",
    "accepted_revenue": "Accepted Revenue",
    "total_quotes": "Total Quotes",
    "status": "Status",
    "quote_count": "Quote Count",
    "pct_of_total": "% of Total",
    "total_value": "Total Value",
    "quotes_needing_follow_up": "Quotes Needing Follow-Up",
}


def friendly_columns(df):
    """Renames raw DB column names to readable labels for display."""
    return df.rename(columns=COLUMN_LABELS)


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


def get_all_parts():
    conn = get_connection()
    cur = get_dict_cursor(conn)
    cur.execute("SELECT part_number, description FROM parts ORDER BY part_number")
    parts = cur.fetchall()
    cur.close()
    conn.close()
    return parts


def new_quote_page():
    st.title("📋 Create Quote")
    st.caption("Replaces the scratch sheet: enter a service order number, everything else auto-populates.")

    if "draft" not in st.session_state:
        st.session_state.draft = None

    # Arriving from the Intake queue via "Create Quote": load the service
    # order immediately so the quote builder is already populated.
    pending_so = st.session_state.pop("pending_service_order", None)
    if pending_so:
        try:
            st.session_state.draft = start_quote_from_service_order(pending_so)
            st.session_state.last_quote_number = None
        except UnknownServiceOrderError as e:
            st.error(str(e))

    if st.session_state.get("pending_intake_id") and st.session_state.get("draft"):
        st.info(
            "Building a quote from an Intake request. It'll be marked as quoted "
            "automatically once you generate the quote."
        )

    # Parsed parts from the intake request. Shown for confirmation rather
    # than added automatically -- free text is occasionally ambiguous, and
    # a silently wrong quote is worse than one that took a few more seconds.
    parts_text = st.session_state.get("pending_parts_text")
    if parts_text and st.session_state.get("draft"):
        parsed = parts_parser.parse_parts_text(parts_text)
        if parsed:
            with st.expander(
                f"Parts from the intake request -- {parts_parser.summarize(parsed)}",
                expanded=True,
            ):
                for idx, item in enumerate(parsed):
                    c1, c2, c3 = st.columns([4, 1, 1.4])
                    if item["matched"]:
                        c1.markdown(f"**{item['part_number']}** — {item['description']}")
                    else:
                        c1.markdown(f"_{item['raw']}_")
                        c1.caption("No catalog match — add manually as a custom item if needed.")
                    c2.markdown(f"Qty {item['quantity']}")
                    if item["matched"]:
                        if c3.button("Add", key=f"addparsed_{idx}"):
                            try:
                                add_line_item(st.session_state.draft,
                                              item["part_number"], item["quantity"])
                                st.rerun()
                            except UnknownPartError as e:
                                st.error(str(e))

                matched = [p for p in parsed if p["matched"]]
                if matched and st.button("Add all matched parts", type="primary"):
                    for item in matched:
                        try:
                            add_line_item(st.session_state.draft,
                                          item["part_number"], item["quantity"])
                        except UnknownPartError:
                            pass
                    st.session_state.pending_parts_text = None
                    st.rerun()

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

    # Wrapped in a form so pressing Enter in the text box submits the
    # lookup. A bare st.text_input + st.button does not do this -- Enter
    # just re-runs the script without triggering the button, which forced
    # the user to reach for the mouse on every single lookup.
    with st.form("service_order_lookup", clear_on_submit=False):
        col1, col2 = st.columns([2, 1])
        with col1:
            service_order_no = st.text_input(
                "Service Order Number", placeholder="e.g. 500125 or 211639",
            )
        with col2:
            st.write("")
            st.write("")
            submitted = st.form_submit_button("Look Up", type="primary")

    if submitted:
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

    render_duplicate_warning(draft.service_order_no)
    render_linked_orders(draft.service_order_no)

    account_alert_rows = account_alerts.get_alerts_for_account(draft.account_id)
    alert_messages = [row["message"] for row in account_alert_rows]
    if tax.is_account_tax_exempt(draft.account_id):
        alert_messages.append("This account is tax exempt.")
    if alert_messages:
        st.warning("**Account Alerts:**\n\n" + "\n".join(f"- {msg}" for msg in alert_messages))

    st.divider()

    # Templates for recurring work (mod kits etc). Populates the draft
    # only -- the user still reviews, edits, and generates as normal.
    try:
        available_templates = tpl_mod.list_templates()
    except Exception:
        available_templates = []

    if available_templates:
        use_template = st.checkbox("Create from template", key="use_template")
        if use_template:
            tcol1, tcol2 = st.columns([3, 1])
            names = [t["name"] for t in available_templates]
            with tcol1:
                chosen = st.selectbox("Template", options=names, key="template_pick")
            with tcol2:
                st.write("")
                st.write("")
                apply_clicked = st.button("Apply Template")

            chosen_t = next((t for t in available_templates if t["name"] == chosen), None)
            if chosen_t:
                if chosen_t.get("description"):
                    st.caption(chosen_t["description"])
                items = tpl_mod.get_template_items(chosen_t["template_id"])
                preview = []
                for i in items:
                    price = (f"${float(i['fixed_price']):,.2f} flat"
                             if i["fixed_price"] is not None else "priced for this account")
                    preview.append(f"- {i['description']} ×{i['quantity']} — {price}")
                st.caption("Will add:\n\n" + "\n".join(preview))

            if apply_clicked and chosen_t:
                result = tpl_mod.apply_to_draft(draft, chosen_t["template_id"])
                # Stash the message rather than showing it here: st.rerun()
                # below wipes anything rendered in this pass, so a message
                # shown now would vanish before the user ever sees it.
                if result["skipped"]:
                    st.session_state.template_msg = (
                        "warning",
                        f"Added {result['added']} line(s) from '{chosen}'. "
                        f"Skipped (not in catalog): {', '.join(result['skipped'])}",
                    )
                else:
                    st.session_state.template_msg = (
                        "success",
                        f"Added {result['added']} line(s) from '{chosen}'. "
                        f"Review and adjust below before generating the quote.",
                    )
                st.rerun()

        msg = st.session_state.pop("template_msg", None)
        if msg:
            kind, text = msg
            (st.success if kind == "success" else st.warning)(text)

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

    with st.expander("Add a Charge (Trip, Labor, Fuel, Hardware, or Custom)"):
        charge_type = st.selectbox(
            "Charge Type",
            ["Trip Charge", "Labor", "Fuel Charge", "Hardware", "Custom"],
            key="charge_type_select",
        )
        default_description = "" if charge_type == "Custom" else charge_type
        custom_col1, custom_col2, custom_col3 = st.columns([3, 1, 1])
        with custom_col1:
            custom_description = st.text_input(
                "Description", value=default_description, key=f"charge_description_{charge_type}",
            )
        with custom_col2:
            custom_qty = st.number_input("Qty", min_value=1, value=1, step=1, key=f"charge_qty_{charge_type}")
        with custom_col3:
            custom_price = st.number_input("Unit Price ($)", min_value=0.0, value=0.0, step=0.01, key=f"charge_price_{charge_type}")
        if st.button("Add Charge"):
            if not custom_description.strip():
                st.error("Enter a description.")
            else:
                add_custom_line_item(draft, custom_description.strip(), custom_qty, custom_price)
                st.rerun()

    st.divider()
    st.subheader("Tax")
    account_tax_exempt = tax.is_account_tax_exempt(draft.account_id)
    if account_tax_exempt:
        st.info(f"**{draft.account_name}** is marked tax-exempt. No sales tax will be applied.")
    else:
        detected_state = tax.extract_state_from_address(draft.site_address)
        if not detected_state:
            st.warning(
                "Couldn't determine the state from the service address, so tax "
                "can't be calculated automatically. Add it manually as a custom "
                "charge above if needed."
            )
        else:
            state_rate = tax.get_tax_rate(detected_state)
            if state_rate is None:
                st.warning(f"No tax rate configured for {detected_state}. Set one in Settings > Tax Rates first.")
            else:
                has_tax_line = any(li.item_type == "tax" for li in draft.line_items)
                if has_tax_line:
                    st.caption(
                        f"Tax currently applied: {detected_state} @ {state_rate * 100:.2f}%. "
                        f"If you add more items above, click below to recalculate."
                    )
                else:
                    st.caption(f"Detected location: {detected_state} (rate: {state_rate * 100:.2f}%)")
                tax_col1, tax_col2 = st.columns(2)
                with tax_col1:
                    if st.button("Apply / Recalculate Tax"):
                        apply_state_tax(draft, detected_state, state_rate)
                        st.rerun()
                with tax_col2:
                    if has_tax_line and st.button("Remove Tax"):
                        remove_tax(draft)
                        st.rerun()

    if draft.line_items:
        st.subheader("Quote Detail")
        df = pd.DataFrame([{
            "Part #": li.part_number or ("(tax)" if li.item_type == "tax" else "(custom)"),
            "Description": li.description,
            "Qty": li.quantity,
            "Unit Price": f"${li.unit_price:,.2f}",
            "Line Total": f"${li.line_total:,.2f}",
        } for li in draft.line_items])
        st.dataframe(df, use_container_width=True, hide_index=True)

        tax_line = next((li for li in draft.line_items if li.item_type == "tax"), None)
        if tax_line:
            st.markdown(f"**Subtotal:** ${compute_pretax_subtotal(draft):,.2f}")
            st.markdown(f"**Tax:** ${tax_line.line_total:,.2f}")
        st.markdown(f"### Total: ${draft.total:,.2f}")

        remove_options = ["-- select an item --"] + [
            f"{i}: {li.description} (${li.line_total:,.2f})" for i, li in enumerate(draft.line_items)
        ]
        col_r1, col_r2 = st.columns([3, 1])
        with col_r1:
            to_remove = st.selectbox("Remove an item", options=remove_options, key="remove_item_select")
        with col_r2:
            st.write("")
            st.write("")
            if st.button("Remove"):
                if to_remove != "-- select an item --":
                    idx = int(to_remove.split(":")[0])
                    remove_line_item(draft, idx)
                    st.rerun()

        tech_id = render_technician_picker(draft.service_order_no, key_prefix="new")
        render_contractor_section(draft)

        col1, col2 = st.columns(2)
        with col1:
            created_by = st.text_input("Prepared by", value="Quote Admin")
        with col2:
            st.write("")

        render_quote_guardrails(draft)

        if st.button("Generate Quote & PDF", type="primary"):
            quote_number = save_quote(draft, created_by=created_by)
            current = get_current_revision(quote_number)
            if current and tech_id:
                apply_technician_selection(current["quote_id"], tech_id)
            if current and st.session_state.get("new_contractor_id"):
                gc_mod.assign_to_quote(current["quote_id"], st.session_state["new_contractor_id"])
                gc_mod.set_line_costs(current["quote_id"],
                                      st.session_state.get("new_contractor_costs", {}))
            pdf_path = generate_pdf(quote_number, output_dir="output")

            intake_id = st.session_state.pop("pending_intake_id", None)
            if intake_id:
                try:
                    intake.mark_quoted(intake_id, current["quote_id"] if current else None)
                except Exception:
                    pass

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

    theme_name = DEFAULT_THEME

    with col1:
        st.subheader("Revenue by Account (Accepted Quotes)")
        rev_df = pd.DataFrame(reporting.revenue_by_account())
        rev_df["accepted_revenue"] = rev_df["accepted_revenue"].fillna(0).astype(float)
        chart1 = build_themed_bar_chart(rev_df, "account_name", "accepted_revenue", theme_name)
        st.altair_chart(chart1, use_container_width=True, theme=None)

    with col2:
        st.subheader("Top Quoted Parts (by $ value)")
        parts_df = pd.DataFrame(reporting.top_quoted_parts(8))
        chart2 = build_themed_bar_chart(parts_df, "part_number", "total_quoted_value", theme_name)
        st.altair_chart(chart2, use_container_width=True, theme=None)

    st.divider()
    st.subheader("Intake → Quote Turnaround")
    try:
        cycle = reporting.intake_to_quote_cycle_time()
        m1, m2, m3 = st.columns(3)
        avg_h = cycle["avg_hours"]
        m1.metric("Avg hours to quote", f"{float(avg_h):.1f}" if avg_h is not None else "—")
        m2.metric("Requests quoted", cycle["quoted_count"])
        waiting = cycle["longest_waiting_hours"]
        m3.metric("Longest still waiting",
                  f"{float(waiting):.1f}h" if waiting is not None else "—")
        backlog = reporting.intake_backlog()
        if backlog:
            st.caption(f"{len(backlog)} request(s) still waiting on a quote:")
            bl = pd.DataFrame(backlog).rename(columns={
                "service_order_no": "Service Order", "account_name": "Account",
                "submitted_by": "Submitted By", "submitted_at": "Submitted",
                "hours_waiting": "Hours Waiting",
            })
            st.dataframe(bl, use_container_width=True, hide_index=True)
    except Exception:
        st.caption("Turnaround stats unavailable (run the intake migration first).")

    st.divider()
    st.subheader("⚠️ Needs Follow-Up (sent 7+ days ago, no response)")
    follow_up_list = follow_up.get_quotes_needing_follow_up(days_since_sent=7)
    if follow_up_list:
        fu_df = pd.DataFrame(follow_up_list)
        fu_df["quote_total"] = fu_df["quote_total"].astype(float).map(lambda x: f"${x:,.2f}")
        # Friendly, properly-capitalized headers instead of raw database
        # column names. Streamlit renders dataframe headers on a canvas in
        # a fixed muted style that no theme setting or CSS can darken or
        # embolden, so clear wording and spacing is the lever we actually
        # have for making these readable.
        fu_df = fu_df.rename(columns={
            "quote_number": "Quote #",
            "service_order_no": "Service Order",
            "account_name": "Account",
            "contact_name": "Contact",
            "contact_email": "Email",
            "days_since_sent": "Days Since Sent",
            "quote_total": "Total",
            "created_by": "Prepared By",
        })
        st.dataframe(
            fu_df[["Quote #", "Service Order", "Account", "Contact", "Email",
                   "Days Since Sent", "Total", "Prepared By"]],
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
        "Intake Turnaround",
        "Quotes by Technician",
        "Contractor Margin",
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
        st.dataframe(friendly_columns(report_df), use_container_width=True, hide_index=True)

    elif report_choice == "Total Quoted Value by Account (All Quotes)":
        data = reporting.total_quoted_value_by_account()
        report_df = pd.DataFrame(data)
        report_filename = "total_quoted_value_by_account.csv"
        st.dataframe(friendly_columns(report_df), use_container_width=True, hide_index=True)

    elif report_choice == "Top Quoted Parts":
        n = st.slider("Number of parts to show", 5, 25, 10)
        data = reporting.top_quoted_parts(n)
        report_df = pd.DataFrame(data)
        report_filename = "top_quoted_parts.csv"
        st.dataframe(friendly_columns(report_df), use_container_width=True, hide_index=True)

    elif report_choice == "Least Quoted Parts":
        n = st.slider("Number of parts to show", 5, 25, 10)
        data = reporting.least_quoted_parts(n)
        report_df = pd.DataFrame(data)
        report_filename = "least_quoted_parts.csv"
        st.dataframe(friendly_columns(report_df), use_container_width=True, hide_index=True)

    elif report_choice == "Pipeline Status Breakdown":
        st.metric("Overall Win Rate", f"{reporting.win_rate_pct()}%")
        data = reporting.win_rate_summary()
        report_df = pd.DataFrame(data)
        report_filename = "pipeline_status_breakdown.csv"
        st.dataframe(friendly_columns(report_df), use_container_width=True, hide_index=True)

    elif report_choice == "Contractor Margin":
        st.caption("Internal only — contractor pricing is never shown to customers.")
        report_df = pd.DataFrame(gc_mod.contractor_margin_report())
        report_filename = "contractor_margin.csv"
        st.dataframe(friendly_columns(report_df), use_container_width=True, hide_index=True)

    elif report_choice == "Quotes by Technician":
        st.caption("Internal only — technician names never appear on customer-facing quotes.")
        report_df = pd.DataFrame(tech_mod.quotes_by_technician())
        report_filename = "quotes_by_technician.csv"
        st.dataframe(friendly_columns(report_df), use_container_width=True, hide_index=True)

    elif report_choice == "Intake Turnaround":
        cycle = reporting.intake_to_quote_cycle_time()
        c1, c2 = st.columns(2)
        avg_h = cycle["avg_hours"]
        c1.metric("Avg hours from intake to quote",
                  f"{float(avg_h):.1f}" if avg_h is not None else "—")
        c2.metric("Requests still pending", cycle["pending_count"])
        report_df = pd.DataFrame(reporting.intake_backlog())
        report_filename = "intake_turnaround.csv"
        st.dataframe(friendly_columns(report_df), use_container_width=True, hide_index=True)

    elif report_choice == "Follow-Up by Employee":
        days = st.slider("Days since sent (threshold)", 1, 30, 7)

        st.subheader("Summary by Employee")
        summary_data = follow_up.get_follow_up_summary_by_employee(days_since_sent=days)
        summary_df = pd.DataFrame(summary_data)
        st.dataframe(friendly_columns(summary_df), use_container_width=True, hide_index=True)

        st.subheader("Detail")
        detail_data = follow_up.get_quotes_needing_follow_up(days_since_sent=days)
        detail_df = pd.DataFrame(detail_data)
        if not detail_df.empty:
            employees = ["All"] + sorted(detail_df["created_by"].dropna().unique().tolist())
            selected_employee = st.selectbox("Filter by employee", employees)
            if selected_employee != "All":
                detail_df = detail_df[detail_df["created_by"] == selected_employee]
            detail_df = detail_df.rename(columns={"created_by": "Prepared By"})
        st.dataframe(friendly_columns(detail_df), use_container_width=True, hide_index=True)
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

    st.divider()
    st.subheader("State Tax Rates")
    st.caption(
        "Base state sales tax rates only (no county/city/local add-ons). "
        "These are a starting-point reference, not guaranteed accurate or "
        "current -- verify against your state's Department of Revenue before "
        "relying on these for real invoicing."
    )
    tax_rates = tax.get_all_tax_rates()
    tax_df = pd.DataFrame(tax_rates)
    if not tax_df.empty:
        tax_df["rate_pct"] = (tax_df["rate"].astype(float) * 100).round(3)
        edited_tax_df = st.data_editor(
            tax_df[["state_code", "state_name", "rate_pct"]],
            column_config={
                "state_code": st.column_config.TextColumn("State", disabled=True),
                "state_name": st.column_config.TextColumn("Name", disabled=True),
                "rate_pct": st.column_config.NumberColumn("Rate (%)", min_value=0.0, max_value=15.0, step=0.01),
            },
            hide_index=True,
            use_container_width=True,
            key="tax_rate_editor",
            height=300,
        )
        if st.button("Save Tax Rates", type="primary"):
            changed = 0
            for _, row in edited_tax_df.iterrows():
                original_rate = tax_df.loc[tax_df["state_code"] == row["state_code"], "rate_pct"].values[0]
                if abs(row["rate_pct"] - original_rate) > 1e-9:
                    tax.update_tax_rate(row["state_code"], row["rate_pct"] / 100)
                    changed += 1
            if changed:
                st.success(f"Updated {changed} state tax rate(s).")
            else:
                st.info("No changes to save.")
            st.rerun()

    st.divider()
    st.subheader("Account Tax Exemptions")
    st.caption(
        "Toggle which customer accounts are tax-exempt (e.g. government, "
        "nonprofit, resale certificate on file). This applies at every "
        "location for that account, regardless of which state the work is in."
    )
    accounts_status = tax.get_all_accounts_tax_status()
    acc_df = pd.DataFrame(accounts_status)
    if not acc_df.empty:
        edited_acc_df = st.data_editor(
            acc_df[["account_name", "tax_exempt"]],
            column_config={
                "account_name": st.column_config.TextColumn("Account", disabled=True),
                "tax_exempt": st.column_config.CheckboxColumn("Tax Exempt"),
            },
            hide_index=True,
            use_container_width=True,
            key="tax_exempt_editor",
        )
        if st.button("Save Account Exemptions", type="primary"):
            changed = 0
            for i, row in edited_acc_df.iterrows():
                if bool(row["tax_exempt"]) != bool(acc_df.iloc[i]["tax_exempt"]):
                    tax.set_account_tax_exempt(int(acc_df.iloc[i]["account_id"]), bool(row["tax_exempt"]))
                    changed += 1
            if changed:
                st.success(f"Updated {changed} account(s).")
            else:
                st.info("No changes to save.")
            st.rerun()

    st.divider()
    st.subheader("Quote Templates")
    st.caption(
        "Reusable line-item sets for recurring work like modernization kits. "
        "Leave the price blank on a catalog part to have it priced per account "
        "at quote time; set a price to make it a flat rate that doesn't vary."
    )
    try:
        all_templates = tpl_mod.list_templates(active_only=False)
        for t in all_templates:
            if not t["is_active"]:
                continue
            with st.expander(f"{t['name']} — {t['item_count']} item(s)"):
                if t.get("description"):
                    st.caption(t["description"])
                for item in tpl_mod.get_template_items(t["template_id"]):
                    ic1, ic2, ic3 = st.columns([4, 1.5, 0.8])
                    label = item["description"]
                    if item["part_number"]:
                        label = f"{item['part_number']} — {label}"
                    ic1.markdown(label)
                    ic2.caption(
                        f"×{item['quantity']} · "
                        + (f"${float(item['fixed_price']):,.2f} flat"
                           if item["fixed_price"] is not None else "account pricing")
                    )
                    if ic3.button("✕", key=f"tplitem_del_{item['id']}", help="Remove this line"):
                        tpl_mod.remove_item(item["id"])
                        st.rerun()

                st.markdown("**Add a line**")
                parts_list = get_all_parts()
                opts = ["(custom / flat-rate line)"] + [
                    f"{p['part_number']} — {p['description']}" for p in parts_list
                ]
                ac1, ac2, ac3 = st.columns([3, 1, 1])
                with ac1:
                    pick = st.selectbox("Part", options=opts, key=f"tpl_part_{t['template_id']}")
                with ac2:
                    q = st.number_input("Qty", min_value=1, value=1, step=1,
                                        key=f"tpl_qty_{t['template_id']}")
                with ac3:
                    fp = st.number_input("Flat price (0 = account pricing)", min_value=0.0,
                                         value=0.0, step=0.01, key=f"tpl_price_{t['template_id']}")
                custom_desc = ""
                if pick == opts[0]:
                    custom_desc = st.text_input("Description", key=f"tpl_desc_{t['template_id']}")
                if st.button("Add Line", key=f"tpl_add_{t['template_id']}"):
                    if pick == opts[0]:
                        if not custom_desc.strip():
                            st.error("Enter a description for the custom line.")
                        else:
                            tpl_mod.add_item(t["template_id"], custom_desc.strip(), q,
                                             fixed_price=fp if fp > 0 else 0.0)
                            st.rerun()
                    else:
                        pn = pick.split("—")[0].strip()
                        desc = pick.split("—", 1)[1].strip()
                        tpl_mod.add_item(t["template_id"], desc, q, part_number=pn,
                                         fixed_price=fp if fp > 0 else None)
                        st.rerun()

                if st.button("Deactivate this template", key=f"tpl_del_{t['template_id']}"):
                    tpl_mod.delete_template(t["template_id"])
                    st.rerun()

        st.markdown("**Create a new template**")
        nc1, nc2 = st.columns([1, 2])
        with nc1:
            new_tpl_name = st.text_input("Name", placeholder="e.g. Mod Kit", key="new_tpl_name")
        with nc2:
            new_tpl_desc = st.text_input("Description", key="new_tpl_desc")
        if st.button("Create Template"):
            if not new_tpl_name.strip():
                st.error("Name is required.")
            else:
                try:
                    tpl_mod.create_template(new_tpl_name.strip(), new_tpl_desc.strip(), name)
                    st.success(f"Created '{new_tpl_name.strip()}'. Add lines to it above.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Could not create template: {e}")
    except Exception:
        st.caption("Templates unavailable (run migrations first).")

    st.divider()
    st.subheader("Technician Roster")
    st.caption(
        "Technicians don't have logins — they never access this system. This is a "
        "roster for internal record keeping only, and these names are never shown "
        "on customer-facing quotes. Deactivate rather than delete so historical "
        "quotes keep pointing at a real name."
    )
    try:
        roster = tech_mod.list_technicians(active_only=False)
        if roster:
            st.dataframe(
                pd.DataFrame(roster).rename(columns={
                    "full_name": "Technician", "employee_code": "Employee #",
                    "region": "Region", "is_active": "Active",
                })[["Technician", "Employee #", "Region", "Active"]],
                use_container_width=True, hide_index=True,
            )
        tc1, tc2, tc3 = st.columns([2, 1, 1])
        with tc1:
            new_tech_name = st.text_input("Name", key="new_tech_name")
        with tc2:
            new_tech_code = st.text_input("Employee #", key="new_tech_code")
        with tc3:
            new_tech_region = st.text_input("Region", key="new_tech_region")
        if st.button("Add Technician"):
            if not new_tech_name.strip():
                st.error("Name is required.")
            else:
                tech_mod.add_technician(new_tech_name.strip(),
                                        new_tech_code.strip() or None,
                                        new_tech_region.strip() or None)
                st.success("Technician added.")
                st.rerun()
    except Exception:
        st.caption("Technician roster unavailable (run migrations first).")

    st.divider()
    st.subheader("Account Alerts")
    st.caption(
        "Per-account instructions that show up automatically whenever a "
        "quote is being prepared for that account -- e.g. \"no Hardware or "
        "Fuel charges,\" \"submit via their portal to Jane Doe,\" \"onsite "
        "work pre-approved up to $2,000.\""
    )

    all_accounts = account_alerts.get_all_accounts()
    if all_accounts:
        account_names = {a["account_name"]: a["account_id"] for a in all_accounts}
        selected_account_name = st.selectbox("Account", options=sorted(account_names.keys()), key="alert_account_select")
        selected_account_id = account_names[selected_account_name]

        existing_alerts = account_alerts.get_alerts_for_account(selected_account_id)
        if existing_alerts:
            st.write("**Current alerts:**")
            for alert in existing_alerts:
                alert_col1, alert_col2 = st.columns([5, 1])
                with alert_col1:
                    st.write(f"- {alert['message']}")
                with alert_col2:
                    if st.button("Remove", key=f"remove_alert_{alert['id']}"):
                        account_alerts.remove_alert(alert["id"])
                        st.rerun()
        else:
            st.caption("No alerts set for this account yet.")

        new_alert_text = st.text_input("New alert message", key="new_alert_text")
        if st.button("Add Alert"):
            if not new_alert_text.strip():
                st.error("Enter an alert message.")
            else:
                account_alerts.add_alert(selected_account_id, new_alert_text.strip())
                st.success("Alert added.")
                st.rerun()


def render_linked_orders(service_order_no: str) -> None:
    """Shows the other half of the job. A 2xxxxx number is the initial
    diagnostic trip and a 5xxxxx is the return trip to do the work;
    roughly 80% of jobs have both, so whichever number someone looked up,
    they need to see its counterpart and any quotes already on it."""
    try:
        linked = get_linked_service_orders(service_order_no)
    except Exception:
        return
    this_order = linked.get("this")
    if not this_order:
        return

    type_label = "Initial trip" if this_order["order_type"] == "initial" else "Return trip"
    bits = [f"**{type_label}** ({service_order_no})"]
    if this_order.get("nte_amount") is not None:
        bits.append(f"NTE on file: **${float(this_order['nte_amount']):,.2f}**")
    st.caption("  |  ".join(bits))

    related = []
    if linked.get("parent"):
        related.append(("Initial trip", linked["parent"]))
    for child in linked.get("children", []):
        related.append(("Return trip", child))

    if not related:
        return

    with st.expander(f"Linked service orders ({len(related)})", expanded=True):
        for label, order in related:
            cols = st.columns([1.2, 1, 2, 1.4])
            cols[0].markdown(f"**{order['service_order_no']}**")
            cols[1].markdown(label)
            cols[2].markdown(order.get("description") or "-")
            nte = order.get("nte_amount")
            cols[3].markdown(f"NTE ${float(nte):,.2f}" if nte is not None else "No NTE")

            quotes = get_quotes_for_service_order(order["service_order_no"])
            if quotes:
                for q in quotes:
                    rev = f" Rev {q['revision_number']}" if q["revision_number"] > 1 else ""
                    st.caption(
                        f"     ↳ {q['quote_number']}{rev} — {q['status']} — "
                        f"${float(q['quote_total']):,.2f} (by {q['created_by']})"
                    )
            else:
                st.caption("     ↳ no quotes yet")


def render_activity_trail(quote_number: str) -> None:
    """Who did what to this quote, across every revision."""
    rows = activity.get_activity_for_quote_number(quote_number)
    if not rows:
        st.caption("No activity recorded yet.")
        return
    for r in rows:
        stamp = r["performed_at"].strftime("%b %d, %Y at %I:%M %p")
        st.markdown(
            f"- **{activity.describe(r['action'])}** by **{r['performed_by']}** "
            f"— Rev {r['revision_number']} — {stamp}"
        )
        if r.get("detail"):
            st.caption(f"   {r['detail']}")


def apply_technician_selection(quote_id: int, selection) -> None:
    """Persists primary/second technician and the tech count."""
    if not selection:
        return
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            "UPDATE quotes SET technician_id = %s, secondary_technician_id = %s, tech_count = %s "
            "WHERE quote_id = %s",
            (selection.get("primary"), selection.get("secondary"),
             selection.get("tech_count", 1), quote_id),
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception:
        pass


def render_duplicate_warning(service_order_no: str) -> None:
    """Warns if this job already has an open quote.

    Checks the whole job family, not just this service order -- a job
    routinely spans an initial and a return order, so the duplicate is
    often sitting under the other number."""
    try:
        existing = find_open_quotes_for_job(service_order_no)
    except Exception:
        return
    if not existing:
        return
    lines = "\n".join(
        f"- **{q['quote_number']}** (SO {q['service_order_no']}) — {q['status']} — "
        f"${float(q['quote_total']):,.2f} by {q['created_by']}"
        for q in existing[:5]
    )
    st.warning(
        f"**This job already has {len(existing)} open quote(s).** Check these before "
        f"creating another:\n\n{lines}"
    )


def render_contractor_section(draft) -> None:
    """Assigns a GC and captures what they charge us per line.

    The customer never sees any of this -- not the pricing, not even that
    a contractor was involved."""
    try:
        roster = gc_mod.list_contractors()
    except Exception:
        return
    if not roster:
        return

    with st.expander("Subcontracted to a general contractor?"):
        st.caption(
            "Internal only. The customer's quote shows your pricing and makes no "
            "mention of a contractor. A separate contractor copy can be generated "
            "with their rates."
        )
        labels = ["(none — we're doing the work)"] + [c["company_name"] for c in roster]
        ids = [None] + [c["contractor_id"] for c in roster]
        pick = st.selectbox("General contractor", options=labels, index=0, key="new_contractor_pick")
        contractor_id = ids[labels.index(pick)]
        st.session_state["new_contractor_id"] = contractor_id

        if contractor_id and draft.line_items:
            st.markdown("**What the contractor charges us** (per unit)")
            costs = {}
            for idx, li in enumerate(draft.line_items):
                if li.item_type == "tax":
                    continue
                c1, c2, c3 = st.columns([3, 1, 1])
                c1.markdown(li.description)
                c2.caption(f"We charge ${li.unit_price:,.2f}")
                cost = c3.number_input(
                    "GC cost", min_value=0.0, value=0.0, step=0.01,
                    key=f"gccost_{idx}", label_visibility="collapsed",
                )
                if cost > 0:
                    costs[li.description] = cost
            st.session_state["new_contractor_costs"] = costs

            if costs:
                gc_total = sum(c * li.quantity for li, c in
                               [(li, costs[li.description]) for li in draft.line_items
                                if li.description in costs])
                margin = draft.total - gc_total
                m1, m2, m3 = st.columns(3)
                m1.metric("Customer pays", f"${draft.total:,.2f}")
                m2.metric("Contractor cost", f"${gc_total:,.2f}")
                m3.metric("Margin", f"${margin:,.2f}")


def pricing_history_page():
    st.title("💲 Account Pricing History")
    st.caption(
        "What a part has cost an account over time, and what they were actually "
        "quoted. Answers \"what did we quote them last time?\" without digging "
        "through old emails."
    )

    try:
        accounts = account_alerts.get_all_accounts()
    except Exception:
        st.error("Could not load accounts.")
        return
    if not accounts:
        return

    names = {a["account_name"]: a["account_id"] for a in accounts}
    chosen = st.selectbox("Account", options=sorted(names.keys()))
    account_id = names[chosen]

    tab_list, tab_part, tab_drift = st.tabs(["Price List", "Part History", "Price Drift"])

    with tab_list:
        rows = reporting.account_price_history(account_id)
        if not rows:
            st.info("No negotiated pricing on file for this account — quotes fall back to list price.")
        else:
            df = pd.DataFrame(rows)
            df["price"] = df["price"].astype(float)
            df["list_price"] = df["list_price"].astype(float)
            df["discount_pct"] = ((1 - df["price"] / df["list_price"]) * 100).round(1)
            df = df.rename(columns={
                "part_number": "Part #", "description": "Description", "price": "Account Price",
                "effective_date": "Effective", "expired_date": "Expired",
                "currently_in_effect": "In Effect", "list_price": "List Price",
                "discount_pct": "Discount %",
            })
            st.dataframe(
                df[["Part #", "Description", "Account Price", "List Price",
                    "Discount %", "Effective", "Expired", "In Effect"]],
                use_container_width=True, hide_index=True,
            )
            st.download_button("Download as CSV", df.to_csv(index=False).encode("utf-8"),
                               file_name=f"pricing_{chosen.replace(' ', '_')}.csv", mime="text/csv")

    with tab_part:
        parts_list = get_all_parts()
        popts = [f"{p['part_number']} — {p['description']}" for p in parts_list]
        picked = st.selectbox("Part", options=popts, key="hist_part")
        pn = picked.split("—")[0].strip()
        hist = reporting.part_quote_history(account_id, pn)
        if not hist:
            st.info(f"{pn} has never been quoted to {chosen}.")
        else:
            hdf = pd.DataFrame(hist)
            hdf["unit_price"] = hdf["unit_price"].astype(float).map(lambda x: f"${x:,.2f}")
            hdf["line_total"] = hdf["line_total"].astype(float).map(lambda x: f"${x:,.2f}")
            hdf = hdf.rename(columns={
                "quote_number": "Quote #", "revision_number": "Rev", "status": "Status",
                "created_at": "Quoted On", "created_by": "Prepared By",
                "quantity": "Qty", "unit_price": "Unit Price", "line_total": "Line Total",
            })
            st.dataframe(hdf, use_container_width=True, hide_index=True)
            st.caption(
                "Prices shown are what was on the quote at the time — not today's "
                "rate card. That's what matters if a customer questions a price."
            )

    with tab_drift:
        drift = reporting.price_variance_across_quotes(account_id)
        if not drift:
            st.success("No price drift — every part has been quoted at a consistent price.")
        else:
            ddf = pd.DataFrame(drift)
            for col in ("lowest_price", "highest_price"):
                ddf[col] = ddf[col].astype(float).map(lambda x: f"${x:,.2f}")
            ddf = ddf.rename(columns={
                "part_number": "Part #", "description": "Description",
                "distinct_prices": "Distinct Prices", "lowest_price": "Lowest",
                "highest_price": "Highest", "times_quoted": "Times Quoted",
                "last_quoted": "Last Quoted",
            })
            st.warning(f"{len(drift)} part(s) quoted to this account at more than one price.")
            st.dataframe(ddf, use_container_width=True, hide_index=True)


def quote_search_page():
    st.title("🔍 Find a Quote")
    st.caption("Search by quote number, account, service order, or who prepared it.")

    c1, c2 = st.columns([3, 1])
    with c1:
        term = st.text_input("Search", placeholder="e.g. Lakeshore, 500125, WAL-2026")
    with c2:
        status = st.selectbox("Status", ["Any", "draft", "sent", "accepted", "declined", "expired"])

    d1, d2 = st.columns(2)
    with d1:
        date_from = st.date_input("From", value=None)
    with d2:
        date_to = st.date_input("To", value=None)

    results = search_quotes(
        term=term.strip(),
        status=None if status == "Any" else status,
        date_from=date_from, date_to=date_to,
    )

    if not results:
        st.info("No quotes match those filters.")
        return

    st.caption(f"{len(results)} quote(s) found. Revisions are collapsed to the current version.")
    df = pd.DataFrame(results)
    df["quote_total"] = df["quote_total"].astype(float).map(lambda x: f"${x:,.2f}")
    df = friendly_columns(df.rename(columns={"revision_number": "Rev"}))
    st.dataframe(df, use_container_width=True, hide_index=True)
    st.download_button("Download as CSV", df.to_csv(index=False).encode("utf-8"),
                       file_name="quote_search.csv", mime="text/csv")


def render_technician_picker(service_order_no, key_prefix: str = "new"):
    """Records which technician worked the job.

    INTERNAL ONLY -- technician identity is deliberately kept off the
    customer-facing PDF to protect techs' identities. Pre-fills from
    whoever dispatch assigned to the service order, so this is usually
    just a confirmation rather than a lookup."""
    try:
        roster = tech_mod.list_technicians()
    except Exception:
        return None
    if not roster:
        return None

    assigned = None
    try:
        assigned = tech_mod.get_technician_for_service_order(service_order_no)
    except Exception:
        pass

    labels = [f"{t['full_name']} ({t['employee_code']})" for t in roster]
    ids = [t["technician_id"] for t in roster]
    default_index = ids.index(assigned["technician_id"]) if assigned and assigned["technician_id"] in ids else None

    choice = st.selectbox(
        "Technician on this job (internal only — not shown on the customer's quote)",
        options=labels, index=default_index, placeholder="Select the technician",
        key=f"{key_prefix}_technician",
    )

    two_techs = st.checkbox(
        "Two technicians on this job (billable second tech)",
        key=f"{key_prefix}_two_techs",
    )
    second_id = None
    if two_techs:
        remaining = [l for l in labels if l != choice]
        second = st.selectbox(
            "Second technician", options=remaining, index=None,
            placeholder="Select the second technician",
            key=f"{key_prefix}_technician_2",
        )
        if second in labels:
            second_id = ids[labels.index(second)]
        st.caption(
            "Remember to add the second tech's labor as a charge above — this "
            "records who was on the job, it doesn't price it automatically."
        )

    st.caption("Recorded for internal history. Technician names never appear on the customer PDF.")
    primary_id = ids[labels.index(choice)] if choice in labels else None
    return {"primary": primary_id, "secondary": second_id, "tech_count": 2 if two_techs else 1}


def render_quote_guardrails(draft) -> None:
    """Surfaces NTE overages and pricing anomalies before a quote goes out.

    These warn rather than block: every one of these checks has a
    legitimate exception (a genuine no-charge warranty part, a real bulk
    order), so the call belongs to the person, not the system."""
    try:
        results = pricing_checks.run_all_checks(
            account_id=draft.account_id,
            service_order_no=draft.service_order_no,
            line_items=draft.line_items,
            quote_total=draft.total,
        )
    except Exception:
        return

    if not results["has_issues"]:
        return

    st.divider()
    st.subheader("Before you send this")

    if results["nte"]:
        st.error(f"**NTE exceeded** — {results['nte']['message']}")

    for w in results["pricing"]:
        if w["severity"] == "high":
            st.warning(w["message"])
        else:
            st.info(w["message"])


def revise_quote_page():
    st.title("🔁 Revise a Quote")
    st.caption(
        "Add parts to an existing quote -- e.g. the tech went back out and the "
        "door still isn't working. The original revision is preserved exactly as "
        "it was quoted (and possibly already paid); this creates the next revision."
    )

    with st.form("revise_lookup"):
        col1, col2 = st.columns([2, 1])
        with col1:
            quote_number = st.text_input("Quote Number", placeholder="e.g. Q-2026-00056")
        with col2:
            st.write("")
            st.write("")
            find = st.form_submit_button("Load Quote", type="primary")

    if find and quote_number.strip():
        try:
            loaded, carried = load_draft_from_quote(quote_number.strip())
            st.session_state.revise_number = quote_number.strip()
            st.session_state.revise_draft = loaded
            st.session_state.revise_carried = carried
        except UnknownQuoteError as e:
            st.error(str(e))
            st.session_state.revise_draft = None

    draft = st.session_state.get("revise_draft")
    if draft is None:
        st.info("Enter an existing quote number above to revise it.")
        return

    qn = st.session_state.revise_number
    carried = st.session_state.get("revise_carried", [])
    carried_lookup = {c["description"]: c for c in carried}

    revisions = get_revisions(qn)
    current_rev = max((r["revision_number"] for r in revisions), default=1)
    st.success(f"**{qn} Rev {current_rev}**  |  {draft.account_name}  |  {draft.contact_name}")
    if draft.site_address:
        st.caption(f"Site: {draft.site_address}")

    render_linked_orders(draft.service_order_no)

    st.divider()
    st.subheader("Revision History")
    for r in revisions:
        marker = " (current)" if r["is_current"] else " (superseded)"
        st.markdown(
            f"- **Rev {r['revision_number']}**{marker} — {r['status']} — "
            f"by {r['created_by']} on {r['created_at']:%b %d, %Y}"
        )
        if r.get("revision_reason"):
            st.caption(f"   Reason: {r['revision_reason']}")

    with st.expander("Activity trail (who did what)"):
        render_activity_trail(qn)

    st.divider()
    st.subheader("Add Parts to This Quote")
    parts = get_all_parts()
    part_options = {f"{p['part_number']} — {p['description']}": p["part_number"] for p in parts}
    c1, c2, c3 = st.columns([3, 1, 1])
    with c1:
        selected = st.selectbox("Part", options=list(part_options.keys()), key="rev_part")
    with c2:
        qty = st.number_input("Qty", min_value=1, value=1, step=1, key="rev_qty")
    with c3:
        st.write("")
        st.write("")
        if st.button("Add Item", key="rev_add"):
            try:
                add_line_item(draft, part_options[selected], qty)
                st.rerun()
            except UnknownPartError as e:
                st.error(str(e))

    with st.expander("Add a Custom Item"):
        d1, d2, d3 = st.columns([3, 1, 1])
        with d1:
            cdesc = st.text_input("Description", key="rev_cdesc")
        with d2:
            cqty = st.number_input("Qty", min_value=1, value=1, step=1, key="rev_cqty")
        with d3:
            cprice = st.number_input("Unit Price ($)", min_value=0.0, value=0.0, step=0.01, key="rev_cprice")
        if st.button("Add Charge", key="rev_addcharge"):
            if not cdesc.strip():
                st.error("Enter a description.")
            else:
                add_custom_line_item(draft, cdesc.strip(), cqty, cprice)
                st.rerun()

    st.divider()
    st.subheader("Quote Detail")
    rows = []
    for li in draft.line_items:
        meta = carried_lookup.get(li.description)
        if meta and meta.get("first_quoted_at"):
            origin = f"Rev {meta['first_quoted_revision']} — {meta['first_quoted_at']:%b %d, %Y}"
        else:
            origin = "NEW on this revision"
        rows.append({
            "Description": li.description,
            "Qty": li.quantity,
            "Unit Price": f"${li.unit_price:,.2f}",
            "Line Total": f"${li.line_total:,.2f}",
            "First Quoted": origin,
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    st.markdown(f"### New Total: ${draft.total:,.2f}")

    remove_opts = ["-- select --"] + [
        f"{i}: {li.description} (${li.line_total:,.2f})" for i, li in enumerate(draft.line_items)
    ]
    rcol1, rcol2 = st.columns([3, 1])
    with rcol1:
        to_remove = st.selectbox("Remove an item", options=remove_opts, key="rev_remove")
    with rcol2:
        st.write("")
        st.write("")
        if st.button("Remove", key="rev_removebtn") and to_remove != "-- select --":
            remove_line_item(draft, int(to_remove.split(":")[0]))
            st.rerun()

    st.divider()
    reason = st.text_input(
        "Reason for revision",
        placeholder="e.g. Tech returned - door still not operating, ADA operator required",
    )
    rev_tech_id = render_technician_picker(draft.service_order_no, key_prefix="rev")

    render_quote_guardrails(draft)

    if st.button("Save Revision", type="primary"):
        new_rev = save_revision(
            qn, draft, created_by=name, reason=reason.strip(),
            carried_meta=carried,
        )
        current_after = get_current_revision(qn)
        if current_after and rev_tech_id:
            apply_technician_selection(current_after["quote_id"], rev_tech_id)
        pdf_path = generate_pdf(qn, output_dir="output")
        st.session_state.revise_draft = None
        st.success(f"Saved as **{qn} Rev {new_rev}**.")
        with open(pdf_path, "rb") as f:
            st.download_button("Download Revised PDF", f,
                               file_name=f"{qn}_Rev{new_rev}.pdf", mime="application/pdf")


def intake_page():
    st.title("📥 Intake Queue")
    st.caption(
        "For CCRs: submit the service order, what the tech found, and the parts "
        "needed. This replaces emailing a scratch sheet -- the quote team picks "
        "it up from the queue below."
    )

    tab_new, tab_queue = st.tabs(["Submit a Request", "Queue"])

    with tab_new:
        # Not wrapped in st.form, because buttons inside a form only fire
        # on submit -- which would make "Add another line" impossible.
        so_no = st.text_input("Service Order Number", placeholder="e.g. 211639 or 500125",
                              key="intake_so")
        issue = st.text_area("What's the issue? (what the customer reported / what broke)",
                             key="intake_issue")
        work = st.text_area("What did the tech do on site?", key="intake_work")

        st.markdown("**Parts needed**")
        st.caption(
            "One part per line. Start typing to search the catalog, or type "
            "anything that isn't in it -- the quote team will see it either way."
        )

        if "intake_rows" not in st.session_state:
            st.session_state.intake_rows = [0, 1, 2]
        if "intake_next_row" not in st.session_state:
            st.session_state.intake_next_row = 3

        try:
            catalog = get_all_parts()
            part_options = [f"{p['part_number']} — {p['description']}" for p in catalog]
        except Exception:
            part_options = []

        for row_id in list(st.session_state.intake_rows):
            pcol, qcol, xcol = st.columns([5, 1.2, 0.8])
            with pcol:
                st.selectbox(
                    "Part", options=part_options, index=None,
                    accept_new_options=True,
                    placeholder="Search the catalog or type a part / description",
                    key=f"intake_part_{row_id}", label_visibility="collapsed",
                )
            with qcol:
                st.number_input(
                    "Qty", min_value=1, value=1, step=1,
                    key=f"intake_qty_{row_id}", label_visibility="collapsed",
                )
            with xcol:
                # Keep at least one row so the form never empties out
                if len(st.session_state.intake_rows) > 1:
                    if st.button("✕", key=f"intake_del_{row_id}", help="Remove this line"):
                        st.session_state.intake_rows.remove(row_id)
                        st.rerun()

        if st.button("+ Add another line"):
            st.session_state.intake_rows.append(st.session_state.intake_next_row)
            st.session_state.intake_next_row += 1
            st.rerun()

        st.write("")
        submitted = st.button("Submit to Quote Team", type="primary")

        if submitted:
            # Collapse the rows back into the same newline format the
            # request is stored in, so the parser and the Create Quote
            # handoff keep working unchanged.
            lines = []
            for row_id in st.session_state.intake_rows:
                part_value = st.session_state.get(f"intake_part_{row_id}")
                qty_value = st.session_state.get(f"intake_qty_{row_id}", 1)
                if not part_value:
                    continue
                # Selections come through as "HW-2201 — Description"; keep
                # just the part number. Free-typed entries pass through as-is.
                part_token = str(part_value).split("—")[0].strip()
                lines.append(f"{part_token} x{int(qty_value)}")
            parts_text = "\n".join(lines)

            if not so_no.strip():
                st.error("Service order number is required.")
            elif not lines:
                st.error("Add at least one part before submitting.")
            else:
                try:
                    intake.create_request(
                        service_order_no=so_no.strip(),
                        issue_description=issue.strip(),
                        work_performed=work.strip(),
                        parts_requested=parts_text,
                        submitted_by=name,
                    )
                    st.success(
                        f"Submitted {len(lines)} part line(s). The quote team can see "
                        f"service order {so_no.strip()} in the queue."
                    )
                    # Reset the rows so the next request starts clean
                    for row_id in st.session_state.intake_rows:
                        st.session_state.pop(f"intake_part_{row_id}", None)
                        st.session_state.pop(f"intake_qty_{row_id}", None)
                    st.session_state.intake_rows = [
                        st.session_state.intake_next_row,
                        st.session_state.intake_next_row + 1,
                        st.session_state.intake_next_row + 2,
                    ]
                    st.session_state.intake_next_row += 3
                except Exception as e:
                    st.error(f"Could not submit: {e}")

    with tab_queue:
        status_filter = st.selectbox("Show", ["pending", "quoted", "closed", "all"])
        requests = intake.list_requests(None if status_filter == "all" else status_filter)
        if not requests:
            st.caption("Nothing in the queue.")
        else:
            for req in requests:
                header = (f"{req['service_order_no']} — {req['status']} — "
                          f"submitted by {req['submitted_by']} on {req['submitted_at']:%b %d, %Y}")
                with st.expander(header):
                    if req.get("account_name"):
                        st.markdown(f"**Account:** {req['account_name']}")
                    if req.get("issue_description"):
                        st.markdown(f"**Issue:** {req['issue_description']}")
                    if req.get("work_performed"):
                        st.markdown(f"**Work performed:** {req['work_performed']}")
                    if req.get("parts_requested"):
                        st.markdown("**Parts requested:**")
                        st.code(req["parts_requested"])
                    if req["status"] == "pending":
                        bcol1, bcol2 = st.columns(2)
                        with bcol1:
                            if st.button("Create Quote", type="primary",
                                         key=f"intake_quote_{req['id']}"):
                                # Hand the service order straight to the quote
                                # builder so the quote team never has to
                                # re-type it on another page.
                                st.session_state.pending_service_order = req["service_order_no"]
                                st.session_state.pending_intake_id = req["id"]
                                st.session_state.pending_parts_text = req.get("parts_requested")
                                st.session_state.requested_page = "Create Quote"
                                st.rerun()
                        with bcol2:
                            if st.button("Mark as Quoted", key=f"intake_done_{req['id']}"):
                                intake.mark_quoted(req["id"])
                                st.rerun()


def main():
    branding = load_branding()

    apply_theme(DEFAULT_THEME)

    if branding.get("logo_path") and Path(branding["logo_path"]).exists():
        st.sidebar.image(branding["logo_path"], width=100)
    st.sidebar.title(branding["company_name"])
    st.sidebar.caption("Replaces the Master Price List + Quote Template workflow.")
    st.sidebar.write(f"Logged in as **{name}**")
    authenticator.logout("Log out", "sidebar")

    # Ordered to follow the real workflow: a job arrives in the intake
    # queue, becomes a quote, may later be revised, and then shows up in
    # the pipeline views.
    nav_options = ["Intake Queue", "Create Quote", "Revise Quote", "Find a Quote", "Pricing History", "Dashboard"]
    is_admin = user_role in ADMIN_ROLES
    if is_admin:
        nav_options.extend(["Reports", "Settings"])

    # Allow another page to request navigation (e.g. Intake's "Create
    # Quote" button). Applied BEFORE the radio is created, since Streamlit
    # won't let a widget's own state be reassigned after it renders.
    requested = st.session_state.pop("requested_page", None)
    if requested in nav_options:
        st.session_state.nav_page = requested

    pending_count = 0
    try:
        pending_count = intake.pending_count()
    except Exception:
        pass
    if pending_count:
        st.sidebar.caption(f"{pending_count} intake request(s) waiting")

    page = st.sidebar.radio("Navigate", nav_options, key="nav_page")

    if page == "Create Quote":
        new_quote_page()
    elif page == "Revise Quote":
        revise_quote_page()
    elif page == "Find a Quote":
        quote_search_page()
    elif page == "Pricing History":
        pricing_history_page()
    elif page == "Intake Queue":
        intake_page()
    elif page == "Dashboard":
        dashboard_page()
    elif page == "Reports" and is_admin:
        reports_page()
    elif page == "Settings" and is_admin:
        settings_page()


if __name__ == "__main__":
    main()
