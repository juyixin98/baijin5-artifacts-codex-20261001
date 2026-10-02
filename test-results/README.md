# 测试运行记录与问题复盘

本目录保留验证过程中**真实出现过的失败**、最终结果与未执行项，供重放。

## 最终结果（2026-10-02，UTC）

| 项目 | 结果 | 证据 |
|---|---|---|
| 单元测试（lib） | 33 passed / 0 failed | `06-cargo-test-final.log` |
| 核心契约集成测试 | 13 passed / 0 failed | 同上（`core_contract`） |
| HTTP 契约集成测试 | 10 passed / 0 failed | 同上（`http_contract`） |
| 合计 | **56 passed / 0 failed** | |
| `cargo clippy --all-targets` | 0 warning / 0 error | |
| 行覆盖率（含 0% 的 main 入口） | **86.69%** | `05-coverage-final.log` |
| release 构建 | 成功 | `cargo build --release` |
| 真实 HTTP 冒烟 | 通过 | `02-server-stdout.log`、`03-example-requests.log` |

> 注：覆盖率统计把 `src/main.rs`（仅进程入口/信号装配）计入分母，其为 0%；
> 业务模块行覆盖率均在 78% 以上，翻译主路径 `store/translate.rs` 91.5%。

## 开发过程中出现过的失败（均已闭环）

1. **夹具期望值错误 — VPN 切分**（`config` 单测）
   初始夹具用 `0xFFC0_0FFC` 断言 L2=1023，但该地址 L2 实为 0（1023 对应
   `0xFFFF_FFFC`）。被测切分函数正确，修正夹具后通过。
2. **夹具期望值错误 — 大页失效范围**（`tlb` 单测）
   夹具把第二张“区内小页”放在 `0x40_1000`，它已越过 4 MiB 边界（属第二个
   大页区）。修正为 `0x2000` 后，失效计数 3 成立。
3. **夹具期望值错误 — 缺页级别**（`core_contract`）
   `unmapped_va_in_other_process...` 期望 L2 缺页，但 p2 的 L1 槽本身为空，
   正确分类是 **L1 not-present**。修正断言。
4. **真实缺陷 — run_id 与事件不一致**（`http_contract::run_id_is_recorded...`）
   翻译结果负载里的 run_id 与写入事件环的 run_id 是两次独立生成，导致按
   run_id 检索 404。修复：新增 `EventLog::record_with_id`，翻译事件复用结果
   中的 run_id。修复后 HTTP 测试通过。
5. **真实缺陷 — run_id 日期错乱**（冒烟时肉眼发现，如 `49053171014T...`）
   `civil_from_unix` 两处错误：(a) `if/else` 括号错误使 era 除法只作用于负
   分支；(b) 该公历算法输入是“天”，代码直接传入了秒。修复为先用
   `div_euclid(86400)` 取天、`rem_euclid(86400)` 取日内秒，并新增固定日期
   回归测试（1970-01-01、2000-02-29 闰年、2026-10-02，时间戳经 `date -u` 校验）。
6. **语法 — 多余括号**：`Some(json!({}))))` 导致 http 测试编译失败，已修正。
7. **环境问题（非代码缺陷）**：本机 18080 端口被无关进程 `h2d` 占用，
   冒烟改用 18091。

## 未执行 / 有意不覆盖的项

- **`src/main.rs` 信号与端口装配路径**：未做进程级信号测试（覆盖率 0% 的唯一
  模块）。其逻辑仅为 env 读取、TcpListener 绑定、SIGTERM/Ctrl-C 透传；已做
  真实启动 + HTTP 冒烟替代。
- **`FaultKind::MisalignedNextTable`**：在 Sv32 编码下，次表物理地址来自 PPN
  （天然 4 KiB 对齐），该分支在不手工破坏编码时**不可达**，保留为防御性检查，
  不构造伪输入去覆盖。
- **并发压测**：服务用 `Mutex<Lab>` 串行化所有状态变更（教学机本就单核顺序
  语义），未做多客户端竞争压测。
- **真实内核交互**：按要求**禁止**，全部为进程内合成状态，无 `/dev/mem`、
  mmap 特权页表、系统账号或外部服务。
- 快照文件是人类可读 JSON；事件环不进快照（进程内观察设施）。

## 如何复现

```bash
cargo test
cargo llvm-cov --summary-only
cargo build --release
MMU_LISTEN=127.0.0.1:18091 ./target/release/mmu-lab &
MMU_BASE=http://127.0.0.1:18091 bash examples/requests.sh
```
