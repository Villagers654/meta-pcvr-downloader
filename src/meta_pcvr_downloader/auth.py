from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


class AuthenticationError(RuntimeError):
    pass


def _chromium_cookie_candidates(home: Path) -> list[tuple[str, Path]]:
    roots = [
        home / ".config/microsoft-edge",
        home / ".config/microsoft-edge-beta",
        home / ".config/google-chrome",
        home / ".config/chromium",
        home / ".var/app/com.microsoft.Edge/config/microsoft-edge",
        home / ".var/app/com.google.Chrome/config/google-chrome",
        home / ".var/app/org.chromium.Chromium/config/chromium",
    ]
    results: list[tuple[str, Path]] = []
    for root in roots:
        if not root.is_dir():
            continue
        for profile in (root / "Default", *sorted(root.glob("Profile *"))):
            for relative in ("Network/Cookies", "Cookies"):
                candidate = profile / relative
                if candidate.is_file():
                    results.append(("chromium", candidate))
    return results


def _firefox_cookie_candidates(home: Path) -> list[tuple[str, Path]]:
    roots = [
        home / ".mozilla/firefox",
        home / ".var/app/org.mozilla.firefox/.mozilla/firefox",
        home / "snap/firefox/common/.mozilla/firefox",
    ]
    results: list[tuple[str, Path]] = []
    for root in roots:
        if not root.is_dir():
            continue
        for candidate in sorted(root.glob("*/cookies.sqlite")):
            if candidate.is_file():
                results.append(("firefox", candidate))
    return results


def _cookie_candidates(home: Path) -> list[tuple[str, Path]]:
    return _firefox_cookie_candidates(home) + _chromium_cookie_candidates(home)


def _keyring_password() -> bytes:
    if shutil.which("secret-tool") is None:
        raise AuthenticationError(
            "secret-tool is required for automatic browser login. Install "
            "libsecret-tools (Debian/Ubuntu) or libsecret (Fedora/Arch), or set "
            "META_OCULUS_ACCESS_TOKEN."
        )
    attribute_values = ("chromium", "chrome", "microsoft-edge", "Microsoft Edge")
    for value in attribute_values:
        result = subprocess.run(
            ["secret-tool", "lookup", "application", value],
            capture_output=True,
            check=False,
        )
        if result.returncode == 0 and result.stdout.rstrip():
            return result.stdout.rstrip()
    raise AuthenticationError(
        "The browser encryption key was not found in Secret Service. Set "
        "META_OCULUS_ACCESS_TOKEN instead."
    )


def _decrypt_chromium_cookie(host: str, encrypted: bytes, password: bytes) -> str:
    if not encrypted.startswith((b"v10", b"v11")):
        try:
            return encrypted.decode()
        except UnicodeDecodeError as error:
            raise AuthenticationError(
                "Unsupported browser cookie encryption format"
            ) from error
    key = hashlib.pbkdf2_hmac("sha1", password, b"saltysalt", 1, 16)
    ciphertext = encrypted[3:]
    decryptor = Cipher(algorithms.AES(key), modes.CBC(b" " * 16)).decryptor()
    clear = decryptor.update(ciphertext) + decryptor.finalize()
    padding = clear[-1]
    if not 1 <= padding <= 16:
        raise AuthenticationError("Could not decrypt the browser login cookie")
    clear = clear[:-padding]
    domain_digest = hashlib.sha256(host.encode()).digest()
    clear = clear.removeprefix(domain_digest)
    try:
        return clear.decode()
    except UnicodeDecodeError as error:
        raise AuthenticationError(
            "Could not decode the browser login cookie"
        ) from error


def _snapshot_database(database: Path, destination: Path) -> None:
    source = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    target = sqlite3.connect(destination)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()


def _read_chromium_cookie(database: Path) -> tuple[str, str | bytes]:
    with tempfile.TemporaryDirectory(prefix="meta-pcvr-auth-") as temporary:
        copied = Path(temporary) / "Cookies"
        _snapshot_database(database, copied)
        connection = sqlite3.connect(copied)
        try:
            row = connection.execute(
                "select host_key, value, encrypted_value from cookies "
                "where name='oc_ac_at' and "
                "(host_key like '%oculus.com' or host_key like '%meta.com') "
                "order by expires_utc desc limit 1"
            ).fetchone()
        finally:
            connection.close()
    if row is None:
        raise AuthenticationError(f"No Meta login cookie in {database}")
    host, value, encrypted = row
    return host, value or bytes(encrypted)


def _read_firefox_cookie(database: Path) -> str:
    with tempfile.TemporaryDirectory(prefix="meta-pcvr-auth-") as temporary:
        copied = Path(temporary) / "cookies.sqlite"
        _snapshot_database(database, copied)
        connection = sqlite3.connect(copied)
        try:
            row = connection.execute(
                "select value from moz_cookies where name='oc_ac_at' and "
                "(host like '%oculus.com' or host like '%meta.com') "
                "order by expiry desc limit 1"
            ).fetchone()
        finally:
            connection.close()
    if row is None or not row[0]:
        raise AuthenticationError(f"No Meta login cookie in {database}")
    return str(row[0])


def get_access_token(home: Path | None = None) -> str:
    token = os.environ.get("META_OCULUS_ACCESS_TOKEN", "").strip()
    if token:
        return token

    failures: list[str] = []
    password: bytes | None = None
    candidates = _cookie_candidates(home or Path.home())
    candidates.sort(key=lambda item: item[1].stat().st_mtime, reverse=True)
    for browser, database in candidates:
        try:
            if browser == "firefox":
                return _read_firefox_cookie(database)
            host, value = _read_chromium_cookie(database)
            if isinstance(value, str):
                return value
            if password is None:
                password = _keyring_password()
            return _decrypt_chromium_cookie(host, value, password)
        except (AuthenticationError, OSError, sqlite3.Error) as error:
            failures.append(f"{database}: {error}")

    detail = f" Last error: {failures[-1]}" if failures else ""
    raise AuthenticationError(
        "No usable Meta login was found. Sign in at meta.com in Firefox, Edge, "
        "Chrome, or Chromium and retry, or set META_OCULUS_ACCESS_TOKEN." + detail
    )
