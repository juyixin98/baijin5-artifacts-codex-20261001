# mini-autodiff

一个从零实现的小型张量自动微分后端。核心反向传播为自研，NumPy **仅**承担张量
数值运算（不使用 `numpy` 之外的任何 autograd 实现，不调用 `torch.autograd` /
`jax` / 微分包等作为被测核心）。

- 广播二元运算（加/减/乘/除/负号）
- 矩阵乘法（2-D、1-D 点积、向量×矩阵、批量批广播）
- 归约（`sum` / `mean`，支持轴、多轴、`keepdims`、空维）
- 激活与数学函数（`relu` / `sigmoid` / `tanh` / `exp` / `log`）
- 计算图：拓扑反向、共享节点梯度累加、版本检测、图释放/保留
- 训练状态：参数容器、SGD、train/eval 模式
- 独立有限差分数值验证器（参考答案由**独立的纯 NumPy 函数**给出）
- FastAPI 校验服务与结构化诊断（记录/请求标识、脱敏）

## 目录结构（按职责拆分的真实模块）

```
autodiff/
  config.py       独立、不可变的配置 + no_grad 上下文
  tensor.py       张量类型：只读数据、版本计数、无梯度/零梯度区分
  ops.py          前向算子与 VJP（含广播反传 unbroadcast）
  graph.py        计算图引擎：拓扑反向、版本拒绝、释放/保留
  training.py     训练状态：ParameterBundle / SGD / 模式
  numeric.py      独立中心有限差分验证器（不含被测核心）
  diagnostics.py  带 record_id/request_id 的结构化诊断与脱敏
  fixtures.py     可复用合成夹具（核心前向 + 独立纯 NumPy 参考）
  api/            FastAPI 传输层：schemas / service / app
scripts/
  verify.py       端到端验证脚本（退出码反映通过/失败）
tests/            113 个 pytest 用例（具体数值 + 具体失败类别）
requirements.txt  固定版本依赖
```

## 安装与运行

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

# 1) 单元/集成测试
python3 -m pytest tests/

# 2) 端到端验证（全部夹具 + 原地写入守卫），非零退出即失败
python3 scripts/verify.py
python3 scripts/verify.py matmul_mlp --json   # 单场景 / JSON

# 3) HTTP 服务
python3 -m uvicorn autodiff.api.app:app --port 8000
#   GET  /health
#   GET  /scenarios
#   POST /verify/gradients  {"scenario": "broadcast_add"}
#   POST /verify/inplace    {"mutate": true}
```

## 四条验收规则的实现位置

1. **广播梯度对扩展轴求和，重复节点累加梯度**
   `autodiff/ops.py::unbroadcast` 先丢弃前导插入轴，再对尺寸为 1 的轴
   `keepdims` 求和并 reshape；`autodiff/graph.py` 用按节点的余切表对多分支
   消费者求和，叶子通过 `Tensor.accumulate_grad` 累加。
2. **原地修改经版本检测拒绝**
   张量底层数组被设为**只读**，裸写 `t.data[...]=` 立即抛 `ValueError`；
   受认可的更新走 `Tensor.set_data`，每次令 `_version += 1`。节点在捕获时
   快照输入版本，反向前整体校验，不匹配抛 `StaleGraphError`
   （分类 `REJECTED`），且不写入任何 `.grad`。
3. **无梯度与零梯度区分**
   `grad is None` = 未参与/未到达（无梯度）；`grad` 为全零 `ndarray` =
   到达但余切为零（零梯度）。优化器对 `None` 跳过并诊断，绝不隐式当零。
4. **图释放与保留策略明确**
   `backward()` 默认 `retain_graph=False`：遍历后所有节点释放输入/输出引用、
   断开回链（中间结果标记 `_graph_detached`），再次 `backward` 或把旧输出接
   入新图均抛 `GraphReleasedError`（需显式 `.detach()` 才能当常量）。
   `retain_graph=True` 保留图，可重复反向，梯度在叶子上累加。

## 边界语义（文档明确约定）

- **空维归约**：`mean` 对空轴产生**定义好的零**（配置常量
  `EMPTY_REDUCTION_GRAD=0.0`），不产生 `nan`；其梯度是同形状的显式零数组。
  空 `sum` 为零，`(M,0)@(0,N)` 为零矩阵且余切为同形零数组。空维上有限差分
  无元素可扰动，验证器只断言空形状一致，并显式记录该“空比较”，不冒充数值
  通过。
- **非标量损失**：`backward` 对非标量输出要求显式 `grad_output`，否则抛
  `NonScalarLossError`；种子形状不符也会拒绝。
- **常数/分离张量**：`requires_grad=False` 的输入不接收梯度（保持 `None`）；
  对纯常数调用 `backward` 是无害空操作。
- **优化器即受认可的原地写**：`SGD.step` 用新数组替换数据并提升版本，旧图
  随之失效，需重新前向——这是期望行为而非缺陷。
- **数值容差**：有限差分默认 `eps=1e-3, atol=1e-5, rtol=5e-2`（见
  `config.py`），sigmoid/log/exp 等二阶导较大处中心差分误差在 1e-6 量级，
  均在容差内。
- **诊断脱敏**：小数组（≤8 元素）才显示数值预览；大数组仅给形状、有限计数、
  min/max 与截断头部；长字符串整体打码。所有记录带 `record_id` 与
  `request_id`。

## 数值验证为何独立可信

`numeric.check_gradients` 接收的“参考答案”是一个**纯 NumPy 标量函数**，
`fixtures.py` 中每个 `ref_*` 函数都不导入 `autodiff`（测试
`test_reference_is_independent_of_core` 以源码检查 + 基点数值一致双重保证）。
它对每个输入元素做中心差分扰动原始 `ndarray`，被测核心的反向规则从不参与
生成期望值，因此前后向共享同一错误不会造成假通过。

## 测试如何断言（而非“接口能调用”）

- 广播/矩阵乘/激活/归约：断言手算的**具体数值**（如
  `b.grad == [[2,2,2]]`、`sigmoid'(0)==0.25`、`mean` 分母为归约轴乘积）。
- 多分支共享：断言 `2x+3 == [7,9]`、菱形图 `4x`、权重两分支外积之和。
- 原地写入：断言抛出**具体异常类型**与消息中的版本跳变 `0->1`，且拒绝后
  所有 `.grad` 仍为 `None`。
- 验证器：注入错误梯度，断言**具体失败类别**
  `grad_value_mismatch / grad_shape_mismatch / nonfinite_analytic_gradient /
  nonfinite_reference_value`，并定位到 `worst_index`。
- HTTP：断言状态、类别、请求标识传播、422 校验、诊断记录状态集合。

## 未执行 / 不在范围内的检查（如实单列，不计为已通过）

- 未做 GPU/CUDA 验证：实现基于 NumPy，仅覆盖 CPU 内存张量。
- 未做大规模性能/并发压测：服务按单请求校验设计，没有吞吐量、延迟 SLA 或
  多客户端竞争的基准；这些**未执行**，不能视为已验证。
- 未引入假设性属性测试（如 Hypothesis 大范围随机形状搜索）；当前以固定种子
  夹具与手算用例为准。
- 未做任意阶高阶导数：仅支持一阶梯度（反向图在一次 backward 后默认释放）。
- 未实现稀疏张量、复数 dtype 与 `pow`/卷积/RNN 等更多算子。
