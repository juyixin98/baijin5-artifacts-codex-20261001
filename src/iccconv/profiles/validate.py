"""ICC profile validation.

Profiles are validated *before* any transform is built.  A profile that
cannot be parsed, that belongs to a non-device class (device links,
abstract, named-color, colorspace-conversion profiles) or that addresses
an unsupported colorspace is rejected with a typed error; the caller
decides whether the rejection is fatal for the request.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass

from PIL import ImageCms

from ..contract.enums import ColorSpace
from ..errors import InvalidProfileError, ProfileRoleMismatchError

# Device profiles only.  link (device link), abst (abstract), spac
# (colorspace conversion) and nmcl (named color) profiles cannot be used
# as source/target of a document conversion in this service.
ALLOWED_DEVICE_CLASSES = {"mntr", "scnr", "prtr"}
ALLOWED_PCS = {"XYZ", "Lab"}

_COLOR_SPACE_BY_TAG = {
    "RGB": ColorSpace.RGB,
    "GRAY": ColorSpace.GRAY,
    "CMYK": ColorSpace.CMYK,
}


@dataclass(frozen=True)
class ProfileInfo:
    """Validated, redacted facts about an ICC profile.

    Contains digests and header metadata only - never the profile bytes,
    so it is safe to log and to return in API responses.
    """

    sha256: str
    description: str
    device_class: str
    color_space: ColorSpace
    pcs: str
    icc_version: str
    is_matrix_shaper: bool
    size_bytes: int
    source: str

    def redacted(self) -> dict:
        return {
            "sha256_16": self.sha256[:16],
            "description": self.description,
            "device_class": self.device_class,
            "color_space": self.color_space.value,
            "pcs": self.pcs,
            "size_bytes": self.size_bytes,
            "source": self.source,
        }


def validate_profile(data: bytes, *, source: str = "inline") -> ProfileInfo:
    """Parse and validate an ICC profile, returning its redacted facts."""
    if not data:
        raise InvalidProfileError("profile is empty", detail={"source": source})
    try:
        handle = ImageCms.getOpenProfile(io.BytesIO(data))
        core = handle.profile
        device_class = str(core.device_class).strip()
        color_space_tag = str(core.xcolor_space).strip()
        pcs = str(core.connection_space).strip()
        description = str(ImageCms.getProfileDescription(handle)).strip()
        icc_version = str(core.icc_version)
        is_matrix_shaper = bool(core.is_matrix_shaper)
    except InvalidProfileError:
        raise
    except Exception as exc:  # littleCMS raises several exception types
        raise InvalidProfileError(
            "profile cannot be parsed by the ICC engine",
            detail={"source": source, "engine_error": str(exc)[:200]},
        ) from exc

    if device_class not in ALLOWED_DEVICE_CLASSES:
        raise InvalidProfileError(
            "profile device class is not usable as a conversion endpoint",
            detail={
                "source": source,
                "device_class": device_class,
                "allowed": sorted(ALLOWED_DEVICE_CLASSES),
            },
        )
    color_space = _COLOR_SPACE_BY_TAG.get(color_space_tag)
    if color_space is None:
        raise InvalidProfileError(
            "profile colorspace is not supported by this service",
            detail={
                "source": source,
                "color_space": color_space_tag,
                "supported": sorted(_COLOR_SPACE_BY_TAG),
            },
        )
    if pcs not in ALLOWED_PCS:
        raise InvalidProfileError(
            "profile connection space is not XYZ or Lab",
            detail={"source": source, "pcs": pcs},
        )
    return ProfileInfo(
        sha256=hashlib.sha256(data).hexdigest(),
        description=description,
        device_class=device_class,
        color_space=color_space,
        pcs=pcs,
        icc_version=icc_version,
        is_matrix_shaper=is_matrix_shaper,
        size_bytes=len(data),
        source=source,
    )


def ensure_role(info: ProfileInfo, expected: ColorSpace, *, role: str) -> None:
    """Check that a validated profile matches the colorspace of its role."""
    if info.color_space is not expected:
        raise ProfileRoleMismatchError(
            f"{role} profile colorspace does not match the {role} data",
            detail={
                "role": role,
                "profile_colorspace": info.color_space.value,
                "expected_colorspace": expected.value,
                "profile_sha256_16": info.sha256[:16],
            },
        )
