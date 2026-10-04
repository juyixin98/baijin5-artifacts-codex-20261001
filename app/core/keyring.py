"""Local key material management.

Keys live outside the database and never appear in logs or audit rows.  A key
file is JSON with mode 0600::

    {"key_id": "k1-...", "aead_key": "<b64 32B>", "nonce_key": "<b64 32B>"}

Keys may instead be supplied through the environment (base64), which takes
precedence.  A fresh file can be generated deterministically for tests via
:func:`generate_key_bundle` (the CLI ``python -m app.core.keyring init`` uses
OS randomness).
"""

from __future__ import annotations

import base64
import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from .errors import KeyMaterialError

KEY_LEN = 32
ENV_AEAD = "SSEA_AEAD_KEY_B64"
ENV_NONCE = "SSEA_NONCE_KEY_B64"
ENV_KEY_ID = "SSEA_KEY_ID"


@dataclass(frozen=True)
class KeyBundle:
    key_id: str
    aead_key: bytes
    nonce_key: bytes


def _b64_key(raw: str, what: str) -> bytes:
    try:
        key = base64.b64decode(raw, validate=True)
    except Exception as exc:  # noqa: BLE001 - report as key error uniformly
        raise KeyMaterialError(f"{what} is not valid base64") from exc
    if len(key) != KEY_LEN:
        raise KeyMaterialError(f"{what} must decode to {KEY_LEN} bytes",
                               got=len(key))
    return key


def generate_key_bundle(key_id: str | None = None) -> KeyBundle:
    """Generate a fresh bundle using the OS CSPRNG."""
    return KeyBundle(
        key_id=key_id or ("k1-" + secrets.token_hex(8)),
        aead_key=secrets.token_bytes(KEY_LEN),
        nonce_key=secrets.token_bytes(KEY_LEN),
    )


def save_key_file(path: str | os.PathLike[str],
                  bundle: KeyBundle) -> None:
    """Persist a key file with owner-only permissions (0600)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({
        "key_id": bundle.key_id,
        "aead_key": base64.b64encode(bundle.aead_key).decode(),
        "nonce_key": base64.b64encode(bundle.nonce_key).decode(),
    }, indent=2)
    p.write_text(payload)
    os.chmod(p, 0o600)


def load_keys(path: str | os.PathLike[str] | None = None,
              env: dict[str, str] | None = None) -> KeyBundle:
    """Load keys from env (priority) or a 0600 JSON key file.

    Raises KeyMaterialError if the file is group/world readable, malformed or
    missing without env overrides.
    """
    env = os.environ if env is None else env
    if env.get(ENV_AEAD) and env.get(ENV_NONCE):
        return KeyBundle(
            key_id=env.get(ENV_KEY_ID, "env-keys"),
            aead_key=_b64_key(env[ENV_AEAD], ENV_AEAD),
            nonce_key=_b64_key(env[ENV_NONCE], ENV_NONCE),
        )

    if path is None:
        raise KeyMaterialError("no key file configured and no key env vars set")
    p = Path(path)
    if not p.exists():
        raise KeyMaterialError("key file not found", path=str(p))
    mode = p.stat().st_mode & 0o777
    if mode & 0o077:
        raise KeyMaterialError(
            "refusing to use key file accessible to group/others",
            path=str(p), mode_oct=format(mode, "03o"))
    try:
        data = json.loads(p.read_text())
        return KeyBundle(
            key_id=str(data["key_id"]),
            aead_key=_b64_key(data["aead_key"], "aead_key"),
            nonce_key=_b64_key(data["nonce_key"], "nonce_key"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise KeyMaterialError("malformed key file", path=str(p)) from exc


def _main() -> None:  # pragma: no cover - CLI helper
    import sys
    if len(sys.argv) == 3 and sys.argv[1] == "init":
        save_key_file(sys.argv[2], generate_key_bundle())
        print(f"wrote new key file (0600): {sys.argv[2]}")
    else:
        print("usage: python -m app.core.keyring init <path>")
        raise SystemExit(2)


if __name__ == "__main__":  # pragma: no cover
    _main()
