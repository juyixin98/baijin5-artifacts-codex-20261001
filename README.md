# minigrad — 小型张量自动微分后端

基于 NumPy 的反向模式自动微分，支持广播、矩阵乘法、归约和基础激活，附带
FastAPI 服务、有限差分数值验证和结构化诊断。所有数据均为本地合成夹具，
不依赖任何生产账号或外部服务。

## 模块划分

```
src/minigrad/
├── tensor.py             # 张量类型:版本计数器、grad 语义(None vs 零)
├── graph.py              # 计算图:Node、版本检测、反向引擎、释放/保留策略
├── ops.py                # 算子:广播二元运算、matmul、归约、激活、形状变换
├── training.py           # 训练状态:线程局部 grad 模式、Parameter、SGD
├── finite_difference.py  # 数值验证:中心差分 vs 解析梯度,三态判定
├── validation_cases.py   # 验证用例注册表(纯 NumPy 独立参考实现)
├── diagnostics.py        # 结构化诊断:accepted / rejected / undecidable
├── config.py             # 独立配置(环境变量 MINIGRAD_* 覆盖)
├── errors.py             # 异常分类
└── api.py                # FastAPI 服务
tests/                    # 68 个测试 + 参考夹具
scripts/
├── generate_fixtures.py  # 用纯 NumPy 解析公式生成参考梯度夹具
└── validate.py           # 有限差分验证运行器(退出码反映结果)
docs/semantics.md         # 边界语义
CHECKS.md                 # 已执行 / 未执行的检查清单
```

## 快速开始

```bash
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -e ".[dev]"        # 或 pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests/ -q     # 单元 + 集成测试
.venv/bin/python scripts/validate.py     # 有限差分验证(8 个用例)
.venv/bin/uvicorn minigrad.api:app       # 启动服务(可选)
```

## 核心语义(验收规则对应)

1. **广播梯度对扩展轴求和**:`ops.unbroadcast` 先对前导扩展轴求和,再对
   大小为 1 的轴求和(keepdims),梯度形状恒等于操作数形状。
2. **原地修改经版本检测拒绝**:每个 Tensor 携带 `_version` 计数器,所有
   经 Tensor API 的原地写(`__setitem__`、`fill_`、`copy_`、`zero_`、
   `__iadd__` 等)都会递增。算子只*保存*其反向闭包实际读取的张量
   (mul/div/pow/log 保存输入;add/sub/sum 不保存),反向传播时逐一核对
   版本,不一致则抛 `InplaceModificationError`。
3. **无梯度与零梯度区分**:不可达叶子的 `grad` 保持 `None`;有梯度流但
   数值为零时 `grad` 是全零数组。`zero_grad()` 重置为 `None` 而非零。
4. **计算图释放/保留策略**:`backward()` 默认释放遍历到的所有节点
   (清空 saved 引用与反向闭包),再次反向抛 `GraphFreedError`;
   `retain_graph=True` 时保留并可重复反向(梯度累加)。

## API 示例

```bash
curl -X POST localhost:8000/v1/graph/execute -H 'content-type: application/json' -d '{
  "inputs": {"x": {"data": [[1,2],[3,4]], "requires_grad": true},
             "w": {"data": [[1,0],[0,1]], "requires_grad": true}},
  "program": [{"op": "matmul", "out": "y", "args": ["x", "w"]},
              {"op": "sum", "out": "loss", "args": ["y"]}],
  "loss": "loss",
  "gradients": ["x", "w"]
}'
```

每个响应携带 `request_id`(同时写入 `x-request-id` 响应头)和结构化
`diagnostics`(accepted / rejected / undecidable + 原因 + 关键状态)。
诊断只记录形状、dtype、版本号等元数据,**绝不记录张量原始值**(脱敏策略,
见 `diagnostics.tensor_state`)。原地写冲突返回 409,程序错误返回 422。

`POST /v1/gradcheck/{case}` 对注册用例执行有限差分验证,返回逐参数的
误差比和三态判定。

## 数值验证的独立性

参考梯度有两条独立来源,均不经过被测的自动微分核心:

* `validation_cases.py` 中每个用例附带纯 NumPy 书写的损失函数
  (`numpy_fn`),有限差分直接扰动它得到数值梯度;
* `tests/fixtures/reference_grads.json` 由 `scripts/generate_fixtures.py`
  用手推解析公式(纯 NumPy)预计算,测试将引擎梯度与之比对。

判定规则:误差比 `err / (atol + rtol·|数值梯度|)` ≤ 1 为 accepted;
(1, 10] 区间为 undecidable(可能是差分噪声,无法判定);> 10 或梯度
缺失/形状不符为 rejected。阈值见 `config.py`,可用环境变量覆盖。
