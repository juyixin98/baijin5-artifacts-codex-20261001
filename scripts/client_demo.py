#!/usr/bin/env python3
"""示例客户端:对运行中的服务执行一次完整聚合流程。

用法: 先启动服务(scripts/run_dev.sh),再运行
      .venv/bin/python scripts/client_demo.py

流程: 创建批次 -> 三个参与者本地加密并提交 -> 聚合 -> 解密 -> 独立验证。
参与者明文只存在于本脚本(模拟外部参与者),服务端只见密文。
"""
import os
import sys

import httpx
from phe.paillier import PaillierPublicKey

BASE = os.environ.get("PAGG_BASE_URL", "http://127.0.0.1:8000")

# 参与者私有明文(服务端不可见)与公开权重
PLAINTEXTS = {"alice": 4, "bob": -6, "carol": 10}
WEIGHTS = {"alice": 2, "bob": 3, "carol": -1}


def main() -> int:
    client = httpx.Client(base_url=BASE, timeout=30.0)

    health = client.get("/health").json()["data"]
    print(f"[health] run_id={health['run_id']} phe={health['phe']} "
          f"python={health['python']}")

    batch = client.post("/batches", json={"label": "demo"}).json()["data"]
    batch_id = batch["batch_id"]
    print(f"[batch] id={batch_id} sum_bound={batch['sum_bound'][:20]}...")

    pub = PaillierPublicKey(int(batch["n"]))
    for pid, value in PLAINTEXTS.items():
        enc = pub.encrypt(value)  # 参与者在本地加密
        resp = client.post(f"/batches/{batch_id}/submissions", json={
            "participant_id": pid,
            "ciphertext": str(enc.ciphertext(be_secure=False)),
            "exponent": enc.exponent,
            "weight": str(WEIGHTS[pid]),
            "key_fingerprint": batch["key_fingerprint"],
            "declared_abs": str(abs(value)),
        })
        resp.raise_for_status()
        print(f"[submit] {pid}: ok (明文不离开客户端)")

    agg = client.post(f"/batches/{batch_id}/aggregate").json()["data"]
    print(f"[aggregate] submissions={agg['submission_count']}")

    dec = client.post(f"/batches/{batch_id}/decrypt").json()["data"]
    expected = sum(v * WEIGHTS[p] for p, v in PLAINTEXTS.items())
    print(f"[decrypt] value={dec['value']} (明文参考={expected})")

    verdict = client.post(f"/batches/{batch_id}/verify", json={
        "reference": [{"participant_id": p, "value": str(v)}
                      for p, v in PLAINTEXTS.items()]
    }).json()["data"]
    print(f"[verify] {verdict['verdict']}: {verdict['reason']}")

    audit = client.get(f"/batches/{batch_id}/audit").json()["data"]
    print(f"[audit] events={[e['event'] for e in audit]}")

    ok = verdict["verdict"] == "MATCH" and int(dec["value"]) == expected
    print("DEMO OK" if ok else "DEMO FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
