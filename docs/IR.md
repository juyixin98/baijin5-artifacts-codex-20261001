# 受限 SIMD 掩码 IR

所有内存访问与可能陷阱的运算（`/`、`%`）都带显式谓词寄存器；掩码只能由
尾掩码、比较、与/非派生，结构校验器据此证明谓词来自“有效通道”集合。

## 指令

| 指令 | 作用 | 掩码要求 |
| --- | --- | --- |
| `VMASKTAIL md, count` | `md[lane] = base+lane < count` | 产生根掩码 |
| `VCONST dst, imm` | 广播常量 | 无 |
| `VIVAR dst` | `dst[lane] = base+lane` | 无 |
| `VLOADSCALAR dst, name` | 广播标量输入/输出 | 无 |
| `VLOAD dst, a, name, m` | 屏蔽 gather：仅 `m` 有效时读 `name[a[lane]]` | 必须 |
| `VBIN dst, a, b, op, m` | 算术/逻辑；`op=/,%` 仅在 `m` 下执行 | `/、%、sel` 必须 |
| `VCMP md, a, b, cop, m` | 比较产生掩码 | 必须 |
| `VAND md, mx, my` | 掩码与 | 两者已定义 |
| `VNOT md, mx` | 掩码非 | 已定义 |
| `VSTORE idx, val, name, m` | 屏蔽散射写 | 必须 |
| `VREDUCE` | 归约在独立的升序扫描中完成 | — |

## 三条硬边界（验收对应项）

1. **被屏蔽通道不陷阱/不越界**：解释器在 `VLOAD`、`VSTORE`、`VBIN(/,%)`
   上先检查 `live && predicate`，为假的通道绝不索引内存、绝不执行除法。
2. **尾批显式有效掩码**：最后不足宽度的批次由 `VMASKTAIL` 生成有效通道
   掩码；所有派生谓词都再与该掩码合取（实现上谓词从根掩码派生，
   无效通道在每条指令上被跳过）。
3. **归约运算顺序声明**：`Program.ReductionOrder =
   LEFT_FOLD_ASCENDING_INDEX`。归约在所有 body 批次成功后，按全局索引
   `0,1,...,n-1` 严格左折叠：
   - `+=` 单位元 `0`，`acc=acc+x`
   - `*=` 单位元 `1`，`acc=acc*x`
   - `concat=`（顺序敏感原语）单位元 `0`，`acc=acc*10+x`

   顺序敏感操作（如 `1..5` 得 `12345`）用于断言顺序；若 body 先陷阱，
   则不产生任何归约贡献（对应标量语义：归约循环根本未执行到）。

## 故障前缀提交

一批内收集所有 `(全局索引 gi, 语句序 stmtID)` 事件，取字典序最小者为停机
故障；只有 `(gi',stmt') < (gi,stmt)` 的散射写被提交，其余整批丢弃。这与
“标量逐元素执行，遇到第一个陷阱停止”的可观察结果一致。
