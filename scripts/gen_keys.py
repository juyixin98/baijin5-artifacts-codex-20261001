#!/usr/bin/env python3
"""生成本地开发密钥环（config/dev_keys.json）。仅用于本地合成环境。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from blindex.config import DEFAULT_DOMAIN, DEFAULT_INDEX_BITS, generate_keyring, save_keyring


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("config/dev_keys.json")
    if out.exists():
        print(f"已存在，不覆盖: {out}")
        return
    save_keyring(generate_keyring(DEFAULT_DOMAIN, DEFAULT_INDEX_BITS), out)
    print(f"已生成密钥环: {out} (domain={DEFAULT_DOMAIN}, index_bits={DEFAULT_INDEX_BITS})")


if __name__ == "__main__":
    main()
