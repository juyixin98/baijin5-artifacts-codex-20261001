# paillier-agg-local:Paillier 密文求和与加权聚合本地测试服务

纯后端本地测试服务:多个参与者在本地用批次公钥加密私有整数并提交密文,
服务端在**不解密**的前提下完成密文求和与**明文权重**的加权聚合,
解密方解密得到加权和,并可与独立明文参考比对。

## 支持范围(只声明算法真实具备的能力)

Paillier 是**加法同态**方案,本服务只提供:

- 密文 + 密文(求和)
- 密文 × 明文标量(加权,标量即可公开的整数权重,支持负数)

**不支持**密文 × 密文、比较、除法等通用密文计算;
`POST /batches/{id}/multiply-ciphertexts` 会明确返回
`UNSUPPORTED_OPERATION`,而不是假装成功。

## 编码与上界规则(核心取舍)

- 明文 `x`:有符号整数,`|x| <= max_plaintext_abs`(默认 `2^31-1`),
  编码为 `x mod n`(非负剩余)。
- 权重 `w`:有符号整数,固定范围 `|w| <= max_weight_abs`(默认 `2^15`)。
- 每个批次创建时绑定一对密钥与编码参数,并声明**总和上界** `B < n/2`
  (默认 `n/4`)。服务端按最坏情况累加 `used_bound += declared_abs * |w|`,
  会突破 `B` 的提交在事前以 `OVERFLOW_RISK` 拒绝。
- 解密得到剩余后映射到对称区间 `(-n/2, n/2]`,再检查 `|v| <= B`:
  **越界即判定发生模回绕,返回 `OVERFLOW_DETECTED`,绝不把回绕结果
  解释成普通负值/正数**。这是本服务最重要的正确性取舍——
  同态加法在 `Z_n` 上进行,真值超过上界时密文层面无法察觉,
  只能靠事前上界约束 + 事后解码拒绝来防止静默错误。

## 信任假设

- **参与者**:持有批次公钥,在本地加密;需诚实声明 `declared_abs = |x|`。
  低报声明可绕过事前上界检查,但会在解码阶段触发 `OVERFLOW_DETECTED`
  或在验证阶段得到 `MISMATCH`,无法静默通过(有对应测试)。
- **聚合方(本服务)**:只见密文与声明上界,聚合不需要私钥。
- **解密方**:本地测试模式下私钥用 Fernet 加密后与批次同库存放,
  由本进程执行解密(接口 `POST /batches/{id}/decrypt`);
  真实部署应把私钥交给独立解密方,聚合方不持有私钥。
- **密钥绑定**:提交必须带批次公钥指纹(SHA-256 of n),不一致即
  `KEY_MISMATCH`;指纹是诚实客户端绑定,恶意客户端可伪造指纹,
  但混入的他钥密文会在验证阶段暴露(测试 `test_mixed_key_ciphertext_is_detected`)。

## 工程结构

```
app/
  config.py          配置层(环境变量 PAGG_*)
  encoding.py        协议编码:有符号编解码、值域与上界规则
  crypto_adapter.py  密码适配:phe 薄封装,私钥 Fernet 加密落盘
  store.py           状态与审计:SQLite(批次/提交/聚合/审计事件)
  service.py         业务编排:批次、提交校验、聚合、解密、验证
  verifier.py        独立验证:明文参考直算与判定(不依赖核心实现)
  api.py             FastAPI 接口,统一信封 {ok, data, error}
  main.py            uvicorn 入口
tests/               独立测试层(38 例),artifacts/ 为结构化运行日志
scripts/run_dev.sh   本地启动
scripts/client_demo.py  示例客户端(完整流程)
requirements.txt     锁定依赖(pip freeze)
```

## 本地启动

```bash
bash scripts/run_dev.sh          # 建 venv、装锁定依赖、起服务于 127.0.0.1:8000
```

环境变量:`PAGG_DB_PATH`(默认 `pagg.db`)、`PAGG_KEY_SIZE`(默认 2048,
测试用 1024)、`PAGG_MAX_PLAINTEXT_ABS`、`PAGG_MAX_WEIGHT_ABS`、
`PAGG_FERNET_KEY`(私钥落盘加密密钥;不设置则进程内临时生成,
重启后历史批次不可解密,仅本地测试可接受)。

## 示例请求

完整可运行示例:`python scripts/client_demo.py`(需服务已启动)。
手工 curl(密文需用 phe 在本地生成,见 client_demo.py):

```bash
# 健康检查:返回 run_id 与各组件版本
curl -s localhost:8000/health

# 创建批次(返回公钥模数 n、密钥指纹、上界等)
curl -s -X POST localhost:8000/batches -H 'content-type: application/json' \
  -d '{"label": "demo"}'

# 提交密文(ciphertext 为十进制字符串;declared_abs 为声明的 |x|)
curl -s -X POST localhost:8000/batches/<batch_id>/submissions \
  -H 'content-type: application/json' -d '{
    "participant_id": "alice", "ciphertext": "<c>", "exponent": 0,
    "weight": "2", "key_fingerprint": "<fp>", "declared_abs": "4"}'

# 聚合 -> 解密 -> 独立验证 -> 审计
curl -s -X POST localhost:8000/batches/<batch_id>/aggregate
curl -s -X POST localhost:8000/batches/<batch_id>/decrypt
curl -s -X POST localhost:8000/batches/<batch_id>/verify \
  -H 'content-type: application/json' -d '{"reference": [
    {"participant_id": "alice", "value": "4"}]}'
curl -s localhost:8000/batches/<batch_id>/audit
```

响应信封统一为 `{"ok": bool, "data": ..., "error": {category, message, detail}}`;
失败类别:`VALIDATION / NOT_FOUND / KEY_MISMATCH / OUT_OF_RANGE /
OVERFLOW_RISK / OVERFLOW_DETECTED / UNSUPPORTED_OPERATION / INTERNAL`。

## 测试

```bash
.venv/bin/python -m pytest                 # 38 例
.venv/bin/python -m pytest --cov=app       # 覆盖率(当前 95%)
```

- 参考答案由 `tests/reference.py`(独立纯 Python 直算)或手算字面量给出,
  不由被测核心生成;覆盖:小整数手算、负权、超范围声明、上界风险、
  混密钥、不诚实声明导致的回绕拒绝、审计链路与错误类别。
- 每次运行在 `tests/artifacts/test_run_<时间>_<run_id>.jsonl` 写结构化日志:
  头部含 Python/phe/fastapi/cryptography/pytest 版本与 run_id,
  每个用例记录结果与耗时,关键用例记录输入、计算步骤与判定依据。
- 当前无跳过/未执行项;最近一次运行:38 passed,覆盖率 95%。

## 关键取舍说明

1. **上界靠声明而非密码学证明**:参与者声明 `declared_abs`,服务只做
   簿记;不诚实声明在解码/验证阶段暴露。零知识范围证明超出本地测试范围。
2. **私钥与聚合方同进程**:仅为本地测试便利,已用 Fernet 加密落盘并
   在 README 明示;生产应拆分解密方。
3. **大整数以十进制字符串传输/存储**:避免 JSON 与 SQLite 的精度问题。
4. **单进程 SQLite + 锁**:本地测试足够,不声明分布式并发能力。
