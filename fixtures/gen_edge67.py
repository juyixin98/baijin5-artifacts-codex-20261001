#!/usr/bin/env python3
"""Deterministically generate the non-machine-word-sized edge fixture.

67 rows -> two 64-bit words with 3 real tail bits and 61 padding positions.
Nullable columns `a` (NULL when id % 3 == 0) and `b` (NULL when id % 4 == 0)
guarantee NULL-crossing combinations on every word and on the tail. The last
logical row (id 66, a tail bit) is deleted at v2 by edge67.toml.

The script has no dependency on the Rust engine: both the engine and the
independent oracle read its output, so it cannot bake in engine answers.
"""

import pathlib

OUT = pathlib.Path(__file__).with_name("edge67.csv")

N = 67


def main() -> None:
    lines = ["id,a,b,grp"]
    for i in range(N):
        a = "" if i % 3 == 0 else str(i % 5)
        b = "" if i % 4 == 0 else str(i % 3)
        grp = "k1" if i % 2 == 0 else "k0"
        lines.append(f"{i},{a},{b},{grp}")
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUT} with {N} rows (tail word carries {N % 64} real bits)")


if __name__ == "__main__":
    main()
