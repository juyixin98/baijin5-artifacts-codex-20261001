#!/usr/bin/env python3
"""Independent reference implementation of the gear-cdc v1 chunker.

This script exists so the golden files under tests/fixtures/expected/ are
NOT solely produced by the Rust core they test. It re-derives everything
from the published spec:

  - GEAR[i] = i-th SplitMix64 output from state 0x9E3779B97F4A7C15
  - roll: h = ((h << 1) + GEAR[b]) mod 2^64, reset at every chunk start
  - boundary when chunk_len >= max_size (forced), or
    chunk_len >= min_size and h & ((1 << avg_bits) - 1) == 0
  - trailing partial chunk is emitted at end of input; empty input -> no chunks

Usage:
  python3 scripts/reference_chunker.py <input.bin> <min_size> <avg_bits> <max_size>
    prints JSON with boundaries/chunk digests to stdout
  python3 scripts/reference_chunker.py --check
    recomputes all fixtures and diffs them against tests/fixtures/expected/
"""

import hashlib
import json
import sys

MASK64 = (1 << 64) - 1
TABLE_SEED = 0x9E3779B97F4A7C15
GOLDEN_GAMMA = 0x9E3779B97F4A7C15
WINDOW = 64


def build_gear_table():
    state = TABLE_SEED
    table = []
    for _ in range(256):
        state = (state + GOLDEN_GAMMA) & MASK64
        z = state
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & MASK64
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & MASK64
        z ^= z >> 31
        table.append(z)
    return table


GEAR = build_gear_table()


def chunk(data: bytes, min_size: int, avg_bits: int, max_size: int) -> dict:
    mask = (1 << avg_bits) - 1
    boundaries = []  # chunk start offsets
    digests = []
    lengths = []
    start = 0
    h = 0
    for i, b in enumerate(data):
        h = ((h << 1) + GEAR[b]) & MASK64
        length = i + 1 - start
        if length >= max_size or (length >= min_size and (h & mask) == 0):
            boundaries.append(start)
            chunk_bytes = data[start : i + 1]
            digests.append(hashlib.sha256(chunk_bytes).hexdigest())
            lengths.append(length)
            start = i + 1
            h = 0
    if start < len(data):
        boundaries.append(start)
        chunk_bytes = data[start:]
        digests.append(hashlib.sha256(chunk_bytes).hexdigest())
        lengths.append(len(chunk_bytes))
    return {
        "params": {"min_size": min_size, "avg_bits": avg_bits, "max_size": max_size},
        "total_len": len(data),
        "content_sha256": hashlib.sha256(data).hexdigest(),
        "boundaries": boundaries,
        "chunk_lengths": lengths,
        "chunk_sha256": digests,
    }


def main() -> int:
    if len(sys.argv) == 2 and sys.argv[1] == "--check":
        return check_fixtures()
    if len(sys.argv) != 5:
        print(__doc__, file=sys.stderr)
        return 2
    path, min_size, avg_bits, max_size = sys.argv[1], *map(int, sys.argv[2:5])
    with open(path, "rb") as f:
        data = f.read()
    print(json.dumps(chunk(data, min_size, avg_bits, max_size), indent=2))
    return 0


def check_fixtures() -> int:
    import pathlib

    expected_dir = pathlib.Path("tests/fixtures/expected")
    failures = 0
    for golden_path in sorted(expected_dir.glob("*.json")):
        name = golden_path.stem
        golden = json.loads(golden_path.read_text())
        data = pathlib.Path(f"tests/fixtures/{name}.bin").read_bytes()
        p = golden["params"]
        actual = chunk(data, p["min_size"], p["avg_bits"], p["max_size"])
        if actual == golden:
            print(f"{name}: OK ({golden['total_len']} bytes, {len(golden['boundaries'])} chunks)")
        else:
            failures += 1
            print(f"{name}: MISMATCH", file=sys.stderr)
            for key in golden:
                if actual[key] != golden[key]:
                    print(f"  field {key} differs", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
