"""ICC profile registry: loading, validation, and resolution.

Profiles are *explicit local dependencies*.  The registry file
(``profiles/REGISTRY.json``) pins every allowed profile by sha256; a profile
that is missing, corrupt, hash-mismatched, or used for a role/colorspace it
was not registered for is rejected with a specific
:class:`~colorconvert.errors.FailureCategory`.

There is deliberately **no** implicit sRGB fallback: callers must name a
source and a target profile, or explicitly request ``"embedded"``.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from PIL import ImageCms

from .contract import COLORSPACE_TO_MODE, ColorMode
from .errors import FailureCategory, ProfileError

ROLE_SOURCE = "source"
ROLE_TARGET = "target"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class ProfileHandle:
    """A validated ICC profile ready for the kernel."""

    profile_id: str
    sha256: str
    color_space: str  # ICC signature, e.g. "RGB", "GRAY", "CMYK", "Lab"
    device_class: str  # ICC device class, e.g. "mntr", "prtr", "spac"
    description: str
    roles: frozenset[str]
    cmyk_restricted: bool
    cms_profile: ImageCms.ImageCmsProfile = field(compare=False)
    origin: str = "registry"  # "registry" | "embedded"

    @property
    def color_mode(self) -> ColorMode:
        try:
            return COLORSPACE_TO_MODE[self.color_space]
        except KeyError as exc:
            raise ProfileError(
                FailureCategory.PROFILE_COLORSPACE_MISMATCH,
                f"unsupported profile colorspace {self.color_space!r} "
                f"for profile {self.profile_id!r}",
            ) from exc

    @property
    def fingerprint(self) -> str:
        return self.sha256[:12]


def _open_cms_profile(source, profile_id: str) -> ImageCms.ImageCmsProfile:
    try:
        return ImageCms.getOpenProfile(source)
    except Exception as exc:  # littlecms raises PyCMSError / OSError
        raise ProfileError(
            FailureCategory.PROFILE_CORRUPT,
            f"ICC engine could not open profile {profile_id!r}: {exc}",
        ) from exc


def _read_signature(cms_profile: ImageCms.ImageCmsProfile, profile_id: str):
    try:
        core = cms_profile.profile
        color_space = str(core.xcolor_space).strip()
        device_class = str(core.device_class).strip()
        description = str(core.profile_description).strip()
    except Exception as exc:
        raise ProfileError(
            FailureCategory.PROFILE_CORRUPT,
            f"could not read header of profile {profile_id!r}: {exc}",
        ) from exc
    return color_space, device_class, description


class ProfileRegistry:
    """Validated, hash-pinned set of named ICC profiles."""

    def __init__(self, profiles: dict[str, ProfileHandle]) -> None:
        self._profiles = dict(profiles)

    @classmethod
    def load(cls, registry_path: str | Path) -> "ProfileRegistry":
        registry_path = Path(registry_path)
        if not registry_path.is_file():
            raise ProfileError(
                FailureCategory.PROFILE_MISSING,
                f"profile registry not found: {registry_path}",
            )
        spec = json.loads(registry_path.read_text(encoding="utf-8"))
        base = registry_path.parent
        profiles: dict[str, ProfileHandle] = {}
        for entry in spec["profiles"]:
            profile_id = entry["id"]
            path = base / entry["file"]
            if not path.is_file():
                raise ProfileError(
                    FailureCategory.PROFILE_MISSING,
                    f"profile file missing for {profile_id!r}: {path}",
                    detail={"profile_id": profile_id},
                )
            digest = sha256_bytes(path.read_bytes())
            if digest != entry["sha256"]:
                raise ProfileError(
                    FailureCategory.PROFILE_HASH_MISMATCH,
                    f"sha256 mismatch for profile {profile_id!r}: file no "
                    f"longer matches the registry pin",
                    detail={
                        "profile_id": profile_id,
                        "expected": digest[:12],
                        "actual": entry["sha256"][:12],
                    },
                )
            cms_profile = _open_cms_profile(str(path), profile_id)
            color_space, device_class, description = _read_signature(
                cms_profile, profile_id
            )
            if color_space != entry["color_space"]:
                raise ProfileError(
                    FailureCategory.PROFILE_COLORSPACE_MISMATCH,
                    f"registry declares {entry['color_space']!r} but file "
                    f"reports {color_space!r} for profile {profile_id!r}",
                    detail={"profile_id": profile_id},
                )
            profiles[profile_id] = ProfileHandle(
                profile_id=profile_id,
                sha256=digest,
                color_space=color_space,
                device_class=device_class,
                description=description,
                roles=frozenset(entry["roles"]),
                cmyk_restricted=bool(entry.get("cmyk_restricted", False)),
                cms_profile=cms_profile,
            )
        return cls(profiles)

    def __contains__(self, profile_id: str) -> bool:
        return profile_id in self._profiles

    def __iter__(self):
        return iter(self._profiles.values())

    def get(self, profile_id: str, *, role: str) -> ProfileHandle:
        handle = self._profiles.get(profile_id)
        if handle is None:
            raise ProfileError(
                FailureCategory.PROFILE_MISSING,
                f"unknown profile id {profile_id!r}; it must be explicitly "
                f"registered (no implicit sRGB fallback)",
                detail={"profile_id": profile_id},
            )
        if role not in handle.roles:
            raise ProfileError(
                FailureCategory.PROFILE_ROLE_NOT_ALLOWED,
                f"profile {profile_id!r} is not registered for role "
                f"{role!r}",
                detail={"profile_id": profile_id, "role": role},
            )
        return handle

    def from_embedded(self, data: bytes, mode: ColorMode) -> ProfileHandle:
        """Validate caller-supplied embedded profile bytes.

        Embedded profiles are only accepted when the caller explicitly asked
        for ``source_profile="embedded"``; they are validated exactly like
        registry profiles (engine-openable, colorspace matches the image).
        """
        import io

        digest = sha256_bytes(data)
        profile_id = f"embedded:{digest[:12]}"
        cms_profile = _open_cms_profile(io.BytesIO(data), profile_id)
        color_space, device_class, description = _read_signature(
            cms_profile, profile_id
        )
        handle = ProfileHandle(
            profile_id=profile_id,
            sha256=digest,
            color_space=color_space,
            device_class=device_class,
            description=description,
            roles=frozenset({ROLE_SOURCE}),
            cmyk_restricted=color_space == "CMYK",
            cms_profile=cms_profile,
            origin="embedded",
        )
        if handle.color_space not in COLORSPACE_TO_MODE:
            raise ProfileError(
                FailureCategory.PROFILE_COLORSPACE_MISMATCH,
                f"embedded profile colorspace {color_space!r} is unsupported",
                detail={"profile_id": profile_id},
            )
        if COLORSPACE_TO_MODE[color_space] is not mode:
            raise ProfileError(
                FailureCategory.PROFILE_COLORSPACE_MISMATCH,
                f"embedded profile colorspace {color_space!r} does not match "
                f"image mode {mode.name}",
                detail={"profile_id": profile_id, "mode": mode.name},
            )
        return handle
