# opp408 — 可解释内容协商资源服务

一个用 **TypeScript + Node.js + Fastify + SQLite** 实现的资源服务，对同一资源提供
多种**媒体类型**与多种**语言**表示，并对 `Accept` / `Accept-Language` 协商给出
**可解释**的结果：每个候选的匹配范围、权重、判定步骤，以及明确的失败类别。

所有数据均为仓库内的**本地合成夹具**（`src/state/fixtures.ts`），SQLite 使用 Node
内置的 `node:sqlite`，无需任何生产账号或外部服务。

---

## 1. 协商语义（契约）

### 媒体类型（RFC 9110 §12.5.1）

- `*/*` 匹配全部；`text/*` 匹配该类型所有子类型；`text/html` 精确匹配。
- 当多个范围都匹配同一候选时，**最具体的范围生效，与其 q 无关**：
  精确类型 > 类型通配 > 全通配；Accept-参数更多者更具体；再并列则按头中顺序。
  因此 `application/json;q=0, */*;q=0.9` **会禁止** json 候选（精确 q=0 压过通配正值）。
- Accept-参数（除 `q` 外）参与匹配：范围上的每个参数必须在候选上存在且等值
  （大小写不敏感），候选可携带额外参数。

### 权重 q（统一规则）

- **q=0 合法且表示「明确禁止」**，绝不当作「低权重」处理。
- 非法权重严格拒绝：仅接受 `0`–`1`、最多 3 位小数（`1.000` 为上限）。

### 语言（RFC 4647 基本过滤 + 可配置回退）

- 精确匹配 / 前缀匹配（范围 `zh` 覆盖候选 `zh-cn`）取**完整权重**；多个范围匹配时
  取最长前缀范围的 q（与媒体特异性对称），`*` 优先级最低。
- **回退**（可配置，默认开启）：候选是请求范围的更短前缀时（请求 `zh-cn`、候选
  `zh`），每剥一个子标签权重乘一次 `penaltyPerStrippedSubtag`（默认 0.9）。
  例如 `en-US;q=0.9` 回退到 `en` → 0.81。回退**绝不**把更长范围上的 q=0 向上扩散
  去禁止更短候选。

### 统一的非法输入策略（`config/negotiation.config.json`）

| 情况 | 策略 |
|---|---|
| 非法 q / 畸形条目 / 畸形或重复参数 / 语言上的未知参数 | `invalidEntryPolicy`：`drop-with-warning`（默认，丢弃+结构化告警）/ `reject-header`（整头拒绝，400 失败关闭）/ `ignore`（静默丢弃） |
| 重复条目 | `duplicatePolicy`：`first-wins`（默认）/ `last-wins`，均记录 `DUPLICATE_ENTRY` |

### 媒体与语言**分别**协商

任一维度无法满足都返回明确类别，而不是猜测：

| failureCategory | HTTP | 含义 |
|---|---|---|
| `OK` | 200 | 已选定表示 |
| `MALFORMED_HEADER` | 400 | 头被策略整体拒绝（失败关闭） |
| `HEADER_TOO_LARGE` | 400 | 协商条目超过 `maxAcceptEntries` |
| `MEDIA_FORBIDDEN` | 406 | 所有候选媒体类型被 q=0 明确禁止 |
| `MEDIA_NOT_ACCEPTABLE` | 406 | 无任何非零 Accept 范围匹配 |
| `LANGUAGE_FORBIDDEN` | 406 | 媒体可行候选的语言全部被 q=0 禁止 |
| `LANGUAGE_NOT_ACCEPTABLE` | 406 | 媒体可行候选无任何非零语言范围匹配 |
| `NO_CANDIDATES` | 406 | 资源没有任何表示 |

**缺头**遵循 RFC：等价于通配（服务端按自身偏好，即声明 ordinal 最小者）。

### Vary 与缓存区分维度

响应头 `Vary` 按**资源真实存在差异的维度**输出（候选含多种媒体类型才列 `Accept`，
含多种语言才列 `Accept-Language`），与本次请求取值无关——所以 `*/*` 或缺头请求
也会带 `Vary`，避免某个通配响应在缓存里遮蔽其他 `Accept` 的响应。

「**这一次请求头是否实际改变了选择**」是另一个正交概念，在诊断中以
`influencedHeaders` 与反事实（counterfactual）结果给出：例如资源多类型但请求
`Accept: */*` 时，`Vary` 含 `Accept`，而 `influencedHeaders.accept=false`。

---

## 2. 模块划分

按要求拆分为真实模块（非单文件、非桩、无硬编码输出）：

```
src/
  contract/                 # 契约解析
    headerTokenizer.ts      #   引号感知分词、q 值严格语法
    acceptParser.ts         #   Accept 解析
    languageParser.ts       #   Accept-Language 解析
    parsePolicy.ts          #   统一策略与告警码
    types.ts
  kernel/                   # 执行内核（纯函数，无 I/O）
    mediaMatcher.ts         #   媒体特异性/通配/参数匹配
    languageMatcher.ts      #   精确/前缀/通配/折扣回退
    negotiator.ts           #   组合决策、失败分类、Vary 维度、反事实、步骤
  state/                    # 状态适配
    repository.ts           #   SQLite（node:sqlite）仓储与候选映射
    fixtures.ts             #   本地合成夹具
    configLoader.ts         #   独立配置文件加载与启动期校验
    traceStore.ts           #   诊断环形缓冲
  http/
    routes.ts               # 诊断/资源 HTTP 接口（薄层）
    negotiationService.ts   #   解析→内核编排、大小守卫
  observability/runLogger.ts# 可关联 runId 的结构化 JSON 日志
  app.ts                    # 装配（入口与 E2E 测试共用）
  server.ts                 # 进程入口
config/negotiation.config.json  # 独立配置
test/                       # 独立测试（断言具体结果与失败类别）
```

---

## 3. 运行

要求 Node.js ≥ 22.5（使用内置 `node:sqlite`）。

```bash
npm ci            # 依据 package-lock.json 安装锁定依赖
npm run build     # tsc 编译到 dist/
npm start         # 读取 config/negotiation.config.json，监听 127.0.0.1:4080
# 开发模式（免编译）：npm run dev
# 自定义配置：NEGOTIATION_CONFIG=/path/to/config.json npm start
```

启动后自动把合成夹具写入 `data/negotiation.db`（已存在则不重复播种）。

### 示例调用

```bash
bash scripts/example-calls.sh
```

手工示例：

```bash
# 权重 + 语言前缀
curl -s -D - \
  -H 'Accept: text/html;q=0.4, application/json;q=0.9' \
  -H 'Accept-Language: zh' \
  http://127.0.0.1:4080/welcome

# 所有媒体被 q=0 禁止 -> 406 MEDIA_FORBIDDEN
curl -s -H 'Accept: text/*;q=0, application/*;q=0' -H 'Accept-Language: *' \
  http://127.0.0.1:4080/welcome

# 语言回退 en-US -> en（0.81）
curl -s -D - -H 'Accept: application/json' -H 'Accept-Language: en-US;q=0.9' \
  http://127.0.0.1:4080/welcome
```

每个响应都带 `X-Run-Id`。

### 诊断接口

```bash
curl -s http://127.0.0.1:4080/diagnostics | python3 -m json.tool
curl -s 'http://127.0.0.1:4080/diagnostics?runId=<X-Run-Id>' | python3 -m json.tool
```

返回每次运行的原始输入头、每个候选的 `steps`（匹配范围/q/参数/语言种类/
禁止原因）、`scores`、`vary`、`influencedHeaders`、反事实结果与解析告警。
其它端点：`GET /`、`GET /resources`、`GET /healthz`。

### 日志

每行一个 JSON 对象，含 `service`/`version`/`node`/`platform`、同一 `runId`、
单调递增 `step`（进度）、`phase` 与 `basis`（判定依据）。协商失败输出
`"level":"error"` 并带 `failureCategory`，绝不把异常状态记成成功。

---

## 4. 测试

```bash
npm test                 # vitest run，63+ 个用例
npm run test -- --coverage   # v8 覆盖率（阈值 80%；当前约 94%）
npm run typecheck        # tsc --noEmit
```

测试为**独立**实现，断言具体选择结果、具体 q、具体失败类别与缓存维度，而非
「接口能调用」：

- `acceptParser.test.ts` / `languageParser.test.ts`：q=0、非法 q 边界、畸形/重复/
  未知参数、三种策略、通配与位置稳定性、引号内逗号。
- `negotiator.test.ts`：手算候选集，覆盖通配特异性压过 q、所有候选被禁止、
  参数匹配与落空、语言精确/前缀/通配/q=0/回退折扣、缺头中性化、失败分类、
  Vary 资源维度与 `influencedHeaders`。
- `repository.test.ts`：真实 SQLite（内存与落盘）播种、映射、持久化。
- `configLoader.test.ts`：独立配置的合法加载与各类 fail-fast。
- `http.e2e.test.ts`：真实 Fastify（`inject`）+ 真实内存 SQLite，断言状态码、
  选中表示、响应头、失败类别、诊断内容，并校验日志 runId 关联、版本、单调步骤
  与 error 级别。

测试不与被测核心共用任何期望生成逻辑；期望值（选中 id、q、类别）均独立手算。

---

## 5. 已验证与剩余限制

已真实执行验证：`tsc --noEmit` 通过；63+ 测试全绿；覆盖率约 94%；编译后实际
启动服务并用 curl 跑通权重/通配禁止/参数/回退/独立失败/非法 q/诊断/日志等场景。

已知限制（明确说明，不做隐性兜底）：

- 依赖 Node 内置的实验性模块 `node:sqlite`（当前 API 可能在未来 Node 版本调整）；
  启动命令以 `--no-warnings` 静默其实验性告警。
- 语言标签做大小写不敏感的子标签比较，未接入完整 BCP-47 注册表（不做
  `zh-Hans/zh-Hant` 脚本别名等映射）。
- Accept-参数采用「范围参数必须被满足」的严格解释；未实现 RFC 9110 的
  `proactive` 之外的内容编码（Accept-Encoding）/字符集（Accept-Charset）协商。
- 诊断轨迹保存在进程内有界环形缓冲（`traceBufferSize`），重启即清空、非分布式。
- 服务仅监听 `127.0.0.1`，面向本地/合成用途，未含鉴权与限流（无真实外部参与方）。
