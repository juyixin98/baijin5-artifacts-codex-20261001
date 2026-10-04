# keytree — 本地测试根密钥派生树服务

从本地测试根密钥出发，按 **租户 / 用途 / 版本 / 上下文** 四级派生密钥树。
所有密码学运算委托给成熟后端（`cryptography` 为主、`pycryptodome` 为独立
交叉验证），本项目不实现任何底层哈希原语。

## 快速开始

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 独立验收（黄金向量 + 碰撞反例 + 确定性/分离性 + 错误类别 + 日志卫生）
.venv/bin/python scripts/verify.py

# 完整测试套件
.venv/bin/python -m pytest -q

# 启动 HTTP 服务（本地合成数据，无需任何生产账号）
.venv/bin/uvicorn "keytree.api:create_app" --factory  # 见下
```

HTTP 服务需要数据库与日志路径，用工厂参数启动：

```bash
.venv/bin/python - <<'EOF'
import uvicorn
from keytree.api import create_app
uvicorn.run(create_app("state.db", "run.log"), host="127.0.0.1", port=8000)
EOF
```

## 模块边界

| 模块 | 职责 | 数据/错误契约 |
|---|---|---|
| `keytree/encoding.py` | TLV 协议编码，无状态无密码学 | 越界/畸形输入 → `input_error` |
| `keytree/crypto_adapter.py` | HKDF-SHA256 适配层（双后端） | 长度超算法上限 → `resource_exhausted`；后端异常 → `computation_failure` |
| `keytree/identity.py` | 密钥身份模型与 `key_id` | 非法字段 → `input_error` |
| `keytree/store.py` | SQLite 状态（根、注册表、名称绑定）与审计 | 状态冲突 → `state_conflict`；存储故障 → `computation_failure` |
| `keytree/service.py` | 派生树编排、运行级诊断日志 | 聚合上述类别并写审计 |
| `keytree/api.py` | FastAPI HTTP 边界 | 类别 → 400/409/413/500 |

## 验证的独立性

- HKDF 正确性：RFC 5869 黄金向量（外部标准），两个后端各自断言。
- 树派生正确性：`tests/fixtures/golden_tree_vectors.json` 由
  `tools/independent_vector_gen.py` 生成——该脚本**不导入任何 keytree
  模块**，按文档规范独立实现 TLV 与树遍历，且只用 PyCryptodome；被测
  服务使用 `cryptography` 后端。参考答案不来自被测核心。
- 如需重新生成黄金向量：`.venv/bin/python tools/independent_vector_gen.py`

边界语义、错误类别、日志契约与"无法执行的检查"清单见
[docs/semantics.md](docs/semantics.md)。
