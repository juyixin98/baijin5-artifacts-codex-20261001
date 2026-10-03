# mylnk — 自定义对象格式的强弱符号解析、节回收与运行解释器

一个完全本地、无外部业务依赖的教学型链接器：自定义 `MKOB v1` 可重定位对象格式、
类汇编前端 `mkasm`、IR 层强/弱符号解析与可达性分析（节回收）、链接/重定位、
栈式虚拟机运行，以及基于**独立手工期望表**的语义差分。

技术栈：Go 1.22 + Go Modules + 标准库。无第三方依赖（见 `go.mod`），无容器。

## 目录结构与模块职责

```
cmd/mylnk/                 CLI：asm / link / run / diff / version
cmd/genfixtures/           可复现夹具生成器：fixtures/asm/*.asm -> fixtures/gen/*.mkobj
internal/objfmt/           MKOB v1 二进制对象格式（编解码、校验）
internal/frontend/         语言前端：mkasm 汇编器（两遍、符号/重定位生成）与 ISA 定义
internal/ir/               IR 变换：全局强/弱符号解析、GC 根与可达闭包、未定义诊断
internal/linker/           链接：段布局、地址分配、abs32/rel32 重定位、镜像与不变量审计
internal/runtime/          运行解释：栈式 VM、独立调用栈、步骤上限、显式运行时错误分类
internal/semdiff/          语义差分：解析独立期望表，断言结果/错误类别/保留与回收集合
internal/config/           独立配置层（key=value 配置文件）
internal/logx/             可关联运行身份的步骤日志（版本、进度、判定依据）
internal/pipeline/         CLI 装配：frontend -> ir -> linker -> runtime
internal/e2e/              集成测试：直接消费磁盘上的夹具与期望表
fixtures/asm/              可复用手工夹具（.asm 源，18 个对象，9 个场景）
fixtures/gen/              生成的固定版本 .mkobj 对象
fixtures/expectations.txt  独立维护的手工对照期望表（参考答案）
fixtures/example.config    配置文件示例
verify.sh / verify.ps1     一键原生验证脚本（Shell / PowerShell）
```

## 对象格式 MKOB v1（边界与持久化规则）

- 文件头：魔数 `"MKOB"` + 小端 `u16` 版本（当前 1）+ 对象名。
- 节表：名称、`keep` 标记（无条件 GC 根）、原始字节数据。
- 符号表：名称、绑定（`local=0 / strong=1 / weak=2`）、偏移、`def`/`export`
  标志、定义所在节索引 `SecIdx`（未定义为 `0xFFFF`）。
- 重定位表按节存放：`Off`（站内偏移）、`SymIdx`（对象内符号下标）、`Addend`、
  `Kind`（`abs32=1`: `*site = S + A`；`rel32=2`: `*site = S + A - P`）。
- 编解码均做边界校验：坏魔数/版本、截断、重定位越界、符号索引悬空、
  非法绑定/重定位种类、尾余字节都返回显式错误，绝不静默成功。

## 固定的强弱符号选择规则

按输入对象顺序确定地处理所有定义：

1. 强定义 + 强定义 => `multiple-strong-definition` 错误（报告两处来源）。
2. 强定义 vs 弱定义 => 强定义获胜；若先见弱后见强，强覆盖弱，
   仅含失败弱定义的节在不可达时被回收。
3. 弱定义 + 弱定义 => 不报错，**第一个**弱定义获胜。
4. 未解析引用：强引用在**可达节**内 => `undefined-symbol` 诊断；
   仅被已回收节引用的未定义符号不诊断；弱未定义（`.weakext`/弱引用）允许为空，
   重定位写 0，运行期经空地址调用/跳转归类为 `runtime` 错误。

## 节回收（GC）与“无悬空重定位”语义

根集合（间接根显式参与）：入口符号所在节、所有 `keep` 节、所有**导出符号**所在节。
沿节内重定位传播闭包：`abs32/rel32` 的目标节成为可达；弱未定义目标不产生边。
回收发生在未定义诊断与重定位之前，因此：

- 存活节引用的每个符号都必须已解析（强未定义在此报错）；
- 链接器只对存活节应用重定位，且审计任何“存活节引用已回收节”的情况为
  `internal-invariant`（单测通过人为破坏可达集合复现，保证不会写出悬空重定位）；
- 镜像内存只包含存活节，回收节不占地址、不留下任何重定位站点。

## 运行解释器（栈式 VM）

数据栈与调用栈分离。指令：`pushi/add/sub/mul/call(rel32)/ret/load(rel32)/
callind/jz(rel32,窥视不弹栈)/pop/halt`。

- 镜像携带 `Base`，VM 以相对基址索引内存。
- 默认步骤上限 1,000,000（可配置）：超限、越界 PC/访存、数据栈下溢、
  空地址间接调用、非法操作码均返回 `KindRuntime` 分类错误，不统一成成功。
- `ret` 用于函数返回（压回返回值并回到调用点）；顶层结果用 `halt` 取出。

## 语义差分与“参考答案独立性”

`fixtures/expectations.txt` 是**人工编写**的 `spec = 1` 期望表，逐例给出
对象顺序、入口、期望错误类别（或成功）、VM 返回值、保留/回收节集合、
未定义符号名集合。它不调用被测链接器来生成答案；`internal/e2e` 同时以
“实时汇编 .asm”和“读取已生成 .mkobj”两种加载方式执行该表。

失败被细分为：`fixture-load / error-category / undefined-set / kept-set /
dropped-set / return-value / runtime / unexpected-error`，每例日志带
运行身份（`MYLNK_RUN_ID`）、版本号、编号步骤与 PASS/FAIL 判定依据。

场景覆盖：弱默认实现、强覆盖弱（弱节回收）、跨双对象循环引用
（iseven/isodd）、未定义符号诊断、死节回收且不诊断、导出+keep 间接根、
双重强定义冲突、四对象传递链、弱未定义空目标运行时错误。

## 运行与验证（本机原生，无容器）

```bash
# 一键：vet、race 测试、夹具再生成、两种模式语义差分、手工 spot check
./verify.sh            # Linux/macOS
pwsh ./verify.ps1      # Windows/PowerShell（或在 PowerShell 中 ./verify.ps1）

# 常用命令
go test ./... -race
go run ./cmd/genfixtures .
go run ./cmd/mylnk asm  fixtures/asm/ws_main.asm /tmp/ws_main.mkobj
go run ./cmd/mylnk diff -from-asm -asmdir fixtures/asm -spec fixtures/expectations.txt
go run ./cmd/mylnk run  -entry main fixtures/gen/ws_main.mkobj \
                        fixtures/gen/ws_strong.mkobj fixtures/gen/ws_weak.mkobj
go run ./cmd/mylnk link -map -entry main fixtures/gen/ws_main.mkobj \
                        fixtures/gen/ws_weak.mkobj fixtures/gen/ws_strong.mkobj
go run ./cmd/mylnk run  -config fixtures/example.config
go run ./cmd/mylnk version
```

## 已实现的检查

- 对象格式往返、坏魔数/版本/截断/悬空符号索引/越界重定位（objfmt 单测）。
- 前端：跨段重定位生成、段内 rel32 汇编期回填、weakext 冲突、词法错误类别。
- 强弱选择四种组合、GC 根与传递闭包、死节静默回收、导出/keep 根、
  强未定义诊断、弱未定义放行（ir 单测）。
- 链接：abs32/rel32 应用、rel32 溢出（合成布局）、悬空重定位不变量、
  非默认镜像基址（linker/runtime 单测）。
- VM：正常停机、空入口、步骤上限、非法操作码分类。
- 语义差分解析错误与 9 个端到端场景；期望表磁盘集成（asm 与 mkobj 双模式）。

## 明确未实现 / 未执行的检查（不计为通过）

- 未实现共享库/动态链接、重定位到外部绝对地址的运行时加载器；弱符号仅解析为 0。
- 未实现通用归档库（`.a`）、链接脚本、C ABI、调试信息；ISA 仅为本项目的小指令集。
- 未实现跨字节序/32 位主机移植（固定小端、地址宽度以 uint32 建模）。
- `verify.ps1` 未在本机 Linux 环境实际执行（无 PowerShell 运行时要求）；
  其命令与 `verify.sh` 逐条对应，PowerShell 下的执行结果不宣称已通过。
- 网络仅限 Go 工具链依赖；本项目 `go.mod` 无任何第三方模块，
  因此验证过程中实际未发生外部依赖下载。
