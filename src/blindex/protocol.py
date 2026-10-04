"""协议编码层。

承担三项实际工作：
1. 字段规范化（normalize）：同值不同表示必须归一到同一形式，否则盲索引漏查。
2. 盲索引输入的规范编码（canonical_index_input）：长度前缀 + 用途域固定，
   防止字段间串扰与拼接歧义。
3. 密文信封编解码（encode/decode_envelope）：版本化的自描述二进制格式。

本模块不做任何密码运算，只定义字节级协议。
"""
from __future__ import annotations

import re
import struct
from typing import Optional

from .errors import BlindIndexError, Category

# ---- 信封格式 -------------------------------------------------------------
# ENVELOPE_MAGIC(3) || key_version(uint16 BE) || nonce(12) || ciphertext||tag
ENVELOPE_MAGIC = b"CE1"
NONCE_LEN = 12
TAG_LEN = 16
_HEADER_LEN = len(ENVELOPE_MAGIC) + 2

# ---- 盲索引输入编码 --------------------------------------------------------
# b"BLIDX\x01" || lp(domain) || lp(field) || lp(normalized_value)
# lp(s) = uint16 BE 长度 || utf-8 字节。长度前缀保证 (a,bc) 与 (ab,c) 不混淆。
INDEX_INPUT_MAGIC = b"BLIDX\x01"

_WS_RUN = re.compile(r"\s+")
_NON_DIGIT = re.compile(r"\D")
_ID_SEP = re.compile(r"[\s\-]")


def normalize_email(value: str) -> str:
    return value.strip().lower()


def normalize_phone(value: str) -> str:
    v = value.strip()
    if v.startswith("+"):
        return "+" + _NON_DIGIT.sub("", v[1:])
    return _NON_DIGIT.sub("", v)


def normalize_name(value: str) -> str:
    return _WS_RUN.sub(" ", value.strip()).casefold()


def normalize_id_number(value: str) -> str:
    return _ID_SEP.sub("", value.strip()).upper()


NORMALIZERS = {
    "email": normalize_email,
    "phone": normalize_phone,
    "name": normalize_name,
    "id_number": normalize_id_number,
}

#: 受控字段集合（schema 固定，便于审计与索引表设计）
FIELDS = tuple(NORMALIZERS.keys())


def normalize(field: str, value: Optional[str]) -> Optional[str]:
    """按字段规范归一化。None 透传（NULL 不入索引，由上层定义查询语义）。"""
    if field not in NORMALIZERS:
        raise BlindIndexError(Category.VALIDATION_ERROR, f"未知字段: {field!r}")
    if value is None:
        return None
    if not isinstance(value, str):
        raise BlindIndexError(
            Category.VALIDATION_ERROR, f"字段 {field} 的值必须是字符串或 null"
        )
    return NORMALIZERS[field](value)


def _length_prefixed(text: str) -> bytes:
    raw = text.encode("utf-8")
    if len(raw) > 0xFFFF:
        raise BlindIndexError(Category.VALIDATION_ERROR, "编码分量超长")
    return struct.pack(">H", len(raw)) + raw


def canonical_index_input(domain: str, field: str, normalized_value: str) -> bytes:
    """盲索引的规范输入编码：用途域固定 + 长度前缀，杜绝跨域/跨字段碰撞。"""
    if not domain:
        raise BlindIndexError(Category.CONFIG_ERROR, "用途域(domain)不能为空")
    return (
        INDEX_INPUT_MAGIC
        + _length_prefixed(domain)
        + _length_prefixed(field)
        + _length_prefixed(normalized_value)
    )


def encode_envelope(key_version: int, nonce: bytes, ciphertext_and_tag: bytes) -> bytes:
    if len(nonce) != NONCE_LEN:
        raise BlindIndexError(Category.VALIDATION_ERROR, "nonce 长度非法")
    if not 0 <= key_version <= 0xFFFF:
        raise BlindIndexError(Category.VALIDATION_ERROR, "密钥版本超出 uint16")
    return (
        ENVELOPE_MAGIC
        + struct.pack(">H", key_version)
        + nonce
        + ciphertext_and_tag
    )


def decode_envelope(blob: bytes) -> tuple[int, bytes, bytes]:
    """解信封，返回 (key_version, nonce, ciphertext_and_tag)。结构损坏即拒绝。"""
    if not isinstance(blob, (bytes, bytearray)):
        raise BlindIndexError(Category.ENVELOPE_MALFORMED, "信封不是字节串")
    blob = bytes(blob)
    if len(blob) < _HEADER_LEN + TAG_LEN:
        raise BlindIndexError(Category.ENVELOPE_MALFORMED, "信封长度不足")
    if not blob.startswith(ENVELOPE_MAGIC):
        raise BlindIndexError(Category.ENVELOPE_MALFORMED, "信封魔数不匹配")
    key_version = struct.unpack(">H", blob[3:5])[0]
    nonce = blob[5 : 5 + NONCE_LEN]
    ciphertext_and_tag = blob[5 + NONCE_LEN :]
    return key_version, nonce, ciphertext_and_tag
