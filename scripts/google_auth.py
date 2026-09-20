"""One-time Google OAuth: produce GOOGLE_REFRESH_TOKEN (SETUP.md §4.4).

Run:  python scripts/google_auth.py
Requires GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET in .env (Desktop-app client).
Browser opens -> sign in as calendar owner -> Advanced -> Continue (own app) ->
allow. Prints refresh token -> save as GOOGLE_REFRESH_TOKEN in .env.
Scopes cover Calendar (Phase 5) + Gmail send (Phase 6, lead mail).
"""
import sys
sys.path.insert(0, "config")
from settings import settings

SCOPES = ["https://www.googleapis.com/auth/calendar",
          "https://www.googleapis.com/auth/gmail.send"]


def main() -> None:
    if not settings.google_client_id or not settings.google_client_secret:
        print("Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in .env first (SETUP.md §4.3).")
        raise SystemExit(1)
    from google_auth_oauthlib.flow import InstalledAppFlow
    flow = InstalledAppFlow.from_client_config(
        {"installed": {"client_id": settings.google_client_id,
                       "client_secret": settings.google_client_secret,
                       "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                       "token_uri": "https://oauth2.googleapis.com/token"}},
        SCOPES)
    # offline access + forced consent: otherwise Google may not return a refresh token
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    print("OK. Save this in .env as GOOGLE_REFRESH_TOKEN (never commit it):")
    print(creds.refresh_token)


if __name__ == "__main__":
    main()
