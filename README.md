# Sparse Embedding Gradient Update Service

大词表嵌入表的**稀疏梯度累积与优化服务**。重复索引的梯度先聚合再进入裁剪与优化器；
裁剪严格区分「声明式全局范数」与「行级范数」两种模式，不混用；索引越界整批拒绝；
持久化只写实际触及行并保留事务边界。

技术栈：Python 3.10+ / FastAPI / NumPy（无原生扩展、无外部账号、全部本地合成夹具）。

---

## 1. 工程结构（按张量类型 / 计算图 / 训练状态 / 数值验证分层）

```
sparse_embeddings/
├── tensor_types.py            # 层1：校验过的稀疏批类型 + 稳定错误类别
├── graph.py                   # 层2：重复索引聚合 / 全局或行级裁剪 / SGD·动量SGD 内核
├── state.py                   # 层3：表状态、prepare→持久化→swap 事务编排
├── persistence.py             # 仅触及行检查点：临时文件+fsync+原子 rename
├── validation/
│   ├── dense_reference.py     # 层4：完全独立的稠密参考（不 import 被测内核）
│   └── equivalence.py         # 稀疏/稠密逐字段等价核验
├── config.py                  # 独立配置层（fail-fast）
├── observability.py           # JSONL 结构化日志（run_id 关联、版本、判定）
└── api/                       # FastAPI 路由与 schema
fixtures/                      # 最小合成数据夹具 + 确定性生成脚本
examples/                      # 直连与 HTTP 调用示例
tests/                         # 独立测试（断言具体数值与失败类别）
results/                       # 真实运行留存结果（正常 + 异常）
```

## 2. 关键语义（复核重点）

### 2.1 重复索引先聚合
一个批是 COO 三元组 `indices (nnz,)`、`values (nnz,dim)`、`scale`。
默认 `scale = nnz`（按 token 求均值）。聚合在**任何裁剪/更新之前**完成：

```
g_i = (Σ token 贡献，row==i) / scale        # 逐列 bincount，确定顺序
```

### 2.2 优化器步数规则（明确，不做隐式选择）
| 情形 | 是否计步 | 动量缓冲 | 权重 |
|---|---|---|---|
| 空批（0 token） | **否**，`global_step` 不增，状态字节不变 | 不变 | 不变 |
| 未触及行 | **否** | 不读不写，保持原值（通常为 0） | 保持种子初值 |
| 触及但聚合梯度恰为 0 | **是** | `v ← μ·v`（衰减，不清零） | `w ← w − lr·μ·v`（若缓冲非零仍移动） |
| 触及且梯度非零 | 是 | `v ← μ·v + g` | `w ← w − lr·v` |

零梯度触及行仍计步，这与稠密实现对该行的行为逐位一致，因此稀疏/稠密等价性是精确的。
该行被记录在响应的 `zero_gradient_rows` 中，判定依据可审计。

### 2.3 裁剪：全局与行级严格分离
裁剪模式在建表时**一次性声明**（`global` 或 `row`），请求不能按行/按批切换：

- `global`：`‖G‖₂ = sqrt(Σ_{触及行,列} g²)`，单一系数 `min(1, max_norm/‖G‖₂)` 乘到每一行；
- `row`：每行独立 `min(1, max_norm/‖g_i‖₂)`，行间不互相影响。

两条分支独立实现，不存在「全局阈值 + 行级缩放」的混合路径。配置层会拒绝任何第三种模式。

### 2.4 索引越界整批拒绝
校验为**全有或全无**，顺序固定：结构（形状/秩/dtype/scale）→ 非有限值
（`numeric_error`）→ 索引整数性（`validation_error`）→ 索引范围
（`index_out_of_range`，返回首个坏位置、坏值、坏值总数）。任何失败都在触碰状态前抛出，
不存在部分应用。

### 2.5 持久化只更新触及状态 + 事务边界
每个步进批的提交顺序：

1. 内核先生成**候选数组**，存活状态不动；
2. 从候选中导出**仅曾触及行**（权重、动量、行步数、全局步、元数据）；
3. 写入同目录临时文件 → `flush` → `fsync`；
4. `os.replace()` 原子覆盖正式检查点（唯一提交点）→ `fsync` 目录；
5. 之后才在表锁内交换存活引用。

持久化失败（测试中以故障注入模拟）会在交换前抛出：内存状态不切换，旧检查点字节不变，
临时文件被清理。未触及行从不入盘（可由配置种子复现），检查点规模只随触及行数增长。

## 3. 快速开始

```bash
python3 -m pip install -r requirements.txt          # 或按 requirements.lock 复现
python3 -m fixtures.generate_fixtures               # 生成夹具（已随仓库提供）
python3 -m pytest                                   # 87 项测试 + 大词表慢测试
python3 -m examples.direct_api_demo                 # 无需服务的端到端演示

# HTTP 方式
python3 -m sparse_embeddings --host 127.0.0.1 --port 8000
SPARSE_EMBEDDINGS_BASE_URL=http://127.0.0.1:8000 python3 -m examples.http_client_example
```

环境变量：`SPARSE_EMBEDDINGS_DATA_DIR`、`SPARSE_EMBEDDINGS_LOG`、`SPARSE_EMBEDDINGS_STDERR`。

### HTTP 摘要

| 方法与路径 | 说明 |
|---|---|
| `GET /health` | 版本（包/NumPy/Python） |
| `POST /tables` | 建表，声明优化器与裁剪模式 |
| `POST /tables/{name}/batches` | 提交稀疏批；返回完整判定依据 |
| `GET  /tables/{name}` | 表信息与曾触及行数 |
| `POST /tables/{name}/rows/query` | 查指定行的权重/动量/行步数 |

错误永不伪装成成功：领域错误→对应 4xx 与稳定 `category`；请求结构错误→422
`validation_error`；未知异常→500 `internal_error`。空批是 **200 的 no-step 成功**
（`stepped=false, reason=empty_batch_no_step`），与错误明确区分。

## 4. 测试与可复核性

- 参考答案**全部来自独立稠密实现** `validation/dense_reference.py`：显式 Python
  累加循环、手写稠密裁剪、稠密掩码更新，**不导入任何被测内核**，避免共享代码掩盖偏差。
- 覆盖场景：重复 ID、热/冷行交替、空批、大梯度（触发两种裁剪）、零梯度触及行、
  越界整批拒绝；并跨 `sgd`/`momentum_sgd` × `global`/`row`/无裁剪 × `float32`/`float64`
  做随机流等价性核验，断言每个权重行、动量行、行步数、全局步、范数与裁剪系数。
- 断言具体结果与失败类别（坏位置、坏值、HTTP 类别），不止「接口可调用」。
- 结构化日志每行一个 JSON：`run_id` 可关联到具体输入与运行；含版本、进度
  （token 数、触及行、裁剪前范数、逐行系数）、`verdict`（applied/skipped/rejected/
  committed/internal_error）。异常与未知状态不会被统一记为成功。

复现最近一次已留存的运行：

```bash
cat results/direct_api_demo_output.txt           # 直连演示（正常+异常）
cat results/http_client_output.txt               # 真实 HTTP 服务调用结果
cat results/pytest_output.txt                    # 全量测试结果
cat results/coverage_report.txt                  # 覆盖率
```

## 5. 依赖锁定

`requirements.txt` 固定直接依赖；`requirements.lock` 为测试环境的完整传递锁定
（Python 3.12.3 / NumPy 2.4.6 / FastAPI 0.141.1 / pydantic 2.13.5 / pytest 9.1.1）。
