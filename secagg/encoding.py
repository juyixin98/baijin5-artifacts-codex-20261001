"""协议编码:定点数 -> Z_R 环元素,模数与范围防溢出.

模数 R = 2^64(与 AES-CTR 掩码输出的 uint64 天然对齐)。
浮点输入按 SCALE 缩放为整数后映射进 Z_R:
    encode(x) = round(x * SCALE) mod R
负数按补码表示(R + v)。解码时按最近原点还原符号。

防溢出约束(建会话时校验,逐消息强制):
    |round(x * SCALE)| <= elem_bound            (单元素)
    max_clients * elem_bound <= R // 2 - 1      (求和不回绕)
满足上式时,n 个编码元素之和的绝对值 < R/2,模加不会跨越符号边界,
解码结果与明文整数和一致。
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import input_error, resource_exhausted

MODULUS: int = 1 << 64
HALF_MODULUS: int = MODULUS // 2
DEFAULT_SCALE: int = 1 << 20  # 定点缩放因子,约 6 位十进制小数精度


@dataclass(frozen=True)
class EncodingParams:
    """一次聚合运行的编码参数。max_clients 参与溢出上界校验。"""

    scale: int = DEFAULT_SCALE
    elem_bound: int = (1 << 40)  # 缩放后单元素绝对值上限
    max_clients: int = 16
    max_vector_len: int = 1 << 16

    def validate(self) -> "EncodingParams":
        if self.scale <= 0:
            raise input_error("bad_scale", "scale 必须为正整数", scale=self.scale)
        if self.elem_bound <= 0:
            raise input_error(
                "bad_elem_bound", "elem_bound 必须为正整数", elem_bound=self.elem_bound
            )
        if self.max_clients <= 0:
            raise input_error(
                "bad_max_clients", "max_clients 必须为正整数",
                max_clients=self.max_clients,
            )
        # 求和防溢出:n 个元素绝对值之和必须严格小于 R/2
        if self.max_clients * self.elem_bound > HALF_MODULUS - 1:
            raise resource_exhausted(
                "overflow_risk",
                "max_clients * elem_bound 超过 R/2-1,求和可能回绕",
                max_clients=self.max_clients,
                elem_bound=self.elem_bound,
                half_modulus=HALF_MODULUS,
            )
        return self


def encode_scalar(value: float, params: EncodingParams) -> int:
    """单个浮点数 -> Z_R 元素。越界抛 INPUT_ERROR。"""
    scaled = round(value * params.scale)
    if abs(scaled) > params.elem_bound:
        raise input_error(
            "elem_out_of_range",
            "元素缩放后超出 elem_bound,拒绝编码以防溢出",
            value=value,
            scaled=scaled,
            elem_bound=params.elem_bound,
        )
    return scaled % MODULUS


def decode_scalar(element: int, params: EncodingParams) -> float:
    """Z_R 元素 -> 浮点数。按最近原点还原符号。"""
    if not 0 <= element < MODULUS:
        raise input_error(
            "elem_not_in_ring", "元素不在 Z_R 内", element=element, modulus=MODULUS
        )
    v = element if element < HALF_MODULUS else element - MODULUS
    return v / params.scale


def encode_vector(values: list[float], params: EncodingParams) -> list[int]:
    if len(values) > params.max_vector_len:
        raise resource_exhausted(
            "vector_too_long",
            "向量长度超过上限",
            length=len(values),
            max_vector_len=params.max_vector_len,
        )
    return [encode_scalar(v, params) for v in values]


def decode_vector(elements: list[int], params: EncodingParams) -> list[float]:
    if len(elements) > params.max_vector_len:
        raise resource_exhausted(
            "vector_too_long",
            "向量长度超过上限",
            length=len(elements),
            max_vector_len=params.max_vector_len,
        )
    return [decode_scalar(e, params) for e in elements]


def add_vectors_mod(a: list[int], b: list[int]) -> list[int]:
    """Z_R 内向量逐元素模加。长度不一致为输入错误。"""
    if len(a) != len(b):
        raise input_error(
            "vector_len_mismatch", "向量长度不一致", len_a=len(a), len_b=len(b)
        )
    return [(x + y) % MODULUS for x, y in zip(a, b)]


def sub_vectors_mod(a: list[int], b: list[int]) -> list[int]:
    if len(a) != len(b):
        raise input_error(
            "vector_len_mismatch", "向量长度不一致", len_a=len(a), len_b=len(b)
        )
    return [(x - y) % MODULUS for x, y in zip(a, b)]
