#!/usr/bin/env python3
"""Independent fixture generator for the RBP1 hybrid RLE/bit-pack format.

This is a *separate* implementation of the format spec (see src/format.rs),
written from the spec — not ported from the Rust encoder. The golden vectors
it emits are the reference answers for the Rust interop tests, so the Rust
core never grades its own homework.

Usage: python3 tools/gen_fixtures.py
Writes: fixtures/manifest.json  (cases with expected hex + value runs)
"""

import json
import os

MAGIC = b"RBP1"
GROUP = 8
RLE_MIN_RUN = 8
MODE_RLE = 0
MODE_BITPACK = 1


def bit_width_of(v: int) -> int:
    return v.bit_length()


def pack(values, bw):
    """Pack values (len multiple of 8) LSB-first at bw bits each."""
    out = bytearray(len(values) // GROUP * bw)
    bit_pos = 0
    for v in values:
        assert 0 <= v < (1 << bw) if bw else v == 0
        for b in range(bw):
            if (v >> b) & 1:
                abs_bit = bit_pos + b
                out[abs_bit // 8] |= 1 << (abs_bit % 8)
        bit_pos += bw
    return bytes(out)


def header(mode, bw, count):
    assert 0 <= bw <= 64
    return (
        bytes([mode, bw, 0, 0])
        + count.to_bytes(4, "little")
    )


def encode(values):
    """Deterministic encoder: maximal-run segmentation, then mode selection."""
    assert values, "empty column not encodable"
    out = bytearray(MAGIC)
    lit = []

    def flush_literals():
        if not lit:
            return
        bw = bit_width_of(max(lit))
        out.extend(header(MODE_BITPACK, bw, len(lit)))
        padded = list(lit) + [0] * ((-len(lit)) % GROUP)
        out.extend(pack(padded, bw))
        lit.clear()

    i = 0
    n = len(values)
    while i < n:
        j = i + 1
        while j < n and values[j] == values[i]:
            j += 1
        run_len = j - i
        if run_len >= RLE_MIN_RUN:
            flush_literals()
            bw = bit_width_of(values[i])
            out.extend(header(MODE_RLE, bw, run_len))
            out.extend(values[i].to_bytes((bw + 7) // 8, "little"))
        else:
            lit.extend(values[i:j])
        i = j
    flush_literals()
    return bytes(out)


def runs_of(values):
    """Compact a value list into [[value, count], ...] runs for the manifest."""
    runs = []
    for v in values:
        if runs and runs[-1][0] == v:
            runs[-1][1] += 1
        else:
            runs.append([v, 1])
    return runs


U64_MAX = (1 << 64) - 1

CASES = []


def add_case(name, description, values):
    CASES.append(
        {
            "name": name,
            "description": description,
            "value_count": len(values),
            "runs": runs_of(values),
            "hex": encode(values).hex(),
        }
    )


def build_cases():
    # 1. Alternating long repeats and short variations (mode switching).
    values = []
    values += [7] * 64                # RLE, bw 3
    values += [1, 2, 3]               # literals
    values += [42] * 100              # RLE, bw 6
    values += [9, 9]                  # short run -> literals
    values += [0] * 8                 # RLE, zero bit width
    values += [5, 6, 7]               # literals, tail group of 3
    add_case(
        "alternating-runs",
        "long repeats alternate with short variations; tail group 3 of 8",
        values,
    )

    # 2. Bit-width crossing inside one literal buffer, then max-width RLE.
    values = [
        0, 1, 255, 256, 65535, 65536,
        (1 << 32) - 1, 1 << 32, (1 << 63) - 1, 1 << 63,
    ]
    values += [U64_MAX] * 8           # RLE at max bit width 64
    add_case(
        "bitwidth-crossing",
        "literals cross 2^8/2^16/2^32/2^63 boundaries; RLE block at bw=64",
        values,
    )

    # 3. Zero bit width: all-zero column, RLE body is 0 bytes.
    add_case("zero-width-rle", "16 zeros -> single RLE block, bit_width 0", [0] * 16)

    # 4. Max bit width: u64::MAX repeated.
    add_case(
        "max-width-rle",
        "u64::MAX x 8 -> single RLE block, bit_width 64",
        [U64_MAX] * 8,
    )

    # 5-7. Non-integral and exact tail groups.
    add_case("tail-group-1", "single literal, 7 padding slots", [123])
    add_case("tail-group-7", "7 literals, 1 padding slot", [10, 20, 30, 40, 50, 60, 70])
    add_case("exact-group-8", "8 literals, no padding", [1, 3, 5, 7, 11, 13, 17, 19])

    # 8. Boundary values at mode switches must not be dropped or duplicated.
    values = []
    values += [3, 3]                  # short run -> literals
    values += [5] * 8                 # RLE
    values += [5]                     # single 5 after the RLE run -> literal
    values += [9] * 9                 # RLE of 9 (non-multiple-of-8 count)
    values += [3]                     # trailing literal equal to first run's value
    add_case(
        "mode-switch-boundaries",
        "literal/RLE/literal transitions around equal values; RLE count 9",
        values,
    )

    # 9. Long mixed stress: deterministic pattern, many blocks.
    values = []
    for k in range(12):
        values += [k * 1000] * (8 + (k % 3))      # RLE runs of 8..10
        values += [k, k + 1, k + 2, k + 3, k + 4]  # 5 literals
    add_case(
        "mixed-stress-12-blocks",
        "12 repetitions of (RLE run + 5 literals); exercises many block boundaries",
        values,
    )


def main():
    build_cases()
    manifest = {
        "format": "RBP1",
        "format_version": 1,
        "generator": "tools/gen_fixtures.py (independent Python implementation)",
        "rle_min_run": RLE_MIN_RUN,
        "cases": CASES,
    }
    out_dir = os.path.join(os.path.dirname(__file__), "..", "fixtures")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "manifest.json")
    with open(path, "w") as f:
        json.dump(manifest, f, indent=2)
    total = sum(c["value_count"] for c in CASES)
    print(f"wrote {path}: {len(CASES)} cases, {total} values total")
    for c in CASES:
        print(f"  {c['name']}: {c['value_count']} values, {len(c['hex'])//2} bytes")


if __name__ == "__main__":
    main()
