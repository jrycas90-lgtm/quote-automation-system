"""
branding.py

Central place for company/brand info used across the app -- PDF quotes,
the Streamlit UI, etc.

Branding is resolved with this priority (highest wins):
  1. A runtime override file (config/branding_override.json), written by
     the Settings page in the Streamlit app. This lets someone rebrand
     the whole system live, without touching code or redeploying.
  2. Environment variables (useful for deployment-time defaults).
  3. The built-in defaults below.

The override file is gitignored -- it's runtime state, not source code,
and may contain a real company's actual name/address/logo.

Env vars (all optional, fall back to the defaults below):
    QUOTE_COMPANY_NAME
    QUOTE_COMPANY_ADDRESS
    QUOTE_COMPANY_PHONE
    QUOTE_COMPANY_EMAIL
    QUOTE_COMPANY_LOGO   -- path to a logo image file, optional
    QUOTE_BRAND_COLOR    -- hex color used for PDF table headers / accents
"""

from __future__ import annotations
import json
import os
from pathlib import Path

_CONFIG_DIR = Path(__file__).resolve().parent
_OVERRIDE_PATH = _CONFIG_DIR / "branding_override.json"
_DEFAULT_LOGO_PATH = _CONFIG_DIR.parent / "assets" / "logo.png"

_DEFAULTS = {
    "company_name": "Jerry's Hardware Solutions",
    "company_address": "4200 Industrial Pkwy, Suite 220, Denver, CO 80216",
    "company_phone": "(303) 555-0134",
    "company_email": "quotes@jerryshardware.example",
    "brand_color": "#1F3A5F",
}

_ENV_MAP = {
    "company_name": "QUOTE_COMPANY_NAME",
    "company_address": "QUOTE_COMPANY_ADDRESS",
    "company_phone": "QUOTE_COMPANY_PHONE",
    "company_email": "QUOTE_COMPANY_EMAIL",
    "brand_color": "QUOTE_BRAND_COLOR",
}


def load_branding() -> dict:
    """Returns the current branding config as a dict, freshly resolved on
    every call (not cached at import time). This matters because Streamlit
    reruns the whole script on every interaction, and someone may have just
    changed branding via the Settings page -- re-reading here means those
    changes show up immediately, without restarting the app."""
    values = dict(_DEFAULTS)
    values["logo_path"] = str(_DEFAULT_LOGO_PATH) if _DEFAULT_LOGO_PATH.exists() else None

    for key, env_name in _ENV_MAP.items():
        if os.environ.get(env_name):
            values[key] = os.environ[env_name]
    if os.environ.get("QUOTE_COMPANY_LOGO"):
        values["logo_path"] = os.environ["QUOTE_COMPANY_LOGO"]

    if _OVERRIDE_PATH.exists():
        try:
            with open(_OVERRIDE_PATH) as f:
                override = json.load(f)
            values.update({k: v for k, v in override.items() if v})
        except (json.JSONDecodeError, OSError):
            pass

    return values


def save_branding_override(**kwargs) -> None:
    """Persists branding fields (company_name, company_address,
    company_phone, company_email, brand_color, logo_path) to a small JSON
    file so they survive across reruns of the Streamlit app. Called by the
    Settings page. Only non-None kwargs are written/updated."""
    current = {}
    if _OVERRIDE_PATH.exists():
        try:
            with open(_OVERRIDE_PATH) as f:
                current = json.load(f)
        except (json.JSONDecodeError, OSError):
            current = {}
    current.update({k: v for k, v in kwargs.items() if v is not None})
    _OVERRIDE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_OVERRIDE_PATH, "w") as f:
        json.dump(current, f, indent=2)


def save_logo(image_bytes: bytes, suffix: str = ".png") -> str:
    """Saves an uploaded logo image to assets/logo<suffix> and returns the
    path. Overwrites any previously saved logo."""
    assets_dir = _CONFIG_DIR.parent / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    logo_path = assets_dir / f"logo{suffix}"
    with open(logo_path, "wb") as f:
        f.write(image_bytes)
    return str(logo_path)


# Backwards-compatible module-level constants, evaluated once at import
# time. Existing code (like pdf_generator.py) calls load_branding() fresh
# each time instead, so Settings-page changes take effect without a
# restart -- these are kept only for any external code that might import
# the constants directly.
_initial = load_branding()
COMPANY_NAME = _initial["company_name"]
COMPANY_ADDRESS = _initial["company_address"]
COMPANY_PHONE = _initial["company_phone"]
COMPANY_EMAIL = _initial["company_email"]
COMPANY_LOGO_PATH = _initial["logo_path"]
BRAND_COLOR = _initial["brand_color"]
