# 测试运行报告

## 运行编号

- **run_no**: `test-20260927T200444Z`
- **started(UTC)**: 2026-09-27T20:04:44Z
- **机器**: Linux 6.8.0-90-generic · Python 3.12.3（项目隔离虚拟环境 `.venv`）
- **依赖**: 与 `requirements-lock.txt` 完全一致（已用 `pip freeze | diff` 校验）
  - numpy 2.5.3 / scipy 1.18.1 / mpmath 1.4.1 / fastapi 0.141.1 /
    uvicorn 0.54.0 / httpx 0.28.1 / pytest 9.1.1 / pytest-cov 7.1.0
- **复现命令**:
  ```bash
  python3 -m venv .venv
  .venv/bin/pip install -r requirements-lock.txt
  .venv/bin/python -m pytest --cov=polyroots --cov=api \
      --cov-report=term-missing --junitxml=reports/junit.xml
  ```

## 总体结果

| 指标 | 结果 |
|------|------|
| 测试用例总数 | **76** |
| 通过 | **76** |
| 失败 | **0** |
| 错误 | **0** |
| 跳过 / 未执行 | **0**（pytest 意义上；限制项见下文“未覆盖/未执行”） |
| 总耗时 | ~6.1 s |
| 行覆盖率（polyroots + api） | **96.8%**（761/786），超过 80% 门槛 |

按文件：

| 文件 | 用例数 | 类别 |
|------|-------|------|
| test_validation.py        | 11 | unit |
| test_evidence.py          | 9  | unit |
| test_ordering.py          | 8  | unit |
| test_degree_boundaries.py | 5  | unit |
| test_solver.py            | 6  | integration |
| test_near_roots.py        | 6  | integration |
| test_runlog.py            | 4  | integration |
| test_api.py               | 13 | integration（HTTP 契约） |

marker 选择已验证：`-m unit` = 43 通过，`-m integration` = 33 通过（计数含
参数化展开后的用例，合计 76）。

## 开发过程中实际出现并修复的失败（保留记录）

这些是实现过程中真实遇到、随后修复的失败，非当前结果，列出以保留判断依据：

1. **mpmath 1.4 API 误用**：初版调用 `mp.polyroots(..., tol=.., method="aberth")`，
   1.4 版本无此参数 → `TypeError`。改为 `extraprec=` / `error=True` / `asc=True`。
2. **参考实现升降序因子写反**：高精度因子重构一度报 `maxfac ~ 5e1`，定位到
   把升序因子 `(x-z)` 的系数写成了降序。修正后参考自洽误差为 0 / 1e-112。
3. **x^64−1 严格重构误差 0.13**：最初以为求根错误，经独立测量确认是 float64
   逐次相乘在零系数上的**消去假象**（包络归一化 5e-15、mpmath 高精度重构 3.7e-14）。
   据此把因子证据改为“包络主判据 + strict 诊断 + 按需高精度复核”。
4. **NaN 输入的错误响应自身崩溃**：错误详情回显了 NaN，导致 JSON 序列化二次失败。
   增加 `_json_safe` 递归清洗后返回正确的 400 `non_finite_coefficient`。
5. **AUTO 内核名未回传**：报告里仍写 `auto` 而非实际内核。改为 `run_kernel`
   返回 `(输出, 实际内核名)`。
6. **停滞提前退出时 iterations_used 误报为 max_iterations**：记录实际退出轮次。

## 关键数值验证（具体结果，非“接口能调用”）

- **已知实根** `{3,-2,0.5,7}`：两内核逐根匹配误差 < 1e-9，Vieta 和/积 < 1e-9。
- **已知复根** `{1±2i, -3±1i, 2}`：匹配误差 < 1e-9；恰好 4 个根两两共轭配对，
  实根不配；配对满足显式 `conjugate_tol=1e-8`。
- **近重根**（名义间距 1e-7，多项式 (x-1)(x-1-1e-7)(x+4)(x-0.5)）：
  - companion 求根相对 mpmath(60 位) 参考的**真实误差 ~2.5e-9**，
    但相对残差低至 ~7e-17、敏感性 κ≈3.5e7 → 误差量级与 κ·ε 一致；
  - 系统把这两根标为 `near_repeated` 并给出簇间距 ~5.7e-8，警告
    “小残差不构成精度保证”。
- **Wilkinson 1..15**：相对高精度参考最大根误差 ~3.4e-6（病态，κ 最大 ~1e10），
  残差却可小至 ~1e-16；测试断言 `1e-9 < 误差 < 1e-3` 且 `残差 < 真实误差`，
  强制系统“看见”而非掩盖该量级。
- **x^64 − 1（高阶稀疏）**：64 根对单位圆真根误差 < 1e-10，Vieta < 1e-9，
  strict64 重构 ~1.3e-1，包络判据 ~2.2e-14，高精度复核 ~6.2e-14，消去比 ~6e12，
  并有显式警告。
- **x^5（全零根）**：5 根 |z|<1e-12，因子误差 < 1e-12。
- **精确重根 (x-1)²(x-2)²**：Aberth 停滞在 √eps 量级 → `status=not_converged`，
  未收敛根保留当前近似、迭代数与理由。
- **强制 8 轮预算（间距 1e-9）**：`not_converged`，1 根收敛 / 3 根未收敛，
  返回全部 4 根的当前状态，末轮校正量 7e-3 被记录。
- **可重放**：同种子两次结果位级一致；运行落盘 JSON 含规范化输入、选项、
  内核中间状态、逐根 note。
- **错误分类（HTTP 实测）**：零多项式 400/`zero_polynomial`；NaN 400/
  `non_finite_coefficient`；非数值 400/`invalid_coefficients`；非法选项 400/
  `invalid_option`；次数 300 超限 413/`resource_exhausted`；同 run_id 不同输入
  409/`run_id_conflict`；注入 LAPACK 失败 422/`computation_failed`；
  迭代耗尽 200/`not_converged`。

## 未覆盖 / 未执行项（如实列出）

- **次数 > 256（默认上限）**：设计为拒绝（413），未对超大次数做求解性能/正确性
  验证；可通过 `max_degree` 与 `POLYROOTS_MAX_DEGREE` 放开后另行评估。
- **多实例/分布式幂等**：`RunStore` 为单实例文件锁，未覆盖多进程/多机一致性；
  本任务边界为本地单实例。
- **生产级并发与限流**：未做请求速率限制与鉴权（本地合成服务，无真实账号需求）。
- **未覆盖的代码分支（约 3.2%）**：主要是 LAPACK 抛错、mpmath 参考求解失败、
  degree-0/导数为零等罕见防御路径；其中 LAPACK 失败通过 monkeypatch 注入覆盖了
  422 映射，真实 LAPACK 失败难以稳定构造。
- 覆盖率工具不统计 `tests/` 与 `examples/`；示例脚本通过真实起服 + httpx/curl
  手工端到端验证（见 README）。

## 产物

- `reports/junit.xml`：JUnit XML（76 用例机读结果）。
- `reports/coverage.json`：覆盖率机读报告。
- `logs/runs/<YYYYMMDD>/<run_id>.json`：每次求解的可重放运行记录
  （默认目录，可用 `POLYROOTS_LOG_DIR` 覆盖；`.gitignore` 已忽略运行产物）。
