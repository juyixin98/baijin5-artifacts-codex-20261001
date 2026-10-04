# fixtures/

本地合成样例数据与互操作测试向量，全部由 `tools/reference_chunker.py
--generate-vectors fixtures` 生成（独立 Python 参考实现，与被测 Rust 内核不共享代码）。

- `vectors.json` — 8 个用例的参数、载荷摘要、期望边界与每块 sha256。
- `*.bin` — 各用例的载荷字节：
  - `empty.bin` / `tiny.bin` — 空输入与短于 min_size 的输入（边界规则用例）
  - `text.bin` — 重复英文句子（周期文本）
  - `repeated.bin` — 长重复字节段（验证 max_size 强制切分）
  - `random.bin` — 种子化伪随机字节
  - `patterned.bin` — 非零目标边界模式（pattern = 0x5A）用例
  - `insert_base.bin` / `insert_modified.bin` — 后者为前者中部插入 100 字节，
    用于局部修改影响范围分析

重新生成：`python3 tools/reference_chunker.py --generate-vectors fixtures`
（确定性：所有随机源均带固定种子，重新生成结果逐字节相同。）
