# TensorCraft

连续与非连续**步幅布局张量**的切片、转置、reshape 与基本运算后端。
Python · FastAPI · NumPy，全部数据为本地合成夹具，不依赖任何外部账号或业务数据。

核心把一个张量严格表示为

```
offset(i₀, …, iₙ₋₁) = storage_offset + Σ iₖ · strideₖ
```

即 **一维存储 + (shape, strides, storage_offset) 布局**。切片、转置、
零拷贝 reshape、广播都只是换一个布局指向同一块存储；只有显式物化或
确实需要时才复制。

---

## 模块划分

| 包 | 职责 |
|---|---|
| `tensorcraft/tensor/` | 张量类型与布局内核：`layout.py`（形状/步幅/偏移、连续性、零拷贝 reshape 判定）、`tensor.py`（视图、别名/重叠检测、写入策略）、`indexing.py`（索引解析）、`storage.py`（一维属主存储）、`ops.py`（运算）、`dtypes.py` |
| `tensorcraft/graph/` | 计算图：严格校验（未知引用、自环、环、重复 id）、拓扑调度、逐节点执行追踪（含是否复制/别名） |
| `tensorcraft/state/` | 有界张量/图/训练会话注册表；真实的线性模型全批量 SGD 训练器（状态机、暂停/恢复、检查点） |
| `tensorcraft/validation/` | **独立** NumPy 预言机（从不导入被测核心）、双解释器差分验证、别名/重叠写入专项检查、合成场景夹具 |
| `tensorcraft/api/` | FastAPI 服务：请求身份、分类错误 → HTTP 状态映射、结构化日志 |

没有任何核心机制是硬编码演示：训练梯度、复制/别名结论、参考值都来自
真实运算或真实 NumPy 观测。

---

## 关键语义与取舍

### 1. 形状、步幅、存储偏移共同决定访问

- 步幅以**元素数**计（不是字节），负步幅表示反向视图，零步幅表示广播轴。
- 任意位置访问前都做越界检查；维度乘积与偏移在访问前做溢出检查
  （上限 `2**63 - 1`，对应 64 位 `NPY_INTP`）。
- 空张量（含 size-0 轴）遵循 NumPy 约定：不寻址任何元素，因此不约束
  存储区域，且 C/F 连续标志都为真。

### 2. reshape 仅在合法布局下零拷贝

零拷贝判定是 NumPy 2.4.6 `_attempt_nocopy_reshape`
（`numpy/_core/src/multiarray/shape.c`）的逐行移植，连续性标志移植自
`_UpdateContiguousFlags`（`flagsobject.c`）。判定顺序与 NumPy 一致：

1. 形状不变 → 同一布局；
2. 空布局 → 视图；
3. 按请求顺序已连续 → 规范步幅视图；
4. 否则运行无拷贝尝试（在部分非连续布局上仍可能成功，如仅合并/拆分
   连续轴、插入长度 1 轴）；
5. 失败时复制；若 `allow_copy=false` 则返回 `NON_CONTIGUOUS_VIEW` 错误。

经典例子：**转置后 reshape 会复制，连续数组直接 reshape 是视图。**

### 3. 重叠视图写入：拒绝，或确定性临时副本

写入前检测目标视图是否自重叠（不同位置映射到同一存储元素）：

- `policy="reject"`（默认）：拒绝，错误类别 `OVERLAPPING_WRITE`，
  缓冲区**保持原样**；
- `policy="temp_copy"`：先把源值取到独立临时区，再按 C 序散布，
  **每个存储偏移以最后一次写入为准**——结果确定、可重复。

源与目标共享存储时，源值也总是先整体快照，避免散布顺序影响结果。
验证接口会同时记录 NumPy 自身对重叠写入的行为作为对照（NumPy 不做
此保护，因此该策略是本项目的显式取舍）。

### 4. dtype 不做隐式提升

逐元素运算要求两个操作数 dtype 完全相同，混合类型返回
`DTYPE_MISMATCH`，需显式 `astype`。例外对齐 NumPy：整数真除
（`divide`）提升为 `float64`。支持类型：int8–64、uint8–64、
float32/64。布尔/复数/object 等显式拒绝。

### 5. 明确的支持范围

- 支持：整数、切片（含负步长）、`Ellipsis`、`np.newaxis`；转置/换轴；
  C/F 序 reshape（含一个 `-1`）；广播；逐元素算术/比较、一元运算、
  matmul（1-D/2-D/同批次批矩阵乘）、求和归约。
- 不支持（显式报错而非静默出错）：fancy/布尔数组索引、matmul 批次维
  广播、`order="K"`、字符串/复数/布尔 dtype。

### 6. 失败即分类

每个核心错误带稳定 `category`（如 `INDEX_OUT_OF_BOUNDS`、
`SIZE_MISMATCH`、`OVERFLOW`、`OVERLAPPING_WRITE`、
`NON_CONTIGUOUS_VIEW`、`BROADCAST_ERROR`、`GRAPH_ERROR`……），
测试断言的是**具体结果与失败类别**，不是“接口能调用”。

---

## 本地启动

需要 Python 3.10+（开发环境 3.12.3）。

```bash
python3 -m pip install -r requirements.txt

# 直接用 uvicorn
python3 -m uvicorn tensorcraft.main:app --host 127.0.0.1 --port 8000

# 或用脚本
./scripts/run_server.sh
```

启动后：

- 交互式文档：http://127.0.0.1:8000/docs
- 健康检查：http://127.0.0.1:8000/health

可用 `TENSORCRAFT_CONFIG=config/default.yaml` 指定配置（监听地址、
注册表容量、重叠写入策略）。

---

## 示例请求

```bash
# 1) 创建张量
curl -s -X POST localhost:8000/api/v1/tensors \
  -H 'content-type: application/json' \
  -d '{"data":[[1,2,3],[4,5,6]],"dtype":"int64","handle":"a"}'

# 2) 转置（视图，copied=false）
curl -s -X POST localhost:8000/api/v1/tensors/a/transpose -d '{}' \
  -H 'content-type: application/json'

# 3) 对转置结果 reshape —— 必然复制（copied=true）
curl -s -X POST localhost:8000/api/v1/tensors/t1/reshape \
  -H 'content-type: application/json' -d '{"shape":[6],"order":"C"}'

# 4) 负步长切片
curl -s -X POST localhost:8000/api/v1/tensors/a/slice \
  -H 'content-type: application/json' \
  -d '{"index":[[null,null,-1]]}'

# 5) 计算图：转置 -> 乘 2，返回逐节点复制/别名追踪
curl -s -X POST localhost:8000/api/v1/graphs \
  -H 'content-type: application/json' \
  -d '{"nodes":[{"id":"t","op":"transpose","inputs":["x"]},
                {"id":"s","op":"scalar_multiply","inputs":["t"],"params":{"value":2}}],
       "outputs":["s"],"inputs":["x"],"handle":"g"}'
curl -s -X POST localhost:8000/api/v1/graphs/g/execute \
  -H 'content-type: application/json' \
  -d '{"bindings":{"x":"a"},"include_values":true}'

# 6) 内置差分验证（核心 vs 独立 NumPy 预言机）
curl -s localhost:8000/api/v1/verify/all | python3 -m json.tool
```

`scripts/example_requests.sh` 是可直接运行的完整版本（含重叠写入被
拒绝与临时副本放行的对照）。Python 示例见 `scripts/examples.py`。

---

## 请求身份与可解释性

- 每个请求可带 `X-Request-ID`；未带则生成 `req-<hex>`，响应头与
  JSON 错误体都回带，日志行带 `[req=…]`。
- 图执行返回**逐节点追踪**：op、输入/输出存储 token、`copied`、
  `aliases_input`、结果 shape/strides、耗时。
- 验证结果分三类：`passed` / `failed`（双方具体分歧）/
  `uncertain`（预言机信号本身不可判别，单独列出，不计为通过）。
  空结果的“复制 vs 视图”就属于不可判别项：空数组不分配元素，
  NumPy 自身的 `owndata`/`shares_memory` 信号相互矛盾。

---

## 测试与验证

```bash
python3 -m pytest                       # 全部测试 + 覆盖率
python3 -m pytest tests/test_layout.py  # 单个文件
```

验证分四层：

1. **内核单测**：连续性、移植的 reshape 判定（含 200 组随机布局与
   NumPy `copy=False` 结果对拍）、越界/溢出、索引；
2. **契约测试**：转置后 reshape、广播零步幅、重叠切片、空张量，断言
   具体值与复制/别名关系；
3. **差分验证**：同一场景在核心解释器与**仅依赖 NumPy 的独立预言机**
   上各跑一遍，逐命名步骤比值与内存事实；参考答案不由被测核心产生；
4. **API/训练集成测试**：HTTP 状态、错误分类、请求身份、真实 SGD
   收敛到已知权重、后台暂停/恢复、检查点独立性。

最近一次完整运行结果记录在 `TEST_RESULTS.md`（含失败项与未执行项，
如有）。
