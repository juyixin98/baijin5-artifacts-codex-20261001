# 边界语义

本文档列出 minigrad 有意为之的边界行为。这些不是缺陷,而是明确的设计
决策;依赖这些行为前请阅读对应条目。

## 数据类型

* 所有 Tensor 数据强制转换为 `float64`(可用 `MINIGRAD_DTYPE` 覆盖)。
  选择 float64 是为了让中心有限差分验证在默认容差内有意义。
* 不支持复数;布尔/整型输入会被提升为浮点。

## 原地修改的追踪边界

* 版本计数器只跟踪**经 Tensor API** 的修改:`__setitem__`、`fill_`、
  `copy_`、`zero_`、`__iadd__`、`__isub__`、`__imul__`、`__itruediv__`,
  以及 `SGD.step()`(显式 bump)。
* **直接改写 `.data` 返回的 ndarray 不在追踪范围内**(与 PyTorch 的
  `.data` 同类警告)。如此修改后反向传播不会报错,但梯度可能错误。
  需要被追踪的修改必须走 Tensor API;`Tensor.numpy()` 返回拷贝,改写
  它不影响张量。
* 版本检测只作用于反向闭包**实际保存**的张量:mul/div/pow/log/matmul
  保存输入;add/sub/neg/sum/mean/max/reshape/transpose/broadcast_to/
  exp/sigmoid/tanh/relu 不保存输入(其反向只依赖上游梯度或前向时就地
  计算的掩码/输出)。因此 `y = x + 1` 之后原地改 `x` 再反向是**允许**
  的——这不是漏检,而是该图对 `x` 的值没有依赖。

## 计算图生命周期

* `backward(retain_graph=False)`(默认)释放遍历子图中所有节点的
  saved 引用与反向闭包;再次经过任一已释放节点抛 `GraphFreedError`。
  未被遍历的分支不受影响。
* `retain_graph=True` 保留全部状态,可重复反向,梯度在叶子上累加。
* 只有**叶子张量**(非算子输出的张量)且 `requires_grad=True` 才会
  收到 `.grad`;中间张量的梯度在引擎内部累加后不对外暴露。

## 梯度语义

* `grad is None` = 无梯度(未参与损失或尚未反向);全零数组 = 真实
  计算出的零梯度。两者永不混同。
* `no_grad()` 内不记录任何节点(线程局部,可嵌套,异常后正确恢复);
  对不记录图的张量调用 `backward()` 抛 `BackwardError`。
* 非标量输出调用 `backward()` 必须显式传入梯度,否则抛
  `NonScalarBackwardError`。

## 空维度(形状含 0)

* `sum` 空张量 → 0.0,反向得到形状正确的空梯度。
* `mean` 空轴 → 遵循 NumPy 语义产生 NaN(并发出 RuntimeWarning),
  反向传播 NaN。不视为错误,但 gradcheck 遇到非有限值会判
  undecidable。
* `max` 空轴 → 与 NumPy 一致抛 `ValueError`。
* 空内维 matmul(如 (2,0)@(0,3))→ 零矩阵,梯度形状正确。

## 数值边界

* `pow` 的指数梯度含 `ln(a)`:底数 ≤ 0 时该分量为 NaN(仅当指数
  本身 `requires_grad` 时才会进入叶子梯度)。
* `max` 并列最大值之间均分梯度。
* `relu` 在 0 处次梯度取 0。
* `detach()` 共享底层存储但版本计数独立;修改 detach 出的张量会
  影响原张量的数据(不触发原张量的版本检测)。

## NumPy 互操作

* Tensor 不实现 `__array__`,不会隐式转换为 ndarray(防止静默
  detach)。显式转换请用 `Tensor.numpy()`(拷贝)。
