# 边界语义（Boundary Semantics）

本文档定义分块卷积引擎在图像边界外的取样语义。所有定义都有对应的
手工计算测试（`tests/test_padding.py`、`tests/test_kernel.py`），并与
`np.pad` / `scipy.ndimage` 的原生实现交叉验证。

## 1. 卷积与锚点（anchor）

引擎实现**真卷积**（不是相关）。长度 `n`、锚点 `a` 的一维核 `k` 定义为：

```
out[i] = sum_j in[i - j + a] * k[j]
```

即锚点是"落在输出样本上"的那个核系数；脉冲响应是核本身按锚点原样放置。
二维时两个轴各自有锚点 `(ar, ac)`。

**默认锚点**：`a = (n - 1) // 2`

- 奇数核：正中样本（`n // 2`）；
- 偶数核：中点偏左样本（`n // 2 - 1`），与 `scipy.ndimage` 默认
  （`origin=0`）的放置一致。

**偶数核锚点必须显式可考**：`KernelSpec.dense(weights, anchor=(ar, ac))`
与 `KernelSpec.separable(..., col_anchor=, row_anchor=)` 允许显式指定；
缺省值有明确文档定义，不存在"隐式少取一侧"的情况。

## 2. Halo（上下文宽度）

卷积形式下，输出样本 `i` 需要输入区间 `[i - (n-1-a), i + a]`，因此：

```
before = n - 1 - a     after = a        before + after == n - 1
```

二维核 `(kh, kw)`、锚点 `(ar, ac)` 的 halo 为：

```
top    = kh - 1 - ar     bottom = ar
left   = kw - 1 - ac     right  = ac
```

例：4×2 核、默认锚点 `(1, 0)` → `(top, bottom, left, right) = (2, 1, 1, 0)`。
偶数核因此在上/左方向多保留一个样本的上下文，两侧之和恒等于 `n - 1`，
任何一侧都不会被少取。

分块读取窗口 = 写区域四边各外扩对应 halo；窗口可能越界，越界部分按下
节的边界模式解析。halo 大于图像尺寸时也成立（镜像会多次折叠，周期会
多次回绕）。

## 3. 三种边界模式

设轴长为 `n`，越界下标 `i` 的取值规则：

### mirror（镜像，全样本对称）

周期 `2n - 2` 的反射，**边缘样本不重复**：

```
f(i) = m,            m = i mod (2n-2), m < n
f(i) = 2n - 2 - m,   否则
```

例（`in = [a b c d]`）：`... c b | a b c d | c b ...`，即 `f(-1) = 1 (b)`。
等价于 `np.pad(..., "reflect")` 与 `scipy.ndimage` 的 `mode="mirror"`。
`n == 1` 时所有下标映射到 0。

### constant（常数）

越界样本恒为 `cval`（作业参数，默认 0.0）。等价于
`np.pad(..., "constant")` 与 scipy 的 `mode="constant"`。

### periodic（周期）

模运算回绕：`f(i) = i mod n`，即 `f(-1) = n - 1`。
等价于 `np.pad(..., "wrap")` 与 scipy 的 `mode="wrap"`。

**实现要点**：越界样本通过对**整图**做下标映射取得
（`padding.extract_window`），而不是对裁剪后的块做 `np.pad`——后者在
periodic 模式下会错误地从块的内部取"回绕"值，而不是从图像远端边缘取。

## 4. 分块写入保证

- 写区域把图像划分成**互不相交且完整覆盖**的网格
  （`TileGrid._check_partition` 在构建时结构性验证；测试用计数数组逐
  像素验证"每像素恰好写一次"）。
- 每块只写自己的有效区域；halo 只用于读。
- 输出恒为 float64（内部累加也是 float64）。

## 5. 中断恢复与摘要绑定

- 每完成一块即原子落盘作业状态（临时文件 + `os.replace`）。
- 恢复前重新计算**输入图像摘要**（SHA-256：shape|dtype|原始字节，分块
  流式计算，memmap 不会整体入内存）与**核摘要**（SHA-256：种类|形状|
  锚点|权重字节），与建作业时记录的摘要比对；任一不符抛出
  `DigestMismatchError`（HTTP 409），拒绝恢复。
- `fail_after=N` 是确定性中断注入钩子，仅用于测试恢复路径。

## 6. 参考实现与判定依据

验收比较的参考是 `scipy.ndimage.convolve/convolve1d`（原生边界模式，
核先零填充为奇数尺寸、锚点居中，从而 `origin=0` 放置精确）。被测引擎
则是"下标映射取窗口 + 移位累加有效卷积"，两条路径不共享边界处理代码。
判定依据：`|actual - expected| <= atol + rtol*|expected|` 逐像素成立；
报告记录容差、最大绝对差、失配像素数、内容摘要与组件版本。
