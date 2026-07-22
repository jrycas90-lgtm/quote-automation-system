"""
hash_password.py

Small helper to generate a bcrypt hash for a plaintext password, for use
in config/auth_config.yaml. Streamlit-authenticator needs hashed
passwords, not plaintext, in that file.

Usage:
    python scripts/hash_password.py "your-password-here"
"""

from __future__ import annotations
import sys
import streamlit_authenticator as stauth


def main():
    if len(sys.argv) != 2:
        print("Usage: python scripts/hash_password.py \"your-password-here\"")
        sys.exit(1)

    plaintext = sys.argv[1]
    hashed = stauth.Hasher.hash(plaintext)
    print(hashed)


if __name__ == "__main__":
    main()
