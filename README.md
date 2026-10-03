# colorconvert — 基于 LittleCMS 的图像颜色转换后端

处理 **RGB、灰度、受限 CMYK** 的 ICC 颜色转换。引擎为 LittleCMS 2.14(经
Pillow `ImageCms` 调用),HTTP 层为 FastAPI。所有 profile 都是仓库内的显式
本地依赖(`profiles/`),测试数据全部为本地合成夹具。

## 模块关系

```
colorconvert/
  contract.py     图像数据契约:uint8、(H,W,C)、固定通道顺序、alpha 独立平面、
                  预乘/非预乘语义声明。被所有模块依赖,自身无内部依赖。
  errors.py       FailureCategory 枚举 + 异常基类(所有拒绝都带失败类别)。
  profiles.py     ICC profile 注册表:sha256 钉死、引擎可打开性、色彩空间与
                  角色校验;embedded profile 的同构校验。
  kernel.py       数值内核:alpha 语义归一化(解预乘→转换→重预乘)、
                  调用 LittleCMS 做逐像素转换、生成 KernelReport。
  tiles.py        分块规划与执行(ICC 转换为逐像素操作,分块结果与整图
                  位级一致,tests/test_tiles.py 断言这一点)。
  jobs.py         作业记录:状态机 + 决策列表(accepted/rejected/undecidable)。
  service.py      编排:校验 → 决策 → 分块执行 → 报告,写作业记录与日志。
  api.py          FastAPI 验证/转换接口(薄 HTTP 层,不含颜色逻辑)。
  diagnostics.py  请求级日志:request_id 贯穿,敏感数据(像素、base64、
                  profile 字节)只记录 sha256 指纹或 <redacted>。
  config.py       设置(profile 目录、像素上限、分块尺寸边界、日志级别)。

profiles/         钉死的 ICC profile + REGISTRY.json(tools/build_registry.py 生成)
fixtures/         golden_vectors.json(tools/make_fixtures.py 由解析参考实现生成)
tests/            测试;tests/reference/analytic.py 是独立于被测代码的
                  纯 NumPy 解析参考(IEC 61966-2-1 / Bradford / CIE Lab)
tools/            夹具与注册表生成脚本
```

## 关键算法假设(如实声明)

1. **不猜 profile**:源/目标 profile 必须显式指定注册表 id;源也可显式
   声明 `"embedded"` 使用图像内嵌 profile(同样经过校验)。图像无内嵌
   profile 且声明 `embedded` 时,请求判为 **undecidable**——拒绝假定 sRGB。
2. **8-bit 契约**:所有通道为 uint8。跨 profile 转换为有损操作:8 位量化
   往返误差实测上界 sRGB↔AdobeRGB 为 3 LSB、sRGB↔ProPhoto 为 4 LSB;
   系统**不宣称任何跨色域转换无损**(`report.lossy` 恒为 true,同 profile
   恒等转换除外)。
3. **PCS 为 D50**:ICC 连接空间是 D50 的 XYZ/Lab。黄金值测试的解析参考
   显式做 Bradford D65→D50 适应。
4. **LAB 8-bit 存储约定**:L*∈[0,255]↔[0,100];a*/b* 为有符号值回绕进
   uint8(LittleCMS/Pillow 的 `frombytes`/`tobytes` 约定;Pillow 的
   `fromarray(...,'LAB')` 与 transform 输出语义不一致,内核因此只走
   bytes 路径——这是开发中实测发现并写进契约的坑)。
5. **Alpha 语义固定**:alpha 永不参与颜色转换、位级原样传递;颜色始终在
   非预乘形式下转换。预乘输入先解预乘(`round(c*255/a)`,`a=0` 时颜色
   置 0),转换后按原 alpha 重预乘。转换为逐像素操作,透明边缘无颜色扩散
   (有精确相等性测试)。
6. **渲染意图与黑点补偿**:按请求传入引擎并**原样写入结果报告**;引擎对
   profile 不支持的意图会静默回退(lcms 行为),报告记录的是请求值。
7. **受限 CMYK**:仅当请求显式 `allow_cmyk=true` 且 profile 为注册表内
   钉死的 CMYK 条目时允许;8-bit;CMYK 输出走 TIFF(alpha 以独立 PNG
   sidecar 返回)。
8. **分块不变性**:分块纯为内存/可审计性优化,不改变数值结果(位级一致
   性有测试)。

## 本地验证

```bash
pip install -r requirements.txt        # 或使用系统已装版本,见下

# 1) 全部自动化测试(73 个)
python -m pytest

# 2) 重新生成注册表与黄金向量(可选,验证可复现性)
python tools/build_registry.py
python tools/make_fixtures.py
git diff --stat profiles/REGISTRY.json fixtures/golden_vectors.json   # 应无变化

# 3) 启动服务并手动验证
uvicorn colorconvert.api:app --port 8137 &
curl -s localhost:8137/healthz
curl -s localhost:8137/v1/profiles | python -m json.tool | head
```

**预期判断方式**:

- `python -m pytest` 末行应为 `73 passed`;任何 failed/error 都视为未通过。
- 黄金值测试(`test_kernel_golden.py`)断言内核输出与解析参考在 8-bit
  量化容差(±2 LSB)内一致;解析参考本身先对公开常量(Lindbloom sRGB→Lab
  D50)校验,保证参考答案不是被测实现自生成的。
- 失败类别测试(`test_bad_profiles.py`、`test_api.py`)断言每类坏输入映射到
  具体 `FailureCategory`(如 `profile_missing`、`cmyk_restricted`、
  `embedded_profile_absent`),不是只检查"接口能调通"。
- 日志中每行带 `request_id`,决策行形如 `decision=accepted|rejected|
  undecidable reason=...`;图像 base64 内容不得出现在日志里(有测试断言)。

## 依赖版本(本机实测)

| 包 | 版本 | 用途 |
|---|---|---|
| Python | 3.12.3 | |
| Pillow | 10.2.0(含 LittleCMS 2.14) | ICC 引擎、图像编解码 |
| numpy | 2.4.6 | 数组契约与数值内核 |
| scipy | 1.15.3 | 测试夹具合成(平滑测试图) |
| fastapi | 0.141.1 + uvicorn | HTTP 接口 |
| pytest | 9.1.1 + httpx | 测试 |

ICC profile 来源:系统 colord/ghostscript 包(`profiles/REGISTRY.json` 内
每个条目含 `provenance` 与 sha256)。

## 测试状态

- **已运行并通过**:全部 73 个(`python -m pytest`,2026-10-03)。
- 已知警告:`StarletteDeprecationWarning`(httpx/httpx2 命名),来自
  环境的 starlette 版本,与本项目逻辑无关。
- **未覆盖/未运行**:并发压力、超大图(>40MP 上限路径仅有拒绝测试)、
  16-bit 数据(契约明确不支持)。这些如实标记为未实现,而非失败。

## 已知限制

- 仅 8-bit;不支持 16-bit/浮点、不支持 DeviceLink 与抽象 profile。
- 作业存储为进程内内存,重启即失。
- 渲染意图以请求值入账,引擎静默回退不会被检测(见假设 6)。
