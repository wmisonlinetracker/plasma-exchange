"""Password encryption for everything that leaves the scanner (website data and the price history).

AES-256-GCM with a key derived from the dashboard password (PBKDF2-SHA256). The website decrypts the
same format in the browser with WebCrypto, so the password never leaves your device.

  python app/vault.py lock   data/market.db      data/market.db.enc
  python app/vault.py unlock data/market.db.enc  data/market.db
The password is read from the DASHBOARD_PASSWORD environment variable (a GitHub secret).
"""
import base64
import hashlib
import json
import os
import sys

ITERATIONS = 600_000
SALT_LABEL = "plasma-exchange-dashboard-v1"  # fixed salt so "remember me" survives each new scan


def _salt():
    return hashlib.sha256(SALT_LABEL.encode()).digest()[:16]


def _key(password):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    return PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=_salt(), iterations=ITERATIONS).derive(password.encode())


def password_from_env():
    pw = os.environ.get("DASHBOARD_PASSWORD", "")
    if len(pw) < 10:
        sys.exit("DASHBOARD_PASSWORD is missing or shorter than 10 characters. Add it under "
                 "Settings > Secrets and variables > Actions > New repository secret.")
    return pw


def encrypt(data: bytes, password: str) -> dict:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    iv = os.urandom(12)
    ct = AESGCM(_key(password)).encrypt(iv, data, None)
    b64 = lambda b: base64.b64encode(b).decode()
    return {"v": 1, "kdf": "PBKDF2-SHA256", "iter": ITERATIONS, "salt": b64(_salt()), "iv": b64(iv), "ct": b64(ct)}


def decrypt(obj: dict, password: str) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    return AESGCM(_key(password)).decrypt(base64.b64decode(obj["iv"]), base64.b64decode(obj["ct"]), None)


def main():
    if len(sys.argv) != 4 or sys.argv[1] not in ("lock", "unlock"):
        sys.exit(__doc__)
    cmd, src, dst = sys.argv[1:]
    pw = password_from_env()
    if cmd == "lock":
        with open(src, "rb") as f:
            obj = encrypt(f.read(), pw)
        with open(dst, "w") as f:
            json.dump(obj, f)
    else:
        with open(src) as f:
            obj = json.load(f)
        try:
            data = decrypt(obj, pw)
        except Exception:
            sys.exit("Could not unlock the price history: the DASHBOARD_PASSWORD secret doesn't match the one "
                     "it was saved with. Put the old password back, or delete data/market.db.enc to start fresh.")
        with open(dst, "wb") as f:
            f.write(data)
    print(f"{cmd}ed {src} -> {dst}")


if __name__ == "__main__":
    main()
