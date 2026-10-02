#!/usr/bin/env bash
# mmu-teach 示例请求集合。用法：
#   cargo run --release            # 先启动服务（默认 127.0.0.1:8080）
#   bash examples/requests.sh      # 另开终端运行
set -u
B="${MMU_BASE:-http://127.0.0.1:8080}"
CT="-H content-type:application/json"
jpy(){ python3 -c 'import sys,json;d=json.load(sys.stdin);print(d.get("asid",d))'; }
say(){ echo; echo "==== $* ===="; }

say "机器信息（固定参数）"
curl -s "$B/api/v1/info" | python3 -m json.tool

say "建进程 A(ASID0) 与 B(ASID1)"
curl -s $CT -XPOST "$B/api/v1/processes" -d '{"name":"A"}' | python3 -m json.tool
curl -s $CT -XPOST "$B/api/v1/processes" -d '{"name":"B"}' | python3 -m json.tool

say "同一虚址 0x4000 映射到不同物理帧（A:ppn100 B:ppn200）"
curl -s $CT -XPOST "$B/api/v1/processes/0/mappings" \
  -d '{"vaddr":"0x4000","level":3,"ppn":100,"read":true,"write":true,"execute":true,"user":true}' -o /dev/null -w 'A map HTTP %{http_code}\n'
curl -s $CT -XPOST "$B/api/v1/processes/1/mappings" \
  -d '{"vaddr":"0x4000","level":3,"ppn":200,"read":true,"write":true,"execute":true,"user":true}' -o /dev/null -w 'B map HTTP %{http_code}\n'

say "翻译同虚址：A->409616(page_walk)；B->819216；再查 A->tlb_hit"
curl -s $CT -XPOST "$B/api/v1/translate" -d '{"asid":0,"vaddr":"0x4010","op":"read"}' | python3 -m json.tool
curl -s $CT -XPOST "$B/api/v1/translate" -d '{"asid":1,"vaddr":"0x4010","op":"read"}' | python3 -m json.tool
curl -s $CT -XPOST "$B/api/v1/translate" -d '{"asid":0,"vaddr":"0x4010","op":"read"}' \
  | python3 -c 'import sys,json;d=json.load(sys.stdin)["translation"];print("A again paddr",d["paddr"],"source",d["source"])'

say "跨页写：0x0ff8 起 16 字节，跨 0x1000 边界"
curl -s $CT -XPOST "$B/api/v1/processes/0/mappings" -d '{"vaddr":"0x0000","level":3,"ppn":10,"read":true,"write":true,"execute":true,"user":true}' -o /dev/null
curl -s $CT -XPOST "$B/api/v1/processes/0/mappings" -d '{"vaddr":"0x1000","level":3,"ppn":11,"read":true,"write":true,"execute":true,"user":true}' -o /dev/null
curl -s $CT -XPOST "$B/api/v1/access" -d '{"asid":0,"vaddr":"0x0ff8","len":16,"op":"write"}' | python3 -m json.tool

say "2MiB 大页（PPN 512 对齐）与其内偏移翻译 0x200abc"
curl -s $CT -XPOST "$B/api/v1/processes/1/mappings" \
  -d '{"vaddr":"0x200000","level":2,"ppn":512,"read":true,"write":true,"execute":true,"user":true}' -o /dev/null -w 'superpage map HTTP %{http_code}\n'
curl -s $CT -XPOST "$B/api/v1/translate" -d '{"asid":1,"vaddr":"0x200abc","op":"read"}' \
  | python3 -c 'import sys,json;t=json.load(sys.stdin)["translation"];print("paddr",hex(t["paddr"]),"leaf_level",t["leaf_level"])'

say "大小页覆盖冲突：在 2MiB 内建 4KiB -> 409 conflict"
curl -s $CT -XPOST "$B/api/v1/processes/1/mappings" -d '{"vaddr":"0x201000","level":3,"ppn":1000,"read":true}' | python3 -m json.tool

say "页故障类别：未映射末级页 -> page_not_present（HTTP 200 判别式）"
curl -s $CT -XPOST "$B/api/v1/translate" -d '{"asid":0,"vaddr":"0x7000","op":"read"}' | python3 -m json.tool

say "权限降级：0x4000 改只读（自动刷 TLB），随后写 -> permission_denied"
curl -s $CT -XPOST "$B/api/v1/processes/0/protect" -d '{"vaddr":"0x4000","read":true,"execute":true,"user":true}' | python3 -m json.tool
curl -s $CT -XPOST "$B/api/v1/translate" -d '{"asid":0,"vaddr":"0x4000","op":"write"}' | python3 -m json.tool

say "TLB 过期夹具（进程 2）：预热写 -> raw-pte 改只读不刷 -> 陈旧命中 -> sfence -> 拒绝"
curl -s $CT -XPOST "$B/api/v1/processes" -d '{"name":"stale"}' -o /dev/null
curl -s $CT -XPOST "$B/api/v1/processes/2/mappings" -d '{"vaddr":"0x6000","level":3,"ppn":300,"read":true,"write":true,"execute":true,"user":true}' -o /dev/null
curl -s $CT -XPOST "$B/api/v1/translate" -d '{"asid":2,"vaddr":"0x6000","op":"write"}' \
  | python3 -c 'import sys,json;print("warm",json.load(sys.stdin)["translation"]["source"])'
curl -s $CT -XPOST "$B/api/v1/processes/2/raw-pte" -d '{"vaddr":"0x6000","level":3,"value":"0x13"}' -o /dev/null
curl -s $CT -XPOST "$B/api/v1/translate" -d '{"asid":2,"vaddr":"0x6004","op":"write"}' \
  | python3 -c 'import sys,json;d=json.load(sys.stdin);print("stale write result",d["result"],"source",d["translation"]["source"])'
curl -s $CT -XPOST "$B/api/v1/sfence" -d '{"asid":2,"vpn":6}' -o /dev/null
curl -s $CT -XPOST "$B/api/v1/translate" -d '{"asid":2,"vaddr":"0x6004","op":"write"}' \
  | python3 -c 'import sys,json;d=json.load(sys.stdin);print("after sfence",d["result"],d["fault"]["kind"])'

say "诊断：遍历轨迹 / TLB 快照 / 运行日志"
curl -s "$B/api/v1/processes/1/walk?vaddr=0x200abc" | python3 -m json.tool
curl -s "$B/api/v1/tlb" | python3 -m json.tool
curl -s "$B/api/v1/runs?limit=5" | python3 -m json.tool

say "持久化：保存快照 / 导出 /（可选）载入"
curl -s $CT -XPOST "$B/api/v1/snapshot/save" -d '{"path":"/tmp/mmu-teach-snap.json"}' | python3 -m json.tool
echo "快照文件大小：$(wc -c < /tmp/mmu-teach-snap.json) 字节"

echo; echo "完成。设置 MMU_RUNLOG=路径 启动可把上述运行以 JSONL 落盘重放。"
