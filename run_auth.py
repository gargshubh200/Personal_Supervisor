# run_auth.py  — run this once locally to generate the token.json file for Google Drive API access
from google_auth_oauthlib.flow import InstalledAppFlow
from pathlib import Path

DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]  # must match dispatch_mcp.py exactly

flow = InstalledAppFlow.from_client_secrets_file(
    str(Path("OtherMCP/credentials.json")),
    scopes=DRIVE_SCOPES
)
creds = flow.run_local_server(port=0)

token_path = Path("OtherMCP/token.json")
token_path.write_text(creds.to_json())
print(f"✅ token.json written to {token_path}")