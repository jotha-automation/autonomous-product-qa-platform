#!/usr/bin/env python3
"""Register a Google Drive push notification channel on the APTP system root folder.

Usage:
  export GOOGLE_DRIVE_CREDENTIALS_JSON='...'
  export GOOGLE_DRIVE_SYSTEM_ROOT_FOLDER_ID='...'
  export GOOGLE_DRIVE_WEBHOOK_SECRET='...'
  export APTP_WEBHOOK_URL='https://<API>/api/v1/webhooks/google-drive'
  python scripts/register_drive_watch.py

Save the printed channel id + resourceId; renew before expiration (~7 days max).
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid

from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = ("https://www.googleapis.com/auth/drive",)


def main() -> int:
    creds_json = (os.getenv("GOOGLE_DRIVE_CREDENTIALS_JSON") or "").strip()
    folder_id = (os.getenv("GOOGLE_DRIVE_SYSTEM_ROOT_FOLDER_ID") or "").strip()
    webhook_url = (os.getenv("APTP_WEBHOOK_URL") or "").strip()
    webhook_secret = (os.getenv("GOOGLE_DRIVE_WEBHOOK_SECRET") or "").strip()

    missing = [
        name
        for name, val in [
            ("GOOGLE_DRIVE_CREDENTIALS_JSON", creds_json),
            ("GOOGLE_DRIVE_SYSTEM_ROOT_FOLDER_ID", folder_id),
            ("APTP_WEBHOOK_URL", webhook_url),
            ("GOOGLE_DRIVE_WEBHOOK_SECRET", webhook_secret),
        ]
        if not val
    ]
    if missing:
        print("Missing env vars:", ", ".join(missing), file=sys.stderr)
        return 1

    info = json.loads(creds_json)
    creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    service = build("drive", "v3", credentials=creds, cache_discovery=False)

    channel_id = str(uuid.uuid4())
    expiration_ms = int((time.time() + 7 * 24 * 3600 - 60) * 1000)

    body = {
        "id": channel_id,
        "type": "web_hook",
        "address": webhook_url,
        "token": webhook_secret,
        "expiration": expiration_ms,
    }

    result = service.files().watch(
        fileId=folder_id,
        body=body,
        supportsAllDrives=True,
    ).execute()

    print(json.dumps(result, indent=2))
    print("\nSave channel id + resourceId. Renew with this script before expiration.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
