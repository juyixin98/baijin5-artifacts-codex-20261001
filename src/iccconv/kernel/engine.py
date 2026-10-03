"""Conversion orchestration.

``convert_document`` wires the pieces together:

    resolve + validate profiles -> check roles -> split/unpremultiply alpha
    -> engine transform on color channels -> optional gamut estimate
    -> re-attach/re-premultiply alpha -> result + metadata

It never guesses a profile: if the document carries no embedded profile
and no explicit source profile is supplied, the request is rejected.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..contract.enums import AlphaMode, RenderingIntent
from ..contract.image import ImageDocument
from ..errors import MissingProfileError
from ..profiles.validate import ProfileInfo, ensure_role, validate_profile
from .alpha import join_alpha, premultiply, split_alpha, unpremultiply
from .gamut import GamutReport, estimate_gamut
from .transform import apply_transform, build_transform, check_intent_supported

_NO_LOSSLESS_NOTE = (
    "cross-profile conversion is never lossless across different gamuts; "
    "results are gamut-mapped by the selected rendering intent"
)


class DiagnosticsSink(Protocol):
    def record(self, step: str, outcome: str, message: str, **state: object) -> None: ...


@dataclass(frozen=True)
class ConversionMetadata:
    rendering_intent: str
    black_point_compensation: bool
    source_profile: dict
    target_profile: dict
    lossless: bool
    gamut_note: str


@dataclass(frozen=True)
class ConversionResult:
    document: ImageDocument
    metadata: ConversionMetadata
    gamut: GamutReport | None


def _resolve_source(
    doc: ImageDocument, source: tuple[bytes, ProfileInfo] | None
) -> tuple[bytes, ProfileInfo]:
    if source is not None:
        return source
    if doc.embedded_profile:
        return doc.embedded_profile, validate_profile(doc.embedded_profile, source="embedded")
    raise MissingProfileError(
        "image has no embedded ICC profile and no explicit source profile was "
        "supplied; provide one explicitly - a default such as sRGB is never assumed"
    )


def convert_document(
    doc: ImageDocument,
    *,
    source: tuple[bytes, ProfileInfo] | None,
    target: tuple[bytes, ProfileInfo],
    intent: RenderingIntent = RenderingIntent.PERCEPTUAL,
    black_point_compensation: bool = False,
    alpha_mode_out: AlphaMode | None = None,
    check_gamut: bool = False,
    gamut_tolerance: int = 3,
    diagnostics: DiagnosticsSink | None = None,
) -> ConversionResult:
    src_bytes, src_info = _resolve_source(doc, source)
    dst_bytes, dst_info = target
    if diagnostics:
        diagnostics.record(
            "profiles", "accepted", "source and target profiles validated",
            source=src_info.redacted(), target=dst_info.redacted(),
        )

    ensure_role(src_info, doc.color_space, role="source")
    out_alpha_mode = alpha_mode_out if alpha_mode_out is not None else doc.alpha_mode
    if out_alpha_mode is not AlphaMode.NONE and not doc.has_alpha:
        out_alpha_mode = doc.alpha_mode  # nothing to attach; keep NONE

    check_intent_supported(src_bytes, dst_bytes, intent)

    color, alpha_channel = split_alpha(doc.pixels, doc.color_space, doc.alpha_mode)
    if doc.alpha_mode is AlphaMode.PREMULTIPLIED and alpha_channel is not None:
        color = unpremultiply(color, alpha_channel)
        if diagnostics:
            diagnostics.record("alpha", "accepted", "premultiplied input unpremultiplied")

    transform = build_transform(
        src_bytes, dst_bytes, src_info.color_space, dst_info.color_space,
        intent, black_point_compensation,
    )
    out_color = apply_transform(transform, color, src_info.color_space)
    if diagnostics:
        diagnostics.record(
            "transform", "accepted", "color channels converted",
            intent=intent.name, black_point_compensation=black_point_compensation,
        )

    gamut_report: GamutReport | None = None
    if check_gamut:
        gamut_report = estimate_gamut(
            color, src_bytes, dst_bytes, src_info.color_space, dst_info.color_space,
            intent, black_point_compensation, gamut_tolerance,
        )
        if diagnostics:
            diagnostics.record(
                "gamut", "undetermined",
                "out-of-gamut estimate is a round-trip heuristic, not a proof",
                flagged_pixels=gamut_report.flagged_pixels,
                total_pixels=gamut_report.total_pixels,
                tolerance=gamut_report.tolerance,
            )

    if out_alpha_mode is AlphaMode.PREMULTIPLIED and alpha_channel is not None:
        out_color = premultiply(out_color, alpha_channel)
    out_pixels = join_alpha(out_color, alpha_channel if doc.has_alpha else None)

    out_doc = ImageDocument(
        pixels=out_pixels,
        color_space=dst_info.color_space,
        alpha_mode=out_alpha_mode,
        embedded_profile=dst_bytes,
    )
    metadata = ConversionMetadata(
        rendering_intent=intent.name,
        black_point_compensation=black_point_compensation,
        source_profile=src_info.redacted(),
        target_profile=dst_info.redacted(),
        lossless=False,
        gamut_note=_NO_LOSSLESS_NOTE,
    )
    return ConversionResult(document=out_doc, metadata=metadata, gamut=gamut_report)
