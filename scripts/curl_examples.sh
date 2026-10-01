#!/usr/bin/env bash
# 服务调用示例：正常路径 + 四类失败。
# 用法: scripts/curl_examples.sh [base_url]
set -u
BASE="${1:-http://127.0.0.1:8080}"
j() { python3 -m json.tool; }

echo "== 0. 健康检查 =="
curl -s "$BASE/health" | j

echo "== 1. EXCEPT ALL（小内存预算，真实溢写） =="
curl -s -X POST "$BASE/v1/query" -H 'Content-Type: application/json' -d '{
  "op": "EXCEPT",
  "qualifier": "all",
  "mode": "auto",
  "schema": "id:bigint,label:text",
  "left":  {"rows": [[1,"a"],[1,"a"],[2,null],[3,"12"]], "batch_rows": 2},
  "right": {"rows": [[1,"a"],[2,null],[4,"x"]]},
  "limits": {"memory_bytes": 64, "partition_fanout": 2}
}' | j

echo "== 2. 输入错误（400）：未知算子 =="
curl -s -o /tmp/r.json -w "HTTP %{http_code}\n" -X POST "$BASE/v1/query" \
  -H 'Content-Type: application/json' -d '{"op":"OUTER","schema":"a:bigint","left":{"rows":[]},"right":{"rows":[]}}'
cat /tmp/r.json | j

echo "== 3. 资源耗尽（422）：in_memory 超出预算 =="
curl -s -o /tmp/r.json -w "HTTP %{http_code}\n" -X POST "$BASE/v1/query" \
  -H 'Content-Type: application/json' -d '{
    "op":"UNION","qualifier":"distinct","mode":"in_memory",
    "schema":"v:text",
    "left":{"rows":[["v1"],["v2"],["v3"],["v4"],["v5"]]},
    "right":{"rows":[]},
    "limits":{"memory_bytes":8}}'
cat /tmp/r.json | j

echo "== 4. 计数溢出（422）：max_count 拒绝 =="
curl -s -o /tmp/r.json -w "HTTP %{http_code}\n" -X POST "$BASE/v1/query" \
  -H 'Content-Type: application/json' -d '{
    "op":"UNION","qualifier":"all","mode":"in_memory",
    "schema":"v:bigint",
    "left":{"rows":[[1],[1]]},"right":{"rows":[[1],[1]]},
    "limits":{"max_count":3}}'
cat /tmp/r.json | j

echo "== 5. 状态冲突（409）：重复 run_id（先成功再冲突） =="
curl -s -o /dev/null -w "first:  HTTP %{http_code}\n" -X POST "$BASE/v1/query" \
  -H 'Content-Type: application/json' -d '{
    "op":"UNION","qualifier":"distinct","mode":"auto","run_id":"demo-run",
    "schema":"v:bigint","left":{"rows":[[1]]},"right":{"rows":[]}}'
curl -s -o /tmp/r.json -w "second: HTTP %{http_code}\n" -X POST "$BASE/v1/query" \
  -H 'Content-Type: application/json' -d '{
    "op":"UNION","qualifier":"distinct","mode":"auto","run_id":"demo-run",
    "schema":"v:bigint","left":{"rows":[[1]]},"right":{"rows":[]}}'
cat /tmp/r.json | j

echo "== 6. 夹具文件输入 + 重放事件 =="
curl -s -X POST "$BASE/v1/query" -H 'Content-Type: application/json' -d '{
  "op":"INTERSECT","qualifier":"all","mode":"external",
  "schema":"a:text,b:text",
  "left":{"fixture":"edge_left.csv"},
  "right":{"fixture":"edge_right.csv"}}' | j
curl -s "$BASE/v1/runs/demo-run/events" | j
