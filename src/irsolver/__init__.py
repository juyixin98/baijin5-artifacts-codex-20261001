"""irsolver：低精度分解 + 高精度残差的迭代精化线性求解器。

模块划分：
- inputs      数值输入（解析、校验、规范化，保留十进制原文）
- precision   精度阶梯定义（各级分解精度 / 残差精度 / 迭代预算）
- factor      分解内核（fp32/fp64 SciPy LU，mpmath 任意精度 LU）
- residual    残差计算（始终使用原矩阵，按当前阶段精度）
- evidence    误差证据（向后误差、前向误差界、迭代轨迹）
- condition   条件数估计与数值秩判定
- refinement  迭代精化主流程（升级策略、逐列独立求解）
- diagnostics 诊断（请求标识、决策日志、脱敏摘要）
- service     FastAPI 服务接口
- reference   独立参考实现（Householder QR，仅供测试对照）
"""

__version__ = "0.1.0"
