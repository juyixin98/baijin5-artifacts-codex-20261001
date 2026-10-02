"""图像数据契约。

全系统统一的数据形状约定：
- 像素网格为单通道 float32，形状 (height, width)，行优先。
- 层级 L 的尺寸为 ceil(size_0 / 2**L)，奇数尺寸边界不丢行列。
- 层级 L 像素 (row, col) 的中心映射到原图坐标
  ((row + 0.5) * 2**L - 0.5, (col + 0.5) * 2**L - 0.5)，该映射固定不变。
- 瓦片按 tile_size 规则网格切分，边缘瓦片允许小于 tile_size。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .errors import InputError

DTYPE = "float32"

GENERATOR_KINDS = ("checkerboard", "diagonal", "gradient", "noise")


@dataclass(frozen=True)
class GeneratorSpec:
    """合成图像生成规格。"""

    kind: str
    width: int
    height: int
    params: dict = field(default_factory=dict)

    def validate(self, *, max_image_pixels: int) -> None:
        from .errors import ResourceExhaustedError

        if self.kind not in GENERATOR_KINDS:
            raise InputError(
                f"unknown generator {self.kind!r}; expected one of {GENERATOR_KINDS}"
            )
        if self.width < 1 or self.height < 1:
            raise InputError(
                f"image dimensions must be >= 1, got {self.width}x{self.height}"
            )
        if self.width * self.height > max_image_pixels:
            raise ResourceExhaustedError(
                f"image {self.width}x{self.height} exceeds max_image_pixels={max_image_pixels}"
            )


@dataclass(frozen=True)
class LevelMeta:
    """单层级元数据（随瓦片内容一起原子发布）。"""

    level: int
    width: int
    height: int
    tile_size: int
    tiles_x: int
    tiles_y: int

    @property
    def scale(self) -> int:
        return 1 << self.level

    def to_dict(self) -> dict:
        return {
            "level": self.level,
            "width": self.width,
            "height": self.height,
            "tile_size": self.tile_size,
            "tiles_x": self.tiles_x,
            "tiles_y": self.tiles_y,
        }

    @staticmethod
    def from_dict(d: dict) -> "LevelMeta":
        return LevelMeta(
            level=int(d["level"]),
            width=int(d["width"]),
            height=int(d["height"]),
            tile_size=int(d["tile_size"]),
            tiles_x=int(d["tiles_x"]),
            tiles_y=int(d["tiles_y"]),
        )


@dataclass(frozen=True)
class ImageMeta:
    """图像级元数据，在所有层级发布完成后最后落盘（提交点）。"""

    image_id: str
    width: int
    height: int
    tile_size: int
    levels: tuple[LevelMeta, ...]
    dtype: str = DTYPE

    def to_dict(self) -> dict:
        return {
            "image_id": self.image_id,
            "width": self.width,
            "height": self.height,
            "tile_size": self.tile_size,
            "dtype": self.dtype,
            "levels": [lm.to_dict() for lm in self.levels],
        }

    @staticmethod
    def from_dict(d: dict) -> "ImageMeta":
        return ImageMeta(
            image_id=str(d["image_id"]),
            width=int(d["width"]),
            height=int(d["height"]),
            tile_size=int(d["tile_size"]),
            dtype=str(d.get("dtype", DTYPE)),
            levels=tuple(LevelMeta.from_dict(x) for x in d["levels"]),
        )

    def level_meta(self, level: int) -> LevelMeta | None:
        for lm in self.levels:
            if lm.level == level:
                return lm
        return None


@dataclass(frozen=True)
class RegionRequest:
    """区域查询请求，坐标与尺寸均在目标层级像素网格内。"""

    level: int
    x: int
    y: int
    width: int
    height: int

    def validate_against(self, level_meta: LevelMeta) -> None:
        if self.width < 1 or self.height < 1:
            raise InputError(
                f"region width/height must be >= 1, got {self.width}x{self.height}"
            )
        if self.x < 0 or self.y < 0:
            raise InputError(f"region origin must be >= 0, got ({self.x}, {self.y})")
        if self.x + self.width > level_meta.width or self.y + self.height > level_meta.height:
            raise InputError(
                f"region (x={self.x}, y={self.y}, w={self.width}, h={self.height}) "
                f"exceeds level {self.level} bounds {level_meta.width}x{level_meta.height}"
            )
