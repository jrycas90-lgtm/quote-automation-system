"""
branding.py

Central place for company/brand info used across the app -- PDF quotes,
the Streamlit UI, etc. Edit the defaults below (or override via
environment variables, which take priority) to rebrand the whole system
for a different company without touching any business logic.

Env vars (all optional, fall back to the defaults below):
    QUOTE_COMPANY_NAME
    QUOTE_COMPANY_ADDRESS
    QUOTE_COMPANY_PHONE
    QUOTE_COMPANY_EMAIL
    QUOTE_COMPANY_LOGO   -- path to a logo image file, optional
    QUOTE_BRAND_COLOR    -- hex color used for PDF table headers / accents
"""

from __future__ import annotations
import os

COMPANY_NAME = os.environ.get("QUOTE_COMPANY_NAME", "Sentinel Access & Hardware Solutions")
COMPANY_ADDRESS = os.environ.get("QUOTE_COMPANY_ADDRESS", "4200 Industrial Pkwy, Suite 220, Denver, CO 80216")
COMPANY_PHONE = os.environ.get("QUOTE_COMPANY_PHONE", "(303) 555-0134")
COMPANY_EMAIL = os.environ.get("QUOTE_COMPANY_EMAIL", "quotes@sentinelaccess.example")
COMPANY_LOGO_PATH = os.environ.get("QUOTE_COMPANY_LOGO") or None
BRAND_COLOR = os.environ.get("QUOTE_BRAND_COLOR", "#1F3A5F")
