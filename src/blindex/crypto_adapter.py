"""成熟密码适配层。

- 机密性：AES-256-GCM（cryptography 库），每次加密使用新鲜随机 nonce，
  同一明文每次得到不同密文（随机密文，非确定性加密）。
- 盲索引：HMAC-SHA256（PyCryptodome 实现），截断到 keyring.index_bits。

安全定位（必须明确，不得含糊）：
- 盲索引是【确定性】的 keyed hash：它泄露相等关系（同值同索引），
  用于等值查询的候选筛选，【不是】匿名化，也【不能】替代密文。
- 记录的机密性完全由 AES-GCM 密文承担；索引只承担“找到候选”的职责，
  候选必须解密后二次确认（见 service.query）。
"""
from __future__ import annotations

import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from Crypto.Hash import HMAC, SHA256

from . import protocol
from .config import Keyring
from .errors import BlindIndexError, Category


class CryptoAdapter:
    def __init__(self, keyring: Keyring):
        keyring.validate()
        self._kr = keyring

    @property
    def keyring(self) -> Keyring:
        return self._kr

    # ---- 随机密文（AES-256-GCM） ------------------------------------------
    def encrypt(self, plaintext: str) -> bytes:
        """每次调用使用新的随机 nonce → 同值不同密文。"""
        key = self._kr.enc_keys[self._kr.current_enc_version]
        nonce = secrets.token_bytes(protocol.NONCE_LEN)
        ct_and_tag = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), None)
        return protocol.encode_envelope(
            self._kr.current_enc_version, nonce, ct_and_tag
        )

    def decrypt(self, blob: bytes) -> str:
        """解信封并按信封内版本取密钥解密；失败归入明确类别。"""
        key_version, nonce, ct_and_tag = protocol.decode_envelope(blob)
        key = self._kr.enc_keys.get(key_version)
        if key is None:
            raise BlindIndexError(
                Category.UNKNOWN_KEY_VERSION, f"未知加密密钥版本: {key_version}"
            )
        try:
            pt = AESGCM(key).decrypt(nonce, ct_and_tag, None)
        except InvalidTag:
            raise BlindIndexError(
                Category.DECRYPT_AUTH_FAILED, "密文认证失败（篡改或密钥不匹配）"
            )
        return pt.decode("utf-8")

    # ---- 带密钥盲索引（HMAC-SHA256，截断） ---------------------------------
    def blind_index(self, field: str, normalized_value: str, version: int | None = None) -> str:
        """计算指定索引密钥版本下的盲索引（hex，长度 = index_bits/4）。

        确定性输出是设计使然：它泄露相等关系，仅用于候选筛选。
        """
        v = self._kr.current_index_version if version is None else version
        key = self._kr.index_keys.get(v)
        if key is None:
            raise BlindIndexError(
                Category.UNKNOWN_KEY_VERSION, f"未知索引密钥版本: {v}"
            )
        msg = protocol.canonical_index_input(
            self._kr.domain, field, normalized_value
        )
        mac = HMAC.new(key, msg, SHA256).hexdigest()
        return mac[: self._kr.index_bits // 4]
