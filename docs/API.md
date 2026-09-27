# HTTP 接口参考

所有响应错误体统一为：

```json
{ "category": "input_error|state_conflict|resource_exhausted|computation_failed",
  "error": "RuleLanguageError", "message": "…", "details": { }, "run_id": "run-…" }
```

| 方法 | 路径 | 说明 | 成功码 |
|------|------|------|--------|
| GET | `/health` | 健康检查 | 200 |
| POST | `/api/kbs` | 建库 `{kb_id,name}` | 201 |
| GET | `/api/kbs` | 列出知识库与统计 | 200 |
| DELETE | `/api/kbs/{kb_id}` | 删除知识库 | 204 |
| PUT | `/api/kbs/{kb_id}/theory` | 整体装载理论（先内存校验+编译，再落库） | 200 |
| POST | `/api/kbs/{kb_id}/facts` | 增量追加接地事实 `{facts:[…]}` | 200 |
| POST | `/api/kbs/{kb_id}/query` | 查询 `{query:"Flies(X)"}` | 200 |
| GET | `/api/runs?limit=N` | 最近运行（含错误类别） | 200 |
| GET | `/api/runs/{run_id}` | 取回一次运行的原始请求/结果/错误 | 200 |

## 查询响应结构（关键字段）

```json
{
  "run_id": "run-…", "kb_id": "birds", "query": "Flies(polly)",
  "answers": [{
    "goal": "Flies(polly)",
    "substitution": {},
    "status": "rejected",
    "reason_code": "REJECTED_OPPOSITE_GROUNDED",
    "supporting_chains": [ … ],
    "defeated_chains":  [ { "rule_id": "r1",
                            "counter_chains": [
                              {"rule":"r2","attack_kind":"REBUTTAL",
                               "detail":"… r2 > r1", "tree": {…证明树…} } ],
                            "tree": {…证明树…} } ],
    "pending_chains":   [ … ],
    "opposing_evidence": [ … ],
    "chain_counts": { "supporting": 0, "defeated": 1, "pending": 0 }
  }],
  "kb_stats": { "facts": 3, "ground_instances": 3, "arguments": 6,
                "attack_edges": 1, "grounded_iterations": 2 }
}
```

- 变量查询在 `answers` 中枚举所有接地实例，并在 `substitution` 回带变量取值。
- 每条链的 `tree` 是递归证明树：`conclusion / rule / rule_kind /
  assumptions / premises`，叶节点 `rule_kind` 为 `fact`。

## 状态与理由码

| status | reason_code | 含义 |
|--------|-------------|------|
| accepted | ACCEPTED_GROUNDED | grounded 扩展中有支持链 |
| rejected | REJECTED_OPPOSITE_GROUNDED | 相反结论有 grounded 链 |
| rejected | REJECTED_ALL_CHAINS_ATTACKED | 支持链全被击败（含 NAF 假设被证成） |
| undecided | PENDING_MUTUAL_CONFLICT | 相反结论优先级不可比较，冲突保留 |
| undecided | PENDING_UNRESOLVED_ASSUMPTION | 依赖的 NAF 假设有未决争议 |
| no_evidence | NO_EVIDENCE_AT_ALL | 无任何支持/反对论证（未知，非假） |
