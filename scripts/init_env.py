"""Create private local config without overwriting user-supplied secrets."""

import os
import secrets
from pathlib import Path

from cryptography.fernet import Fernet

root = Path(__file__).resolve().parent.parent
target = root / ".env"
if target.exists():
    raise SystemExit(".env already exists; refusing to overwrite.")
text = (root / ".env.example").read_text()
values = {
    "DJANGO_SECRET_KEY": secrets.token_urlsafe(48),
    "DATA_ENCRYPTION_KEY": Fernet.generate_key().decode(),
    "LOCAL_ADMIN_PASSWORD": secrets.token_urlsafe(18),
    "LOCAL_DEMO_PASSWORD": secrets.token_urlsafe(18),
    "LOCAL_DB_PASSWORD": secrets.token_urlsafe(24),
}
lines = [
    key + "=" + values[key] if (key := line.split("=", 1)[0]) in values else line
    for line in text.splitlines()
]
fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
with os.fdopen(fd, "w") as file:
    file.write("\n".join(lines) + "\n")
print("Created private .env (0600). Secrets and local account credentials are in the file.")
