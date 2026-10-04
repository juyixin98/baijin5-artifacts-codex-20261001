"""密码适配层测试：随机密文、失败类别、盲索引的确定性定位。"""
import hashlib
import hmac
import struct

import pytest

from blindex.crypto_adapter import CryptoAdapter
from blindex.errors import BlindIndexError, Category


class TestRandomizedCiphertext:
    def test_same_plaintext_different_ciphertext(self, keyring):
        ad = CryptoAdapter(keyring)
        c1 = ad.encrypt("alice@example.com")
        c2 = ad.encrypt("alice@example.com")
        assert c1 != c2  # 随机 nonce → 随机密文
        assert ad.decrypt(c1) == ad.decrypt(c2) == "alice@example.com"

    def test_tampered_ciphertext_category(self, keyring):
        ad = CryptoAdapter(keyring)
        blob = bytearray(ad.encrypt("secret"))
        blob[-1] ^= 0x01  # 翻转 tag 内一字节
        with pytest.raises(BlindIndexError) as ei:
            ad.decrypt(bytes(blob))
        assert ei.value.category is Category.DECRYPT_AUTH_FAILED

    def test_unknown_key_version_category(self, keyring):
        ad = CryptoAdapter(keyring)
        blob = bytearray(ad.encrypt("secret"))
        blob[3] = 0xFF
        blob[4] = 0xFE  # 版本号改为不存在的 65534
        with pytest.raises(BlindIndexError) as ei:
            ad.decrypt(bytes(blob))
        assert ei.value.category is Category.UNKNOWN_KEY_VERSION

    def test_malformed_envelope_category(self, keyring):
        ad = CryptoAdapter(keyring)
        with pytest.raises(BlindIndexError) as ei:
            ad.decrypt(b"not-an-envelope")
        assert ei.value.category is Category.ENVELOPE_MALFORMED


class TestBlindIndex:
    def test_deterministic_and_equality_leakage_is_explicit(self, keyring):
        """盲索引是确定性 keyed hash：同值同索引（泄露相等关系）。

        本测试故意断言这一泄露存在 —— 它是设计前提而非 bug；
        索引不是匿名化，也不能替代密文（见下一条测试）。
        """
        ad = CryptoAdapter(keyring)
        i1 = ad.blind_index("email", "alice@example.com")
        i2 = ad.blind_index("email", "alice@example.com")
        i3 = ad.blind_index("email", "bob@example.org")
        assert i1 == i2          # 相等关系对持有索引表者可见
        assert i1 != i3
        assert len(i1) == keyring.index_bits // 4

    def test_index_is_not_ciphertext_substitute(self, keyring):
        """禁止把确定性索引当密文：索引不可解密、且与密文编码完全不同。"""
        ad = CryptoAdapter(keyring)
        idx = ad.blind_index("email", "alice@example.com")
        ct = ad.encrypt("alice@example.com")
        assert idx.encode() != ct
        # 索引不是合法信封，无法“解密”出明文
        with pytest.raises(BlindIndexError) as ei:
            ad.decrypt(bytes.fromhex(idx))
        assert ei.value.category is Category.ENVELOPE_MALFORMED

    def test_cross_implementation_agreement(self, keyring):
        """PyCryptodome（核心）与 hashlib（独立参考）对同一规范必须一致。"""
        ad = CryptoAdapter(keyring)
        key = keyring.index_keys[keyring.current_index_version]

        def lp(s: str) -> bytes:
            b = s.encode()
            return struct.pack(">H", len(b)) + b

        msg = b"BLIDX\x01" + lp(keyring.domain) + lp("email") + lp("alice@example.com")
        ref = hmac.new(key, msg, hashlib.sha256).hexdigest()[: keyring.index_bits // 4]
        assert ad.blind_index("email", "alice@example.com") == ref

    def test_version_isolation(self, keyring):
        ad = CryptoAdapter(keyring)
        keyring.index_keys[2] = b"\x07" * 32
        i1 = ad.blind_index("email", "alice@example.com", version=1)
        i2 = ad.blind_index("email", "alice@example.com", version=2)
        assert i1 != i2  # 换密钥即换索引空间

    def test_unknown_index_version_category(self, keyring):
        ad = CryptoAdapter(keyring)
        with pytest.raises(BlindIndexError) as ei:
            ad.blind_index("email", "x", version=999)
        assert ei.value.category is Category.UNKNOWN_KEY_VERSION
