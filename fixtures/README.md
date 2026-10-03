# Fixtures

所有夹具均为本地合成数据，无生产账号或真实业务数据。

| 文件 | 来源 | 用途 |
|------|------|------|
| `additive4.json` | 手工计算（树 `((A:1,B:2):3,C:2,D:3)` 的枝长手工求和） | 加性矩阵精确还原、平局稳定性、路径距离核验 |
| `negative_branch.json` | 手工构造的非加性 4 类矩阵 | 负枝长三种声明模式（error/report/clamp） |
| `sequences.fasta` | 手工设计的 11 位点合成序列（1+2+2+3 个私有位点 + 3 个内部分裂位点） | 合成序列解析 → Hamming 距离 → 与 additive4 同矩阵 |
| `additive6.json` | `generate.py` 对手工定义的边列表做 BFS 路径求和 | 6 类加性还原、拓扑分裂比较 |
| `noisy6.json` | `generate.py` 加确定性噪声 `0.1*(((i+1)*(j+3))%3-1)` | 非加性输入的残差报告 |
| `duplicates.json` | `generate.py` 由 additive4 复制 D 行为 E | 重复叶的零长姐妹枝 |

参考值（期望 Newick、残差、路径距离）均为手工计算或由独立于被测核心的
`generate.py` 产生；测试侧的 `tests/independent.py` 提供第三方的 Newick
解析/路径/分裂实现，用于交叉核验，不由被测核心生成参考答案。

重新生成派生夹具：`python3 fixtures/generate.py`
