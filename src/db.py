"""
db.py

Database connection helper. Reads connection settings from environment
variables (with sane local defaults) so credentials never need to be
hardcoded or committed.
"""

from __future__ import annotations
import os
import psycopg2
import psycopg2.extras


def get_connection():
    return psycopg2.connect(
        host=os.environ.get("QUOTE_DB_HOST", "localhost"),
        port=os.environ.get("QUOTE_DB_PORT", "5432"),
        dbname=os.environ.get("QUOTE_DB_NAME", "quote_automation"),
        user=os.environ.get("QUOTE_DB_USER", "postgres"),
        password=os.environ.get("QUOTE_DB_PASSWORD", "postgres"),
    )


def get_dict_cursor(conn):
    """Returns a cursor that yields rows as dicts instead of tuples --
    much easier to work with in the PDF generator and Streamlit app."""
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
