# 受限语义的形式化

本文给出引擎实现的精确定义。内核（`engine.py` + `grounding.py`）是该定义的直接翻译，
`tests/unit/oracle.py` 用另一种独立算法（Dung 接地扩展）交叉验证同一语义。

## 1. 语法

- 项（literal）：`L = P(a₁,…,aₙ)` 或 `-P(a₁,…,aₙ)`，`-` 为经典否定。
  变量大写开头、常量小写开头、谓词小写。
- 规则：`r : L₁ ∧ … ∧ Lₘ ⇒ L₀`，种类 `kind(r) ∈ {strict, default}`。
  `m = 0` 的严格规则即事实。
- 优先断言：`r > s`，仅允许两条默认规则之间。
- 良构条件：
  1. 规则 id 唯一；
  2. **范围受限**：头中每个变量都出现在某个正正文文字中；负文字中的变量也须被正文字约束；
  3. 优先关系 `>` 是有向无环图（传递闭包由 `language.priority_reachable` 计算）。

## 2. 基化（grounding）

论域 `D` = 证据文字与规则中出现的所有常量之集合。每条规则按变量到 `D` 的所有绑定实例化；
实例数受 `max_ground_rules` 限制，超出报 `resource_exhausted`。
基化规则集记为 `R_g`，证据与理论内事实合记为事实集 `F`。

## 3. 三层证明标记

对每个基文字 `L` 定义：

### 3.1 definite（严格结论）
`definite` 是从 `F` 出发、只使用严格规则的前向闭包（最小不动点）。
若同时有 `definite(L)` 与 `definite(-L)`，理论**不连贯**，求值以 `state_conflict` 拒绝。

### 3.2 编译为正规逻辑程序
对每条基化默认/严格规则 `g`（头为 `L`）引入两类带标记原子：
`sup:L`（supported）与 `prov:L`（provable），以及每规则的击败原子
`beat_sup:g`、`beat_prov:g`。记规则 `g` 的体为 `B(g)`，其对立头为 `-L`。

**supported 子句**

```
sup:L  :-  L ∈ definite.
sup:L  :-  sup:B₁, …, sup:Bₘ                                  （g 为严格规则）
sup:L  :-  sup:B₁, …, sup:Bₘ,  not beat_sup:g                （g 为默认规则）
beat_sup:g  :-  sup:C₁, …, sup:Cₖ                             （对每条严格更强的反方规则 h）
```

即默认链只被**严格更强**（显式 `h > g`，或 `h` 是严格规则/事实）的 supported 反方链击败。

**provable 子句（歧义传播的关键）**

```
prov:L :-  L ∈ definite.
prov:L :-  prov:B₁, …, prov:Bₘ                                （g 为严格规则）
prov:L :-  prov:B₁, …, prov:Bₘ,  not beat_prov:g             （g 为默认规则）
beat_prov:g  :-  sup:C₁, …, sup:Cₖ
               （对每条反方规则 h，只要 g 不严格强于 h；注意此处读 sup:，不是 prov:）
```

当 `−L ∈ definite` 时，`prov:L` 不给任何子句（严格反面恒胜）。

> 决定性的一点：`beat_prov:g` 读反方体的 **supported** 标记。因此只要反方规则有一条
> *有支持* 的链且 `g` 不支配它，`g` 就不能用于证明——支持层的争议向结论上方**传播**。

`sup:*` 与 `prov:*` 在**同一个**基正规程序中，对其取一次
[良基模型（well-founded model）](https://en.wikipedia.org/wiki/Well-founded_semantics)：
真原子集 `T`、假原子集 `F`、其余未定义。被引用但无子句无事实的原子属于全集并被置入
最大无根基集，故为假。良基模型是唯一的，且其不动点仅依赖集合运算，故与规则、事实顺序无关。

## 4. 查询状态

```
provable(L) ⇔ 「prov:L」∈ T
supported(L) ⇔ 「sup:L」∈ T

status(L) = proved    若 definite(L) 或 provable(L)
          = refuted   若 provable(-L)
          = conflict  若 ¬provable(L) ∧ ¬provable(-L)
                       ∧ supported(L) ∧ supported(-L)
          = unknown   否则（含完全无证据）
```

注意 `refuted` 需要**明确的反面证明**；`-L` 仅仅未知时 `L` 是 `unknown` 而不是 `refuted`。

## 5. 小空间穷举核验

证据仅 `p(t)`，候选默认规则 `R1: p(X)⇒q(X)`、`R2: p(X)⇒-q(X)`，
对“是否含规则 × 优先方向”的整个有限空间枚举（由内核实际运行生成）：

| 规则 | 优先级 | `q(t)` | `-q(t)` |
|------|--------|--------|---------|
| 无 R1 无 R2 | — | unknown | unknown |
| 仅 R2 | — | refuted | proved |
| 仅 R1 | — | proved | refuted |
| R1 与 R2 | 无 | **conflict** | **conflict** |
| R1 与 R2 | R1>R2 | proved | refuted |
| R1 与 R2 | R2>R1 | refuted | proved |

- 无任何规则时双方 `unknown`：**缺少证据不是反面事实**。
- 两条对立默认且无优先级时双方 `conflict`：**保留冲突，不取先出现者**。
- 加入任一方向的优先级后，结论严格沿优先方向裁决；交换规则书写顺序结果不变。

复现命令：

```bash
PYTHONPATH=src python3 - <<'PY'
from itertools import product
from defeasible.language import Theory, Term
from defeasible.engine import Engine
for r1,r2 in product([0,1],[0,1]):
    ...   # 见 docs 表格；完整脚本可照第 3 节元程序构造
PY
```

## 6. 论证链分类

对查询 `L`，枚举所有以事实为叶、以 `L` 为根的有限证明树（正推导环用“分支上已出现结论”切断，
数量受 `max_chains` 限制）。对每条树 `A` 顶规则 `g`：

1. 某子链被击败 → `defeat`；
2. 存在 *supported 可适用* 的反方树 `B` 且 `B` 严格强于 `g` → `defeat`，记录 `B` 为击败者；
3. `g` 全体可证且 `L` 可证 → `support`；
4. 某子链悬置 → `pending`；
5. 存在 supported/provable 可适用但与 `g` **不可比**的反方树 → `pending`；
6. 与反方相互攻击、双方 supported 在良基模型中均不成立 → `pending`；
7. 其余有限链 → `support`。

强弱比较返回三值 `{g 强, B 强, 不可比}`；不可比即保留歧义。这与第 3 节元程序的
真/假/未定义三值结果一致，链只是同一结论的**可读解释**。

## 7. 与独立预言器的关系

`tests/unit/oracle.py` 把同一理论看作 Dung 抽象论证框架：

- 论证 = 有限证明树（同样切断正环）；
- `A` 攻击 `B` 当二者结论相反且 `B` 不支配 `A`；
- 取接地扩展（grounded extension）的 in/out/undecided 标注。

25 个随机小理论上，预言器的 proved/refuted/conflict/unknown 与内核逐查询一致。
两套实现共享的只有规则数据结构，不含推理代码，因此一致性不是“自证”。
