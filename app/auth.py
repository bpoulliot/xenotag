from __future__ import annotations

import base64
import hashlib
import hmac as _hmac
import logging
import os
import secrets
import time
from pathlib import Path

import bcrypt as _bcrypt

log = logging.getLogger(__name__)

_TOKEN_EXPIRY_SECS = 30 * 86400  # 30 days


def _session_key(secret: str, password_hash: str) -> str:
    # Fold the first 16 chars of the bcrypt hash into the signing key so that
    # changing the password automatically invalidates all existing sessions.
    return f"{secret}:{password_hash[:16]}" if password_hash else secret


# ---------------------------------------------------------------------------
# Password hashing — bcrypt only
# ---------------------------------------------------------------------------
# bcrypt is a hard dependency: a missing bcrypt fails at import rather than
# downgrading to a weaker hash (B15).

# bcrypt only ever uses the first 72 bytes of a password. bcrypt 4.x
# truncated longer input silently; 5.x raises ValueError instead, which
# verify_password() below would swallow as "wrong password" -- locking out
# anyone whose >72-byte password was hashed under 4.x, with nothing logged.
# Truncating the bytes here reproduces 4.x exactly, so every existing hash
# stays valid under either version.
_BCRYPT_MAX_BYTES = 72


def _bcrypt_input(pw: str) -> bytes:
    return pw.encode()[:_BCRYPT_MAX_BYTES]


def hash_password(pw: str) -> str:
    return _bcrypt.hashpw(_bcrypt_input(pw), _bcrypt.gensalt(rounds=12)).decode()


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return _bcrypt.checkpw(_bcrypt_input(pw), hashed.encode())
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Stateless HMAC session tokens — survive container restarts
# ---------------------------------------------------------------------------


def _sign(payload: str, secret: str) -> str:
    return _hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def create_session(username: str, secret: str, password_hash: str = "") -> str:
    """Return a signed, time-limited session token."""
    key = _session_key(secret, password_hash)
    expires = int(time.time()) + _TOKEN_EXPIRY_SECS
    payload = f"{username}:{expires}"
    sig = _sign(payload, key)
    return base64.urlsafe_b64encode(f"{payload}:{sig}".encode()).decode()


def get_session_user(token: str, secret: str, password_hash: str = "") -> str | None:
    """Verify token signature and expiry; return username or None."""
    key = _session_key(secret, password_hash)
    try:
        decoded = base64.urlsafe_b64decode(token.encode()).decode()
        # Format: <username>:<expires>:<sig> — split from right to handle colons in usernames
        last = decoded.rfind(":")
        if last < 0:
            return None
        sig = decoded[last + 1 :]
        rest = decoded[:last]
        sep = rest.rfind(":")
        if sep < 0:
            return None
        username = rest[:sep]
        expires_str = rest[sep + 1 :]
        if time.time() > int(expires_str):
            return None
        expected = _sign(f"{username}:{expires_str}", key)
        if not secrets.compare_digest(sig, expected):
            return None
        return username
    except Exception:
        return None


def delete_session(token: str) -> None:
    """No-op — stateless tokens are invalidated by deleting the cookie."""


# ---------------------------------------------------------------------------
# First-run bootstrap
# ---------------------------------------------------------------------------


# B16: a generated first-run password goes to a 0600 file beside config.yml
# (config.initial_password_path()), never to the log -- the log is readable by
# anyone with `docker logs`.


def _write_initial_password(path: Path, password: str) -> None:
    """Create `path` 0600 holding `password`; FileExistsError if it is there.

    O_EXCL creates the file with its final mode in one step -- there is no
    window in which it exists with the umask's mode -- and refuses to follow
    or replace anything already at the path.
    """
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(password + "\n")
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _read_initial_password(path: Path) -> str:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd) as f:
        return f.read().strip()


def remove_initial_password(path: Path | None) -> None:
    """Delete the first-run password file, if any (called on a password change)."""
    if path is None:
        return
    try:
        path.unlink()
    except FileNotFoundError:
        return
    log.info("Password changed; removed %s", path)


def bootstrap(cfg_auth, save_fn, password_file: Path | None = None) -> None:
    """
    Ensure a signing secret and admin account exist.
    Priority: existing values in config > XENOTAG_USERNAME/PASSWORD env vars > auto-generated.

    An auto-generated password is written to `password_file` (0600) and only
    the file's path is logged. If that file already exists it is never
    overwritten: a first run whose hash was not saved adopts the password in it.
    """
    changed = False

    if not cfg_auth.secret_key:
        cfg_auth.secret_key = secrets.token_hex(32)
        changed = True

    if cfg_auth.password_hash.startswith("sha256:"):
        # Written by the removed sha256 fallback (B15); bcrypt cannot verify it,
        # so nobody can log in until the hash is reset.
        log.error(
            "auth.password_hash is an unsalted sha256 hash from an install without bcrypt; "
            "it can no longer be verified. To recover, blank auth.password_hash in "
            "config.yml and restart: the first-run setup creates a new admin password."
        )

    if cfg_auth.password_hash and password_file is not None and password_file.exists():
        log.warning(
            "The first-run admin password is still in %s; it is removed when the password "
            "is changed in Settings.",
            password_file,
        )

    if not cfg_auth.password_hash:
        username = os.environ.get("XENOTAG_USERNAME", "") or cfg_auth.username or "admin"
        password = os.environ.get("XENOTAG_PASSWORD", "")

        if not password:
            password = _first_run_password(password_file)
            if not password:
                if changed:
                    save_fn(cfg_auth)
                return
            log.warning("=" * 60)
            log.warning("XENOTAG FIRST RUN — username: %s", username)
            log.warning("  The generated admin password is in %s (readable only by its owner).", password_file)
            log.warning("  That file is removed on the first password change in Settings.")
            log.warning("  Or set XENOTAG_USERNAME / XENOTAG_PASSWORD env vars before the first start.")
            log.warning("=" * 60)

        cfg_auth.username = username
        cfg_auth.password_hash = hash_password(password)
        changed = True

    if changed:
        save_fn(cfg_auth)


def _first_run_password(path: Path | None) -> str:
    """Generate a password into `path`, or adopt the one already there.

    Returns "" (and logs why) when no password can be stored without logging
    it; the admin hash then stays empty and nobody can log in until it is fixed.
    """
    if path is None:
        log.error("No config directory to hold a generated admin password; set XENOTAG_PASSWORD and restart.")
        return ""
    password = secrets.token_urlsafe(12)
    try:
        _write_initial_password(path, password)
        return password
    except FileExistsError:
        pass
    except OSError as exc:
        log.error(
            "Could not create %s (%s); set XENOTAG_PASSWORD and restart.", path, exc.strerror or type(exc).__name__
        )
        return ""
    # Already there: a first run whose hash never reached config.yml, or a hash
    # blanked by hand. Never overwrite it -- use what it holds.
    try:
        existing = _read_initial_password(path)
    except OSError as exc:
        existing = ""
        log.error("Could not read %s (%s).", path, exc.strerror or type(exc).__name__)
    if not existing:
        log.error("%s exists but holds no password; delete it and restart to generate one.", path)
        return ""
    log.warning("%s already exists; not overwritten — the admin password is the one it holds.", path)
    return existing
