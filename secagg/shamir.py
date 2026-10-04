"""Shamir 门限秘密分享,素数域 GF(p),p = 2^521 - 1 (Mersenne 素数).

秘密为任意 <= 32 字节串(如 X25519 私钥/掩码种子),按大端整数嵌入域中。
份额为 (index, value),index 从 1 开始;value 序列化为定长 66 字节。

教学实现说明:系数取自 secrets 模块的 CSPRNG;拉格朗日插值在 x=0 处求值。
份额数与门限受 MAX_SHARES 限制,超限抛 RESOURCE_EXHAUSTED。
"""

from __future__ import annotations

import secrets

from .errors import computation_failure, input_error, resource_exhausted

PRIME: int = (1 << 521) - 1
SHARE_BYTES: int = 66  # ceil(521 / 8) + 1 余量,定长便于传输
MAX_SECRET_BYTES: int = 64
MAX_SHARES: int = 64


def _int_to_bytes(v: int) -> bytes:
    return v.to_bytes(SHARE_BYTES, "big")


def _bytes_to_int(b: bytes) -> int:
    return int.from_bytes(b, "big")


def split_secret(secret: bytes, threshold: int, n: int) -> list[tuple[int, bytes]]:
    """把 secret 拆成 n 份,任意 threshold 份可重建。"""
    if not secret or len(secret) > MAX_SECRET_BYTES:
        raise input_error(
            "bad_secret", "秘密为空或超过长度上限", length=len(secret)
        )
    if not 1 <= threshold <= n:
        raise input_error(
            "bad_threshold", "门限必须满足 1 <= t <= n", threshold=threshold, n=n
        )
    if n > MAX_SHARES:
        raise resource_exhausted(
            "too_many_shares", "份额数超过上限", n=n, max_shares=MAX_SHARES
        )
    s = _bytes_to_int(secret)
    if s >= PRIME:
        raise input_error("secret_too_large", "秘密超出域范围")

    coeffs = [s] + [secrets.randbelow(PRIME) for _ in range(threshold - 1)]
    shares: list[tuple[int, bytes]] = []
    for x in range(1, n + 1):
        y = 0
        for c in reversed(coeffs):  # Horner
            y = (y * x + c) % PRIME
        shares.append((x, _int_to_bytes(y)))
    return shares


def _parse_points(shares: list[tuple[int, bytes]]) -> list[tuple[int, int]]:
    if not shares:
        raise computation_failure("no_shares", "没有任何份额,无法重建")
    points: list[tuple[int, int]] = []
    seen: set[int] = set()
    for idx, raw in shares:
        if not isinstance(idx, int) or idx < 1:
            raise input_error("bad_share_index", "份额索引必须为正整数", index=idx)
        if idx in seen:
            raise input_error("dup_share_index", "份额索引重复", index=idx)
        seen.add(idx)
        if len(raw) != SHARE_BYTES:
            raise input_error(
                "bad_share_len", "份额长度不符", index=idx, length=len(raw)
            )
        points.append((idx, _bytes_to_int(raw)))
    return points


def reconstruct_secret(
    shares: list[tuple[int, bytes]],
    expected_len: int | None = None,
) -> bytes:
    """由 >= threshold 份份额重建秘密。份额不足/无效抛 COMPUTATION_FAILURE。

    expected_len 给定且秘密以零字节开头时, 左侧补零到定长
    (否则前导零会丢失, 例如 32 字节私钥以 0x00 开头的情况);
    重建值放不进 expected_len 时判定份额不一致, 抛 COMPUTATION_FAILURE。
    """
    points = _parse_points(shares)

    # 拉格朗日插值在 x=0:secret = Σ y_i * Π_{j≠i} x_j/(x_j - x_i)
    secret = 0
    for i, (xi, yi) in enumerate(points):
        num, den = 1, 1
        for j, (xj, _) in enumerate(points):
            if i == j:
                continue
            num = (num * xj) % PRIME
            den = (den * (xj - xi)) % PRIME
        secret = (secret + yi * num * pow(den, -1, PRIME)) % PRIME

    if expected_len is not None:
        if expected_len < 1 or expected_len > MAX_SECRET_BYTES:
            raise input_error(
                "bad_expected_len", "期望秘密长度超出允许范围",
                expected_len=expected_len)
        if secret >= 1 << (8 * expected_len):
            raise computation_failure(
                "reconstruct_invalid",
                "重建结果超出期望秘密长度,份额可能不一致",
                expected_len=expected_len)
        return secret.to_bytes(expected_len, "big")

    out = _int_to_bytes(secret).lstrip(b"\x00")
    if len(out) > MAX_SECRET_BYTES:
        raise computation_failure(
            "reconstruct_invalid", "重建结果超出合法秘密长度,份额可能不一致"
        )
    return out
