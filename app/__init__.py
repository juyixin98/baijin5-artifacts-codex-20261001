"""Paillier 密文求和与加权聚合本地测试服务。

分层说明:
- encoding:        协议层有符号整数编码与值域/总和上界检查
- crypto_adapter:  对成熟库 phe 的薄适配(密钥、加解密、密文加法与标量乘)
- store:           SQLite 状态与审计
- service:         聚合业务编排(批次、提交、聚合、解密、验证)
- verifier:        独立于核心实现的明文参考与比对判定
- api:             FastAPI 接口层
"""

__version__ = "0.1.0"
