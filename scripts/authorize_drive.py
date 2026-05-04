#!/usr/bin/env python3
"""One-time browser-based OAuth dance to produce token.json.

Run this on a machine with a browser (your laptop) — NOT on the headless robot:

    python scripts/authorize_drive.py /path/to/credentials.json [/path/to/token.json]

Then SCP the resulting token.json to the robot's ~/.sn2_backup/.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("credentials", type=Path, help="OAuth client secret JSON from Google Cloud Console")
    p.add_argument("token", type=Path, nargs="?", default=Path("token.json"),
                   help="output token path (default: ./token.json)")
    args = p.parse_args(argv)

    if not args.credentials.exists():
        print(f"credentials file not found: {args.credentials}", file=sys.stderr)
        return 1

    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(str(args.credentials), SCOPES)
    creds = flow.run_local_server(port=0)
    args.token.write_text(creds.to_json())
    print(f"wrote {args.token}")
    print("Now copy this file to the robot at ~/.sn2_backup/token.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
