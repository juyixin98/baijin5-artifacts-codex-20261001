"""成熟密码学适配层:全部委托 cryptography / PyCryptodome,不自实现原语.

- 密钥协商: X25519 (cryptography)
- 密钥派生: HKDF-SHA256 (cryptography)
- 对称加密: AES-256-GCM (cryptography),用于份额的端到端加密
- 掩码扩展: AES-256-CTR 密钥流 (PyCryptodome),种子 -> 确定性 uint64 掩码序列

私钥以 32 字节原始形式参与 Shamir 分享;公钥以 32 字节原始形式传输。
"""

from __future__ import annotations

import os

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from Crypto.Cipher import AES

from .encoding import MODULUS
from .errors import computation_failure, input_error

KEY_BYTES = 32
GCM_NONCE_BYTES = 12
HKDF_INFO_MASK = b"secagg-teach/mask-prg/v1"
HKDF_INFO_SHARE_ENC = b"secagg-teach/share-enc/v1"


def generate_keypair() -> tuple[bytes, bytes]:
    """生成 X25519 密钥对,返回 (私钥原始字节, 公钥原始字节)。"""
    sk = X25519PrivateKey.generate()
    sk_bytes = sk.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    pk_bytes = sk.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return sk_bytes, pk_bytes


def public_key_from_private(sk_bytes: bytes) -> bytes:
    sk = X25519PrivateKey.from_private_bytes(sk_bytes)
    return sk.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )


def key_agreement(sk_bytes: bytes, peer_pk_bytes: bytes, info: bytes) -> bytes:
    """X25519 协商 + HKDF 派生 32 字节会话密钥。"""
    if len(sk_bytes) != KEY_BYTES or len(peer_pk_bytes) != KEY_BYTES:
        raise input_error(
            "bad_key_len",
            "X25519 密钥必须为 32 字节",
            sk_len=len(sk_bytes),
            pk_len=len(peer_pk_bytes),
        )
    try:
        shared = X25519PrivateKey.from_private_bytes(sk_bytes).exchange(
            X25519PublicKey.from_public_bytes(peer_pk_bytes)
        )
    except ValueError as exc:
        raise computation_failure(
            "key_agreement_failed", "X25519 协商失败(可能为低阶点)", reason=str(exc)
        ) from exc
    return HKDF(algorithm=hashes.SHA256(), length=KEY_BYTES, salt=None, info=info).derive(
        shared
    )


def aead_encrypt(key: bytes, plaintext: bytes, aad: bytes) -> bytes:
    """AES-256-GCM 加密,nonce 随机生成并前置。"""
    if len(key) != KEY_BYTES:
        raise input_error("bad_aead_key", "AEAD 密钥必须为 32 字节")
    nonce = os.urandom(GCM_NONCE_BYTES)
    return nonce + AESGCM(key).encrypt(nonce, plaintext, aad)


def aead_decrypt(key: bytes, ciphertext: bytes, aad: bytes) -> bytes:
    """AES-256-GCM 解密;认证失败抛 COMPUTATION_FAILURE。"""
    if len(ciphertext) < GCM_NONCE_BYTES + 16:
        raise input_error(
            "ciphertext_too_short", "密文长度小于 nonce+tag", length=len(ciphertext)
        )
    nonce, body = ciphertext[:GCM_NONCE_BYTES], ciphertext[GCM_NONCE_BYTES:]
    try:
        return AESGCM(key).decrypt(nonce, body, aad)
    except Exception as exc:  # cryptography 抛 InvalidTag
        raise computation_failure(
            "decrypt_failed", "份额密文解密/认证失败", reason=type(exc).__name__
        ) from exc


def expand_mask(seed: bytes, length: int) -> list[int]:
    """确定性掩码扩展:AES-256-CTR 密钥流 -> length 个 Z_R 元素 (R = 2^64)。

    双方持有相同种子即可独立生成相同序列;nonce 固定为零(种子本身唯一)。
    """
    if len(seed) != KEY_BYTES:
        raise input_error("bad_seed_len", "掩码种子必须为 32 字节", length=len(seed))
    if length < 0:
        raise input_error("bad_mask_len", "掩码长度不能为负", length=length)
    nbytes = length * 8
    cipher = AES.new(seed, AES.MODE_CTR, nonce=b"", initial_value=0)
    stream = cipher.encrypt(b"\x00" * nbytes)
    return [int.from_bytes(stream[i * 8 : i * 8 + 8], "big") % MODULUS for i in range(length)]
