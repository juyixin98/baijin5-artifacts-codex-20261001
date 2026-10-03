# tiled-filter-service

超内存图像的分块卷积与可分离滤波服务。技术栈：Python 3.12、FastAPI、NumPy、
SciPy、Pillow。所有输入均为本地合成夹具（`app/fixtures.py`），不依赖任何生产
账号或真实业务数据。

## 工程结构

```
app/
  contract.py       图像数据契约：BoundaryMode / KernelSpec / SeparableKernelSpec /
                    ImageDocument、锚点与 halo 定义、内容摘要
  kernels.py        数值内核：边界索引映射、区域提取、稠密/可分离窗口滤波
  tiling.py         分块规划与"精确覆盖、无重叠"证明
  jobs.py           分块作业：清单、检查点、中断恢复（绑定输入/核摘要）、报告
  reference.py      独立参考实现（scipy.ndimage + 朴素循环），仅供验证/测试
  fixtures.py       合成图像与核夹具、Pillow PNG 读写
  api.py            FastAPI 验证接口
  config.py         配置层（环境变量 TFS_* 可覆盖）
  memory.py         峰值 RSS 探测
  memprobe.py       子进程内存基准入口（python -m app.memprobe）
scripts/validate.py 端到端验证脚本（矩阵用例，非零退出码表示失败）
tests/              独立测试层
```

## 数值契约

### 锚点（含偶数核）

滤波算子为相关形式（与 `scipy.ndimage.correlate` 一致）：

```
out[y, x] = sum_{i,j} w[i, j] * src[y + (i - ay), x + (j - ax)]
```

核元素 `(ay, ax)` 正对输出像素。核覆盖的偏移区间是
`[-anchor, k-1-anchor]`。**偶数核锚点显式**：默认 `anchor = k//2`，例如
`k=4, anchor=2` 覆盖偏移 `-2,-1,0,+1` —— 锚点侧（左/上）取两个样本的上下文，
另一侧取一个。halo 由偏移区间推导，从不假设对称，因此偶数核不会在锚点侧
少取一行/列上下文（`tests/test_tiling.py::test_even_kernel_halo_not_short_on_anchor_side`
用 1 像素宽块专门回归这一点）。

### halo 宽度

`halo = (anchor_y, ky-1-anchor_y, anchor_x, kx-1-anchor_x)`，即
(上, 下, 左, 右)。可分离核按两个 1D 向量的长度与锚点分别推导，与
`outer(col, row)` 的稠密 halo 完全一致（有测试断言）。

### 边界语义

| 模式 | 语义 | 等价物 |
|------|------|--------|
| `mirror` | 半样本对称反射，**边缘样本重复**：`... c b a \| a b c \| c b a ...` | `np.pad(mode="symmetric")`、`scipy.ndimage mode="reflect"` |
| `constant` | 越界样本取常数 `cval` | `np.pad(mode="constant")`、`scipy.ndimage mode="constant"` |
| `periodic` | 图像周期回绕 | `np.pad(mode="wrap")`、`scipy.ndimage mode="wrap"` |

边界扩展通过**索引映射**（`boundary_indices`）按需物化，halo 大于图像尺寸
时仍然正确（周期模式回绕整图，而非只回绕局部裁剪块——有专门测试）。

### 分块写入规则

输出被划分为互不重叠的块；每块只写自己的输出窗口，halo 只读不写。
`validate_tiling` 用面积恒等 + 区间扫描线证明"精确覆盖、无重叠、不出界"；
测试另用逐像素写入计数断言每个有效像素恰好被写一次。

### 中断恢复

`create` 把输入摘要与核摘要冻结进 `manifest.json`；每次 `execute`/`resume`
都从当前 spec 重新物化输入与核并重算摘要，不匹配即抛
`DigestMismatchError`（API 映射为 409 `digest_mismatch`），拒绝在不同输入或
核上继续。每处理完一块即 flush 输出 memmap 并原子替换检查点文件，任意时刻
中断都留下可恢复的一致状态。输出文件预分配并填充 NaN，未写区域可被检测，
永远不会被静默当作 0。

## API

```
GET  /health               版本信息（python/numpy/scipy）
POST /jobs                 创建作业（图像描述符 + 核 + 边界 + 块尺寸）
GET  /jobs/{id}            状态与进度
POST /jobs/{id}/run        执行；{"max_tiles": N} 可模拟中断
POST /jobs/{id}/resume     恢复（摘要绑定）
GET  /jobs/{id}/report     完成报告（峰值 RSS、输出摘要、版本）
POST /validate             同一 spec 跑分块 + 直接 + scipy 参考，返回逐像素
                           最大误差、容差与判定依据；失败返回 "fail" 而非成功
```

错误模型：`unknown_job`→404、`digest_mismatch`→409、`invalid_spec`→422、
`job_state`→409、未预期异常→500 `internal`。异常或未知状态不会统一返回成功。

启动：`uvicorn app.api:app --port 8000`

## 测试与验证

```bash
pip install -r requirements.txt
python -m pytest tests/                 # 全部测试（含子进程峰值内存测试）
python -m pytest tests/ -m "not slow"   # 跳过内存基准
python scripts/validate.py              # 端到端矩阵验证，失败时退出码非零
python scripts/validate.py --json       # JSON 行输出
```

测试要点：

- **参考答案独立**：`app/reference.py` 提供 scipy.ndimage 参考与逐像素朴素
  循环参考，测试另含手写字面量期望值（边界索引序列、脉冲响应、常数边界
  数值），不全部由被测核心生成。
- **用例**：脉冲、阶跃边缘、带标记边界、随机噪声 × 三种边界 ×
  3x3 / 4x4（偶数）/ 31x31（大核）/ 非对称锚点 × 不规则块（103x97 @ 32x32、
  33x17 @ 33x1 等），与整图直接卷积逐像素比较。
- **恢复**：中断-恢复输出与未中断运行逐位一致；篡改输入或核后恢复抛出
  `digest_mismatch`。
- **峰值内存**：`tests/test_memory.py` 在独立子进程中分别运行分块与直接
  模式（2400x1800 float64、25x25 核），断言分块峰值 RSS 显著低于直接模式，
  且两种模式校验和一致。
- **日志**：每次运行写 `logs/run-<job_id>.log`（JSON 行），携带 run_id、
  输入/核摘要、版本、逐块进度与耗时，可关联到具体输入与运行身份。

## 无法执行的检查（单列，未写成已通过）

- **真正超出内存的图像（数十 GB 级）端到端运行**：本机内存可容纳
  2400x1800 的对比基准，更大规模未实际执行；内存有界性由子进程峰值
  RSS 对比间接验证。
- **进程崩溃（SIGKILL）后的恢复**：测试覆盖的是受控中断
  （`max_tiles` 提前停止），未模拟断电/OOM 杀进程；检查点原子写
  （tmp + rename）按设计支持该场景，但未实测。
- **并发同一作业的互斥**：当前未实现作业级文件锁，多进程同时恢复同一
  作业的行为未定义、未测试。
- **macOS/Windows 上的峰值内存**：`app/memory.py` 有 getrusage 回退，
  但内存对比测试仅在 Linux（/proc VmHWM）上验证过。
