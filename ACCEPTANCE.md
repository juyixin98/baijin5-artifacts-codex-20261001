# 验收记录（ACCEPTANCE）

本文件如实记录最近一次验收执行的结果。复现方式：`./scripts/acceptance.sh`
（完整输出写入 `ACCEPTANCE.log`，测试详情写入 `test-output.log`）。

## 环境

- Go: go1.22.2 linux/amd64
- 依赖: github.com/mattn/go-sqlite3 v1.14.52, github.com/go-asn1-ber/asn1-ber v1.5.8
- 服务版本: 1.0.0

## 执行结果（2026-10-02，本地执行）

| 步骤 | 结果 |
|------|------|
| `go build ./...` | 通过 |
| `go test ./...` | 5 个包全部 ok，40 个顶层测试全部通过，286 条 `verdict=PASS` 判定日志 |
| 覆盖率 | internal/ber 85.3%，internal/server 83.0%，internal/config 100%，internal/store 95.2% |
| 服务启动（临时配置 + SQLite） | 通过，run_id 与版本/限制在日志中可见 |
| 请求样例（9 个） | 全部返回预期结果（见下） |
| CLI 交叉核验 | `bercli decode`/`bercli der` 输出与服务一致 |

## 请求样例实测摘要

- `GET /v1/health` → 200，`run_id`/`version`/`go_version` 齐全
- `decode 0203010001` → 200，INTEGER 65537
- `decode 3080308002010500000000`（嵌套不确定长度）→ 200，SEQUENCE{SEQUENCE{5}}，两层 `indefinite:true`
- `decode 0201`（截断）→ 422，`truncation@2`
- `decode 0000`（游离 EOC）→ 422，`eoc@0`
- `encode SEQUENCE{5,-129}}` → 200，`30070201050202ff7f`
- `canonicalize 30800201050000` → 200，DER `3003020105`，`changed:true`
- `verify-der 02017f` → 200；`verify-der 0202007f`（非最小整数）→ 422，`constraint@1`

## 已知边界（如实说明）

- `cmd/berd`、`cmd/bercli` 为薄入口，未计入覆盖率统计；逻辑均在 `internal/` 下。
- BIT STRING 仅支持原始形式（constructed BIT STRING 在 DER 路径显式拒绝，
  BER 解析保留 TLV 结构但值级访问报错）。
- 参考库 `go-asn1-ber` 与标准库 `encoding/asn1` 用于对照；两者不覆盖的
  场景（EOC 误用、资源上限）以手工向量断言精确偏移。
