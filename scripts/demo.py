"""本地演示:5 客户端安全聚合,其中 1 人在轮2前掉线。

运行: python3 scripts/demo.py
预期: 打印 run_id、各阶段审计事件、聚合结果与独立明文参考一致。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from secagg.client import SecAggClient
from secagg.encoding import EncodingParams, decode_vector
from secagg.server import create_app
from secagg.state import StateStore
from secagg.transport import HttpTransport

CLIENTS = ["alice", "bob", "carol", "dave", "erin"]
THRESHOLD = 3
VECTOR_LEN = 4
SCALE = 1 << 20


def main() -> None:
    store = StateStore(":memory:")
    http = TestClient(create_app(store))
    transport = HttpTransport(http)

    run_id = http.post("/runs", json={
        "client_ids": CLIENTS, "threshold": THRESHOLD,
        "vector_len": VECTOR_LEN, "scale": SCALE,
    }).json()["run_id"]
    print(f"run_id = {run_id}")

    params = EncodingParams(scale=SCALE, max_clients=len(CLIENTS))
    clients = {c: SecAggClient(c, run_id, transport, params, THRESHOLD, VECTOR_LEN)
               for c in CLIENTS}
    vectors = {c: [i + 0.5 * j for j in range(VECTOR_LEN)]
               for i, c in enumerate(CLIENTS)}

    for c in CLIENTS:
        clients[c].advertise_keys()
    http.post(f"/runs/{run_id}/advance")

    for c in CLIENTS:
        clients[c].share_keys()
    http.post(f"/runs/{run_id}/advance")

    dropped = ["erin"]  # erin 在轮2前掉线
    alive = [c for c in CLIENTS if c not in dropped]
    for c in alive:
        clients[c].submit_masked_input(vectors[c])
    http.post(f"/runs/{run_id}/advance")
    print(f"活跃集合已冻结, 掉线: {dropped}")

    for c in alive:
        clients[c].send_recovery()

    result = http.get(f"/runs/{run_id}/result").json()
    decoded = decode_vector(result["sum"], params)
    expected = [sum(vectors[c][j] for c in alive) for j in range(VECTOR_LEN)]
    print(f"聚合结果(解码): {[round(v, 6) for v in decoded]}")
    print(f"明文参考(独立): {[round(v, 6) for v in expected]}")
    ok = all(abs(a - b) < 1e-3 for a, b in zip(decoded, expected))
    print("判定:", "一致 ✓" if ok else "不一致 ✗")

    print("\n审计日志:")
    for row in http.get(f"/runs/{run_id}/audit").json():
        print(f"  [{row['phase']:>8}] {row['event']}: {row['rationale']}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
