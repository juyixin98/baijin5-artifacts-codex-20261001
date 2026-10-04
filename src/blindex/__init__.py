"""blindex — 本地敏感字段随机密文 + 带密钥盲索引查询层。

模块划分：
- protocol        协议编码：字段规范化、用途域固定的盲索引输入编码、密文信封
- crypto_adapter  成熟密码适配：AES-256-GCM（cryptography）+ HMAC-SHA256 盲索引（PyCryptodome）
- storage         状态：SQLite 记录表 / 盲索引表 / 轮换状态
- audit           审计：日志仅输出记录身份与安全元数据
- service         业务：写入、双版本查询、解密二次确认、索引密钥轮换
- verify          独立验证：不经过核心实现重算期望结果并比对
- api             FastAPI 接口层
"""

__version__ = "0.1.0"
