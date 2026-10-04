"""密码适配层:对成熟 Paillier 库 phe 的薄封装。

只暴露算法真实具备的运算:
- 密文 + 密文(同态加)
- 密文 * 明文标量(同态标量乘,标量范围由上层校验)
不提供密文 * 密文 —— Paillier 是加法同态,该运算不存在。

私钥序列化后用 cryptography.Fernet 加密落盘(本地测试级保护,
密钥来自配置,见 config.Settings.fernet_key)。
"""
from __future__ import annotations

import hashlib
import json
from typing import Tuple

from cryptography.fernet import Fernet
from phe import paillier
from phe.paillier import EncryptedNumber, PaillierPrivateKey, PaillierPublicKey

from .errors import ErrorCategory, ServiceError

# 本服务只接受指数 0 的整数编码;phe 的浮点指数编码不在支持范围内
SUPPORTED_EXPONENT = 0


def generate_keypair(bits: int) -> Tuple[PaillierPublicKey, PaillierPrivateKey]:
    if bits < 512:
        raise ServiceError(ErrorCategory.VALIDATION, f"密钥位数 {bits} 过小,至少 512")
    return paillier.generate_paillier_keypair(n_length=bits)


def key_fingerprint(n: int) -> str:
    """公钥模数指纹,用于把密文提交绑定到批次密钥。"""
    return hashlib.sha256(f"paillier-n:{n}".encode("ascii")).hexdigest()


def encrypt_signed(pub: PaillierPublicKey, value: int) -> EncryptedNumber:
    """加密有符号整数(phe 内部完成 mod n 编码)。"""
    return pub.encrypt(value)


def serialize(enc: EncryptedNumber) -> dict:
    """密文 -> 可 JSON 序列化结构(大整数用十进制字符串)。"""
    return {"c": str(enc.ciphertext(be_secure=False)), "e": enc.exponent}


def deserialize(pub: PaillierPublicKey, c: int, exponent: int) -> EncryptedNumber:
    if exponent != SUPPORTED_EXPONENT:
        raise ServiceError(
            ErrorCategory.VALIDATION,
            f"仅支持指数 {SUPPORTED_EXPONENT} 的整数编码,收到 exponent={exponent}",
        )
    if not 0 <= c < pub.n * pub.n:
        raise ServiceError(
            ErrorCategory.OUT_OF_RANGE,
            "密文不在 [0, n^2) 内,可能来自其他密钥或被篡改",
        )
    return EncryptedNumber(pub, c, SUPPORTED_EXPONENT)


def add(a: EncryptedNumber, b: EncryptedNumber) -> EncryptedNumber:
    return a + b


def scalar_mul(enc: EncryptedNumber, k: int) -> EncryptedNumber:
    """密文乘明文标量(支持负标量,范围由 service 层校验)。"""
    return enc * k


def decrypt_residue(priv: PaillierPrivateKey, enc: EncryptedNumber) -> int:
    """解密为 [0, n) 原始剩余;符号解释与上界检查在 encoding 层完成。"""
    return priv.raw_decrypt(enc.ciphertext(be_secure=False))


def export_private_key(priv: PaillierPrivateKey, fernet: Fernet) -> bytes:
    payload = json.dumps({"p": str(priv.p), "q": str(priv.q)}).encode("utf-8")
    return fernet.encrypt(payload)


def import_private_key(pub: PaillierPublicKey, blob: bytes,
                       fernet: Fernet) -> PaillierPrivateKey:
    try:
        payload = json.loads(fernet.decrypt(blob).decode("utf-8"))
        return PaillierPrivateKey(pub, int(payload["p"]), int(payload["q"]))
    except ServiceError:
        raise
    except Exception as exc:  # Fernet InvalidToken / JSON / 缺字段等
        raise ServiceError(
            ErrorCategory.INTERNAL, f"私钥解密或解析失败: {type(exc).__name__}"
        ) from exc
