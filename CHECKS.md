# 检查清单

## 已执行(交付环境中实际运行)

| 检查 | 命令 | 结果 |
|------|------|------|
| 单元 + 集成测试(68 项) | `.venv/bin/python -m pytest tests/ -q` | 全部通过 |
| 测试覆盖率 | `pytest --cov=src/minigrad` | 91%(阈值 80%) |
| 有限差分验证(8 用例) | `.venv/bin/python scripts/validate.py` | 8 accepted / 0 undecidable / 0 rejected,退出码 0 |
| 参考夹具比对(纯 NumPy 解析参考) | `pytest tests/test_reference_fixtures.py` | 通过 |
| 广播梯度 / 共享节点 / 空维 / 原地写 | `pytest tests/test_broadcast_grad.py tests/test_shared_node.py tests/test_empty_dims.py tests/test_inplace.py` | 通过 |
| API 拒绝诊断(409/422/404)+ 脱敏断言 | `pytest tests/test_api.py` | 通过 |

环境:Python 3.12.3,numpy 2.4.6,fastapi 0.141.1,pytest 9.1.1
(版本固定于 requirements.txt / requirements-dev.txt)。

## 未执行(在此环境中无法或不适合运行,不声称已通过)

| 检查 | 原因 |
|------|------|
| 真实网络端口上的 uvicorn 服务冒烟 | 交付环境以 FastAPI TestClient 覆盖全部端点;未绑定真实端口验证 |
| 多线程并发下 grad 模式隔离的压力测试 | grad 模式为线程局部实现,但无并发测试基础设施;仅单线程语义已测 |
| 性能/内存基准 | 不在本次验收范围 |
| 静态类型检查(mypy/pyright) | 环境未安装;类型标注已提供但未机器验证 |
| float32 / 其他 dtype 配置 | 默认 float64 已验证;`MINIGRAD_DTYPE` 覆盖路径未做数值验证 |
