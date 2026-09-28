# 测试结果

本文件记录交付时**实际运行**的测试结果、覆盖的验证面，以及失败项/
未执行项（当前为无；如下次运行出现变化，应在此如实更新）。

## 运行方式

```bash
python3 -m pytest
```

环境：Python 3.12.3 · Linux x86_64 · 依赖见 `requirements.txt`
（numpy 2.4.6 / fastapi 0.141.1 / starlette 1.7.0 / pydantic 2.13.5 /
pytest 9.1.1 / coverage 7.16.1）。

## 最近一次完整运行

- **结果：242 passed，0 failed，0 errors**
- 总覆盖率：**87%**（语句+分支，`branch = true`），核心模块均 ≥87%。
- 耗时：约 80 秒（含 SGD 收敛与后台暂停/恢复的真实等待）。

> 说明：以 `python3 -m pytest` 实际输出为准；本文件数字在最后一次
> 完整运行后更新。若复现环境的 NumPy/Python 版本不同，空数组相关的
> “不确定”计数可能不同，属正常现象（见下）。

## 测试分层与断言内容

| 文件 | 数量级 | 断言的是具体行为，而非“接口可调” |
|---|---|---|
| `tests/test_layout.py` | 33 | C/F 连续标志（空/长度1轴）、移植的零拷贝 reshape 判定（含 200 组随机布局与 NumPy `copy=False` 对拍）、区域越界、乘积溢出、-1 推断、广播 |
| `tests/test_indexing.py` | 23 | 整数归一化、负步长切片、newaxis/ellipsis、越界/零步长/布尔/越轴等失败类别 |
| `tests/test_tensor.py` | 20+ | 转置=视图、转置后reshape=复制（含具体值）、别名写入可见、物化独立、重叠偏移计数、reject/temp_copy 确定性、零步幅广播、空张量、0 维标量 |
| `tests/test_ops.py` | 25 | 逐元素/比较/matmul/归约逐值对拍 NumPy；广播错误、dtype 不匹配、整数除零、非连续操作数 |
| `tests/test_graph.py` | 14 | 未知引用/自环/环/重复 id、拓扑顺序、子图剪枝、逐节点 copied/aliases 追踪 |
| `tests/test_trainer.py` | 16 | SGD 真实收敛到已知权重、损失下降、每步新存储、状态机非法转移、后台暂停/恢复、检查点独立与恢复 |
| `tests/test_validation.py` | 25 | 预言机 AST 级独立性保证、四类契约场景、空结果不确定项单列、失败类别断言、内存专项检查 |
| `tests/test_api.py`, `tests/test_api_edges.py` | 60+ | HTTP 状态/错误分类映射、`X-Request-ID` 贯穿、完整张量/图/训练/验证链路、409 重叠写入与 temp_copy 对照 |
| `tests/test_config.py`, `tests/test_stores.py`, `tests/test_dtypes_storage.py` | 20+ | 配置校验、注册表容量/未知句柄、dtype 解析边界、存储不变量 |

## 四类合同场景的验证结论

1. **转置后 reshape**：核心与 NumPy 一致——转置是视图（strides 置换），
   转置后 C 序展平会复制，连续数组直接 reshape 是视图；3D 情形
   `allow_copy=false` 时返回 `NON_CONTIGUOUS_VIEW`。
2. **广播零步幅**：广播轴 stride=0、共享源存储，每个源元素被寻址次数
   正确（3×4 列向量广播中每个元素恰好 4 次）；广播相加产生全新存储。
3. **重叠切片**：(3,3) 滑动窗口覆于 5 个元素（9 个位置、5 个不同元素）；
   `reject` 返回 `OVERLAPPING_WRITE` 且缓冲区不变；`temp_copy` 按
   “C 序最后写入获胜”给出确定且可重复的结果，并附 NumPy 自身不安全
   重叠写入的缓冲区作对照。
4. **空张量**：值断言全部对拍通过；空结果的“复制 vs 视图”在观测上
   不可区分（NumPy 的 `owndata`/`shares_memory` 信号相互矛盾），按
   合同**单列为 uncertain**，不计为通过也不计为失败。

## 失败项 / 未执行项

- 失败项：**无**。
- 跳过项：**无**（无 `pytest.skip` / `xfail`）。
- 未纳入测试的内容：`order="K"`、fancy/布尔数组索引、matmul 批次广播
  ——这些在实现中为**显式拒绝**（各有失败类别测试或文档说明），不是
  未执行的静默缺口。

## 手工端到端验证

除自动化测试外，交付时实际执行过：

- `python3 scripts/examples.py` —— 库级七组示例，全部输出预期结论；
- 启动 `uvicorn tensorcraft.main:app` 后运行
  `scripts/example_requests.sh` —— HTTP 全链路，重叠写入实测返回
  `409 OVERLAPPING_WRITE`，`/api/v1/verify/all` 返回
  `ok: True {'scenario_failures': 0, 'memory_check_failures': 0}`。
