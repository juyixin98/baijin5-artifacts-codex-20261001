#!/usr/bin/env bash
# mmu-lab 示例请求序列（需要 `curl` 与 `jq`，jq 仅用于美化输出）。
#
# 用法：
#   1) 终端 A：MMU_LISTEN=127.0.0.1:8080 cargo run
#   2) 终端 B：./examples/requests.sh
#
# 序列覆盖四个规定夹具：同虚址不同进程、跨页写、权限降级 + TLB 过期、
# 大页/小页覆盖冲突；并演示 run_id 诊断检索与快照保存。
set -u
BASE="${MMU_BASE:-http://127.0.0.1:8080}"
JQ="$(command -v jq || true)"

say() { printf '\n\033[1;36m== %s ==\033[0m\n' "$*"; }
req() { # req METHOD PATH [JSON]
  local method="$1" path="$2" body="${3:-}"
  if [ -n "$body" ]; then
    curl -sS -X "$method" -H 'content-type: application/json' -d "$body" "$BASE$path"
  else
    curl -sS -X "$method" "$BASE$path"
  fi | ${JQ:-cat}
}

say "0. 固定机器参数"
req GET /machine

say "1. 创建两个进程（ASID）"
req POST /asids '{"name":"proc-a"}'
req POST /asids '{"name":"proc-b"}'

say "2. 同一虚址 0x1000 在两个进程中映射到各自物理帧"
req POST /maps '{"asid":1,"va":4096,"page":"4K","permissions":{"read":true,"write":true,"execute":true}}'
req POST /maps '{"asid":2,"va":4096,"page":"4K","permissions":{"read":true,"write":false,"execute":true}}'

say "3. 两个进程翻译同一虚址（物理地址必须不同）"
req POST /translate '{"asid":1,"va":4660,"access":"read","len":1}'
req POST /translate '{"asid":2,"va":4660,"access":"read","len":1}'

say "4. 跨页写：0x1ff0 起 32 字节，第一页 RW，第二页只读 -> 边界权限故障"
req POST /maps '{"asid":1,"va":8192,"page":"4K","permissions":{"read":true,"write":false,"execute":true}}'
req POST /translate '{"asid":1,"va":8176,"access":"write","len":32}'

say "5. 大页/小页覆盖冲突：在 8 MiB 区先建 4M 大页，再在其中建 4K 小页 -> 409"
req POST /maps '{"asid":1,"va":8388608,"pa":4194304,"page":"4M","permissions":{"read":true,"write":true,"execute":true}}'
req POST /maps '{"asid":1,"va":8392704,"page":"4K","permissions":{"read":true,"write":true,"execute":true}}'

say "6. 权限降级 + TLB 过期：预热后不失效降级，旧可写条目仍命中且 stale=1"
req POST /translate '{"asid":1,"va":4096,"access":"write","len":1}'
req POST /maps/protect '{"asid":1,"va":4096,"permissions":{"read":true,"write":false,"execute":true},"invalidate":false}'
req POST /translate '{"asid":1,"va":4096,"access":"write","len":1}'

say "7. 执行显式失效协议后，同样的写被拒绝"
req POST /tlb/invalidate '{"asid":1,"va":4096,"page":"4K"}'
req POST /translate '{"asid":1,"va":4096,"access":"write","len":1}'

say "8. 诊断：查看最近事件；用上一步故障的 run_id 可检索完整轨迹"
req GET '/events?limit=5'

say "9. 资源视角：物理帧与 TLB 现状"
req GET /frames
req GET /tlb

say "10. 快照保存到本地文件（原子写）"
req POST /snapshot/save '{"path":"./store/demo.json"}'
