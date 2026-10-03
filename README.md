# tileconv — 超内存图像的分块卷积与可分离滤波服务

面向**超出内存的图像**（memmap 外存映射）的分块卷积引擎与 FastAPI 服务。
所有数据均为本地合成夹具，无外部账号与真实业务数据。

## 工程结构

```
src/tileconv/
  contract.py       数据契约：BoundaryMode / KernelSpec / ImageSpec、锚点语义、摘要
  padding.py        边界下标映射（mirror / constant / periodic）与窗口提取
  kernel.py         数值内核：valid 相关移位累加、可分离两遍、scipy 直接参考
  tiling.py         分块网格：不规则边缘块、halo 读窗、写区域不相交划分
  job.py            分块作业：逐块检查点、中断/恢复、输入与核摘要绑定
  storage.py        本地 memmap 图像库（.npy + JSON 契约）
  validation.py     与直接参考的逐像素比较、判定依据与报告
  fixtures.py       可复用合成夹具（脉冲/边缘值/斜坡/随机/台阶/常数）
  config.py         配置层（环境变量，workspace 布局）
  api.py            FastAPI 服务层
scripts/
  validate_service.py   端到端验收脚本（退出码即判定）
  memory_check.py       峰值内存检查（子进程，tracemalloc + VmHWM）
  probe_scipy_modes.py  scipy 边界模式与本工程语义的一致性探针
tests/                  pytest 套件（契约/边界/内核/分块/作业/验收/API/内存）
docs/boundary-semantics.md  边界语义完整定义
```

## 快速开始

```bash
pip install -r requirements.txt          # 固定版本依赖
python scripts/probe_scipy_modes.py      # 先验证 scipy 边界模式语义
python -m pytest                         # 全部测试（含 slow 内存检查）
python -m pytest -m "not slow"           # 跳过子进程内存检查
python scripts/validate_service.py --workspace /tmp/val   # 端到端验收
TILECONV_WORKSPACE=/tmp/ws PYTHONPATH=src uvicorn tileconv.api:app --port 8000
```

测试无需安装包：`tests/conftest.py` 会把 `src/` 加入 `sys.path`。

## 边界语义（摘要，完整定义见 docs/boundary-semantics.md）

- **卷积形式**：`out[i] = Σ_j in[i - j + a] · k[j]`，锚点 `a` 是落在输出样本上的核抽头。
- **默认锚点** `(n-1)//2`：奇数核正中；**偶数核取中点偏左**（与 scipy 默认
  `origin=0` 一致），halo 为 `before = n-1-a`、`after = a`，两侧之和恒为 `n-1`，
  不少取任何一侧上下文。
- **mirror**：全样本对称镜像，边缘样本不重复（`f(-1)=1`），等价
  `np.pad "reflect"` / scipy `"mirror"`。
- **constant**：越界样本取 `cval`。
- **periodic**：模运算回绕（`f(-1)=n-1`），等价 `np.pad "wrap"` / scipy `"wrap"`。
  越界上下文始终对**整图**做下标映射，绝不从裁剪块内部取回绕值。

## 分块与恢复保证

- 写区域是图像的**不相交完整划分**（结构性校验 + 测试逐像素计数），每像素恰好写一次。
- 每块完成后原子落盘状态；中断（`fail_after` 注入）后可恢复。
- **恢复绑定**：恢复前重算输入图像摘要（SHA-256，流式、按字节预算分块）与核摘要，
  与建作业时记录比对，不符抛 `DigestMismatchError`（HTTP 409），拒绝继续。

## 验收口径

- 分块输出与 `scipy.ndimage.convolve`（原生边界模式、奇数化居中核）**逐像素**比较，
  容差 `atol=1e-9`（大核用 1e-8，浮点累加顺序差异）；报告含判定依据、版本、摘要。
- 参考答案来源独立于被测核心：scipy 原生实现 + 测试内朴素纯 Python 循环 + 手算数组，
  不由分块引擎自身生成。
- 测试矩阵：脉冲（含偏心）、边缘值、随机、斜坡；不规则块（37×29 on 103×89）；
  大核 63×63、偶数核 64×64、核大于块（31 on 8×8）；三种边界；可分离大核（51/34）；
  中断恢复逐像素一致。
- 峰值内存：子进程跑两种尺寸（面积 4 倍），tracemalloc 峰值保持平稳且低于上限，
  报告 VmHWM；日志与报告均带 run_id、输入/核摘要与组件版本。

## 错误分类（不会把异常统一返回成功）

| category | HTTP | 含义 |
|---|---|---|
| `InvalidSpec` | 422 | 核/图像/作业规格非法（锚点越界、非有限值、未知边界模式…） |
| `NotFound` | 404 | 图像/核/作业不存在 |
| `DigestMismatch` | 409 | 恢复时输入或核摘要不符 |
| `JobStateError` | 409 | 状态不允许的转移（未完成先校验、重复运行…） |
| `InterruptInjected` | 500 | 测试注入的确定性中断（作业可恢复） |
| `ValidationFailed` | 500 | 与参考的偏差超容差 |
| `InternalError` | 500 | 未预期异常（保留原始异常类型与消息） |

## 未执行的检查（如实列出，不计为通过）

1. **真正大于物理内存的图像**：本机可用内存约 3GB，未构造 >RAM 的图像做端到端
   运行。已验证的是：memmap 外存映射 + 图像尺寸 4 倍增长时 Python 侧峰值分配
   保持平稳（`tests/test_memory.py`），引擎算法不随图像尺寸分配内存。
2. **并发多 worker 恢复**：作业状态机支持单写者恢复；多进程并发恢复同一作业
   未实现也未测试（需要文件锁，超出本次范围）。
3. **Pillow 图像 I/O 链路**：Pillow 已固定为依赖并出现在版本快照中，但当前
   夹具直接合成 ndarray，未经过 PNG/JPEG 编解码往返的测试。

已在真实 uvicorn 进程上完成冒烟（health/versions/建图/建核/建作业/运行/校验
全链路 200 且校验通过）；并发多 worker 恢复与 >RAM 端到端见上两条。
