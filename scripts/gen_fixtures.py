#!/usr/bin/env python3
"""Independent synthetic fixture generator.

The CoAP implementation under test never generates these expected answers:
this script owns the byte recipe and computes length/sha256 itself.

Recipe (documented for reproducibility):
    byte(i) = ((i * 73) & 0xFF) ^ ((i // 97) & 0xFF)
"""
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FIX_DIR = os.path.normpath(os.path.join(HERE, "..", "test", "testdata", "fixtures"))

SIZES = {
    "payload_64.bin": 64,
    "payload_300.bin": 300,
    "payload_2500.bin": 2500,
    "payload_5000.bin": 5000,
    "payload_alt_900.bin": 900,  # distinct representation for ETag-change tests
}


def render(n: int) -> bytes:
    return bytes(((i * 73) & 0xFF) ^ ((i // 97) & 0xFF) for i in range(n))


def main() -> int:
    os.makedirs(FIX_DIR, exist_ok=True)
    manifest = {
        "recipe": "byte(i) = ((i*73)&0xff) ^ ((i//97)&0xff)",
        "files": {},
    }
    for name, size in SIZES.items():
        data = render(size)
        path = os.path.join(FIX_DIR, name)
        with open(path, "wb") as f:
            f.write(data)
        digest = hashlib.sha256(data).hexdigest()
        manifest["files"][name] = {"length": size, "sha256": digest}
        print(f"wrote {name}: {size} bytes sha256={digest[:16]}...")
    with open(os.path.join(FIX_DIR, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
        f.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
