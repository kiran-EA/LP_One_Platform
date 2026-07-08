"""
Run this ONCE to authorize Gmail for the "Send to LP" feature.
It opens a browser for Google sign-in, then saves the token.

Steps before running:
  1. Go to https://console.cloud.google.com
  2. Create/select a project
  3. APIs & Services -> Enable Gmail API
  4. APIs & Services -> Credentials -> Create OAuth 2.0 Client ID
     - Application type: Desktop app  (NOT Web application for this script)
     - Download the JSON -> save as  credentials.json  in this folder
  5. Run:  python authorize_gmail.py
  6. Browser opens -> sign in with your Google account -> click Allow
  7. Token is saved to /tmp/gmail_token.json  (Flask app reads from there)

After this, the "Send to LP" button will work without re-authorizing.
"""

import os
import json

SCOPES = ['https://www.googleapis.com/auth/gmail.send']
CREDS_FILE  = 'credentials.json'
TOKEN_LOCAL = 'gmail_token.json'       # local copy for reference
TOKEN_TMP   = '/tmp/gmail_token.json'  # where Flask reads from

if not os.path.exists(CREDS_FILE):
    print(f"ERROR: {CREDS_FILE} not found.")
    print("Download it from Google Cloud Console -> Credentials -> OAuth 2.0 Client IDs")
    raise SystemExit(1)

from google_auth_oauthlib.flow import InstalledAppFlow

print("Opening browser for Google sign-in...")
flow = InstalledAppFlow.from_client_secrets_file(CREDS_FILE, SCOPES)
creds = flow.run_local_server(port=0)

token_str = creds.to_json()

os.makedirs('/tmp', exist_ok=True)
with open(TOKEN_TMP, 'w') as f:
    f.write(token_str)
with open(TOKEN_LOCAL, 'w') as f:
    f.write(token_str)

print()
print("✅ Gmail authorized successfully!")
print(f"   Token saved to: {TOKEN_TMP}")
print(f"   Local copy at:  {TOKEN_LOCAL}")
print()
print("The 'Send to LP' button will now work.")
print("For Vercel: set GMAIL_TOKEN_JSON env var to the contents of gmail_token.json")
print()
# Print the token so user can copy it as an env var if needed
print("--- GMAIL_TOKEN_JSON value (for Vercel env var) ---")
print(token_str)
