# secagg-teach:本地多客户端向量安全聚合教学协议

Bonawitz 风格(CCS 2017)的成对掩码安全聚合教学实现:多客户端各自提交
向量,服务器只能得到**存活客户端向量之和**,得不到任何单个输入;
支持受限掉线恢复。全部在本地进程内运行,数据为合成夹具,无外部账号。

## 安全假设(明确声明,不夸大)

- **半诚实(semi-honest)**:服务器好奇但按协议可观测地行动;客户端按协议执行。
- **不串谋**:服务器不与客户端合谋,客户端之间不串谋。
- **不宣称生产安全**:未实现签名/证书链防恶意服务器、未做零知识证明、
  未防侧信道。本代码用于教学与协议行为验证。

## 核心安全约束(实现强制,测试直接攻击)

1. **活跃集合冻结**:轮2(掩码输入)收尾时冻结活跃集合,之后任何集合变更
   (新客户端注册、补交掩码输入、恢复目标与冻结集合不符)一律拒绝。
2. **掩码与秘密不可兼得**:恢复阶段对同一目标客户端,只允许收集
   掩码种子 `b` 的份额(存活者)**或**协商私钥 `c_SK` 的份额(掉线者),
   两者同时出现即判定越权/集合变更攻击,拒绝并写审计。
3. **低于门限明确中止**:存活数 < t 或任一目标的份额 < t 时,
   运行进入 `ABORTED`,不输出任何部分结果。
4. **模数与编码防溢出**:环 Z_R (R = 2^64),定点缩放 SCALE = 2^20;
   建运行时校验 `max_clients * elem_bound <= R/2 - 1`,保证求和不回绕。

## 协议轮次

| 轮次 | 阶段 | 内容 |
|------|------|------|
| 0 | KEYS | 客户端生成两对 X25519 密钥(c: 成对掩码协商,s: 份额加密),广播公钥 |
| 1 | SHARES | 各自把掩码种子 `b` 与 `c_SK` 做 t-of-n Shamir 分享,AES-GCM 加密后分发 |
| 2 | MASKED | 提交 `y = x + PRG(b) ± Σ PRG(成对种子)`;收尾时**冻结活跃集合** |
| 3 | RECOVERY | 存活者上交:存活目标的 b 份额 + 掉线目标的 c_SK 份额;服务器重建并求和 |

掩码抵消关系:成对掩码在双方都存活时自动抵消;存活者自掩码由服务器
重建 `b` 后减去;掉线者与存活者之间的成对掩码由服务器重建掉线者
`c_SK` 后重算抵消。

## 模块关系

```
secagg/
├── errors.py           四类错误契约: input_error / state_conflict /
│                       resource_exhausted / computation_failure
├── encoding.py         定点编码、Z_R 环运算、防溢出上界校验
├── shamir.py           Shamir t-of-n 分享, GF(2^521-1), 纯 Python + secrets
├── crypto_adapters.py  成熟密码适配: X25519/HKDF/AES-GCM (cryptography),
│                       AES-CTR 掩码扩展 (PyCryptodome); 不自实现原语
├── state.py            SQLite 状态与追加式审计日志(按 run_id 归档)
├── protocol.py         服务器侧轮次状态机、冻结点、恢复与重建(核心不变量)
├── server.py           FastAPI HTTP 适配层, 错误类别 -> 400/409/413/422
├── client.py           客户端状态机, 本地校验冻结集合一致性
└── transport.py        httpx 传输适配, 错误体还原为 SecAggError
tests/
├── conftest.py         Harness 驱动器; 失败时自动转储带 run_id 的审计日志
├── reference.py        独立明文参考(纯标准库, 不导入被测模块)
├── test_encoding.py    编码/防溢出/错误类别
├── test_shamir.py      门限分享语义
├── test_crypto_adapters.py  协商对称性/AEAD 防篡改/掩码确定性
├── test_e2e.py         3-5 客户端、各阶段掉线、重复恢复、阈值中止
└── test_attacks.py     集合变更攻击夹具、越权恢复、阶段违规、资源耗尽
```

数据契约:客户端 ↔ 服务器仅传 JSON(公钥/密文/份额/掩码向量均 base64 或
环元素整数);服务器内部只经 `StateStore` 读写 SQLite;错误一律以
`SecAggError(category, code, message, detail)` 跨模块传递。

## 依赖版本(本地已验证)

- Python 3.12.3
- fastapi 0.141.1 / uvicorn 0.54.0 / pydantic 2.13.5
- cryptography 41.0.7(X25519、HKDF-SHA256、AES-256-GCM)
- pycryptodome 3.23.0(AES-256-CTR 掩码扩展)
- httpx 0.28.1 / pytest 9.1.1

安装:`pip install -r requirements.txt`

## 本地验证命令与预期判断

```bash
cd <本目录>
python3 -m pytest -q            # 预期: 42 passed
python3 -m pytest -q --cov=secagg --cov-report=term-missing
                                # 预期: TOTAL 覆盖率 >= 80% (实测 91%)
python3 scripts/demo.py         # 预期: 聚合结果与独立明文参考一致, 退出码 0
```

判断方式:

- 每个端到端用例把服务器输出的环元素和**解码后与 `tests/reference.py`
  的独立明文浮点求和对比**,容差 = 客户端数 × 0.5 / 2^20(定点量化上界)。
- 攻击夹具断言具体 HTTP 状态码 + 错误类别 + 错误码
  (如 `409 state_conflict/mask_and_secret_overlap`),
  并验证被拒运行未被污染(能继续完成或已明确中止)。
- 任一用例失败时,conftest 自动打印该运行的 `run_id` 与完整审计日志
  (阶段、事件、判断理由),可据此重放定位。

## 测试状态(如实记录)

- **42 个用例全部通过**(连续 5 轮复跑稳定,密钥为随机生成,
  已修复 Shamir 重建前导零导致的偶发失败)。
- 覆盖率 91%(未覆盖部分主要为防御性分支与传输层异常兜底)。
- 已知告警:starlette TestClient 提示 httpx 弃用警告,不影响判定。
- 未实现/未测试:恶意服务器与客户端合谋场景、网络分区下的真实异步
  掉线(本实现用"不再调用后续轮次"模拟)、大规模性能压测。
