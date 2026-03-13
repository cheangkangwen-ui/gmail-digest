"""
One-time local setup script to obtain a Gmail OAuth2 refresh token.

Usage:
    1. Download credentials.json from GCP Console (OAuth 2.0 Desktop credential)
    2. Place credentials.json in this directory
    3. Run: python setup_auth.py
    4. A browser window will open — sign in and authorise
    5. Copy the printed GMAIL_REFRESH_TOKEN and add it as a GitHub secret
"""

import json
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

def main():
    flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
    creds = flow.run_local_server(port=0)

    print("\n" + "=" * 60)
    print("Add the following as GitHub repository secrets:")
    print("=" * 60)
    print(f"GMAIL_REFRESH_TOKEN = {creds.refresh_token}")
    print(f"GMAIL_CLIENT_ID     = {creds.client_id}")
    print(f"GMAIL_CLIENT_SECRET = {creds.client_secret}")
    print("=" * 60)

    # Also save locally for reference
    data = {
        "refresh_token": creds.refresh_token,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
    }
    with open("token_info.json", "w") as f:
        json.dump(data, f, indent=2)
    print("\nAlso saved to token_info.json (do NOT commit this file)")

if __name__ == "__main__":
    main()
