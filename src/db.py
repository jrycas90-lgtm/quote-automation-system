"""
db.py

Database connection helper. Resolves connection settings with this
priority (highest wins):
  1. Streamlit secrets (st.secrets) -- used when running inside the
     Streamlit app on a deployed environment like Streamlit Community
     Cloud. Streamlit is supposed to auto-expose top-level secrets as
     environment variables too, but checking st.secrets directly here
     removes any dependence on that behavior working correctly.
  2. Environment variables -- used for local dev (see scripts/dev-env.ps1
     / dev-env.sh) and for anything running outside the Streamlit app
     (the FastAPI layer in api/, CLI scripts like erp_sync.py).
  3. Hardcoded local-Docker defaults, so a fresh local setup works with
     zero configuration.

This module is imported both inside the Streamlit app and by plain
Python scripts/the FastAPI API, which don't have a Streamlit runtime
running. Streamlit itself is safe to import and st.secrets is safe to
read in both contexts -- it just reads secrets.toml off disk if present,
whether or not a Streamlit server is actually running.
"""

from __future__ import annotations
import os
import psycopg2
import psycopg2.extras

try:
    import streamlit as st
except ImportError:
    st = None


def _get_setting(key: str, default: str) -> str:
    """Looks up a connection setting: Streamlit secrets first, then
    environment variables, then the given default."""
    if st is not None:
        try:
            if key in st.secrets:
                return st.secrets[key]
        except Exception:
            # No secrets.toml at all (local dev without Streamlit Cloud),
            # or any other issue reading secrets -- fall through to env vars.
            pass
    return os.environ.get(key, default)


def get_connection():
    return psycopg2.connect(
        host=_get_setting("QUOTE_DB_HOST", "localhost"),
        port=_get_setting("QUOTE_DB_PORT", "5432"),
        dbname=_get_setting("QUOTE_DB_NAME", "quote_automation"),
        user=_get_setting("QUOTE_DB_USER", "postgres"),
        password=_get_setting("QUOTE_DB_PASSWORD", "postgres"),
    )


def get_dict_cursor(conn):
    """Returns a cursor that yields rows as dicts instead of tuples --
    much easier to work with in the PDF generator and Streamlit app."""
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
