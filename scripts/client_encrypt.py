#!/usr/bin/env python3
"""Client-side encryption helper for the demo flow.

Usage: client_encrypt.py <public_key_n> <value> [<value>...]
Prints one JSON object per line: {"value": v, "ciphertext": "..."}

Range checking happens here (client side), exactly as a real participant
would do it — the server never sees the plaintext.
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.crypto_adapter import encrypt_encoded, reconstruct_public_key
from app.encoding import EncodingParams, encode_signed


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    n = int(sys.argv[1])
    values = [int(v) for v in sys.argv[2:]]
    params = EncodingParams(
        max_plaintext_abs=int(os.environ.get("PAILLIER_MAX_PLAINTEXT_ABS", "1000000")),
        max_coefficient_abs=int(
            os.environ.get("PAILLIER_MAX_COEFFICIENT_ABS", "1000")
        ),
        max_aggregate_abs=int(
            os.environ.get("PAILLIER_MAX_AGGREGATE_ABS", "1000000000")
        ),
    )
    public_key = reconstruct_public_key(n)
    for value in values:
        encoded = encode_signed(value, n, params)
        ciphertext = encrypt_encoded(public_key, encoded)
        print(json.dumps({"value": value, "ciphertext": str(ciphertext)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
