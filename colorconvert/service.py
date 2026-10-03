"""Conversion service: orchestrates validate -> plan -> execute -> record.

The service owns the decision pipeline.  It never guesses: a missing source
profile makes the request *undecidable* (we refuse to assume sRGB), and every
outcome is recorded on the job and logged with the request id.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from .config import Settings
from .contract import ColorMode, ImageData
from .diagnostics import RequestLogger
from .errors import ConversionError, FailureCategory
from .jobs import Decision, JobRecord, JobStatus, JobStore
from .kernel import ColorKernel, RenderingIntent
from .profiles import ROLE_SOURCE, ROLE_TARGET, ProfileRegistry
from .tiles import run_tiled

EMBEDDED = "embedded"


@dataclass
class ConversionRequest:
    image: ImageData
    source_profile: str  # registry id or "embedded"
    target_profile: str  # registry id
    rendering_intent: RenderingIntent = RenderingIntent.RELATIVE_COLORIMETRIC
    black_point_compensation: bool = False
    tile_size: int = 512
    allow_cmyk: bool = False
    embedded_profile: bytes | None = None


@dataclass
class ConversionOutcome:
    job: JobRecord
    result: ImageData | None


class ConversionService:
    def __init__(
        self,
        registry: ProfileRegistry,
        settings: Settings | None = None,
        job_store: JobStore | None = None,
    ) -> None:
        self.registry = registry
        self.settings = settings or Settings()
        self.jobs = job_store or JobStore()
        self.kernel = ColorKernel()

    # -- validation ------------------------------------------------------

    def _check(self, request: ConversionRequest):
        """Validate the request; return (src, dst) profile handles.

        Raises ConversionError on the first hard rejection.
        """
        s = self.settings
        request.image.validate()

        if request.image.pixels > s.max_pixels:
            raise ConversionError(
                FailureCategory.LIMIT_EXCEEDED,
                f"image has {request.image.pixels} pixels, limit is "
                f"{s.max_pixels}",
            )
        if not (s.min_tile_size <= request.tile_size <= s.max_tile_size):
            raise ConversionError(
                FailureCategory.CONTRACT_VIOLATION,
                f"tile_size {request.tile_size} outside "
                f"[{s.min_tile_size}, {s.max_tile_size}]",
            )

        if request.source_profile == EMBEDDED:
            if not request.embedded_profile:
                raise ConversionError(
                    FailureCategory.EMBEDDED_PROFILE_ABSENT,
                    "source_profile='embedded' but the image carries no "
                    "embedded ICC profile; refusing to guess a colorspace",
                )
            src = self.registry.from_embedded(
                request.embedded_profile, request.image.mode
            )
        else:
            src = self.registry.get(request.source_profile, role=ROLE_SOURCE)
            if src.color_mode is not request.image.mode:
                raise ConversionError(
                    FailureCategory.PROFILE_COLORSPACE_MISMATCH,
                    f"source profile {src.profile_id!r} is "
                    f"{src.color_space} but image mode is "
                    f"{request.image.mode.name}",
                )

        dst = self.registry.get(request.target_profile, role=ROLE_TARGET)

        if (src.cmyk_restricted or dst.cmyk_restricted) and not request.allow_cmyk:
            raise ConversionError(
                FailureCategory.CMYK_RESTRICTED,
                "CMYK conversion requires allow_cmyk=true (restricted mode: "
                "8-bit, registry-pinned CMYK profiles only)",
            )
        return src, dst

    # -- execution -------------------------------------------------------

    def validate(
        self, request: ConversionRequest, log: RequestLogger
    ) -> JobRecord:
        """Dry-run: record whether the request would be accepted."""
        job = self.jobs.new_record(log.request_id)
        try:
            self._check(request)
        except ConversionError as exc:
            undecidable = (
                exc.category is FailureCategory.EMBEDDED_PROFILE_ABSENT
            )
            job.status = (
                JobStatus.UNDECIDABLE if undecidable else JobStatus.REJECTED
            )
            job.decisions.append(
                Decision(
                    outcome=job.status.value,
                    reason=exc.message,
                    category=exc.category.value,
                )
            )
            log.decision(
                job.status.value, exc.message,
                category=exc.category.value, job_id=job.job_id,
            )
            return job
        job.status = JobStatus.ACCEPTED
        job.decisions.append(
            Decision(outcome="accepted", reason="contract and profiles valid")
        )
        log.decision("accepted", "contract and profiles valid",
                     job_id=job.job_id)
        return job

    def convert(
        self, request: ConversionRequest, log: RequestLogger
    ) -> ConversionOutcome:
        job = self.jobs.new_record(log.request_id)
        try:
            src, dst = self._check(request)
        except ConversionError as exc:
            undecidable = (
                exc.category is FailureCategory.EMBEDDED_PROFILE_ABSENT
            )
            job.status = (
                JobStatus.UNDECIDABLE if undecidable else JobStatus.REJECTED
            )
            job.decisions.append(
                Decision(
                    outcome=job.status.value,
                    reason=exc.message,
                    category=exc.category.value,
                )
            )
            log.decision(
                job.status.value,
                exc.message,
                category=exc.category.value,
                job_id=job.job_id,
                image_shape=tuple(request.image.color.shape)
                if hasattr(request.image.color, "shape")
                else None,
                mode=request.image.mode.name,
            )
            return ConversionOutcome(job=job, result=None)

        job.decisions.append(
            Decision(outcome="accepted", reason="contract and profiles valid")
        )
        log.decision(
            "accepted",
            "contract and profiles valid",
            job_id=job.job_id,
            source=src.profile_id,
            target=dst.profile_id,
            intent=request.rendering_intent.name.lower(),
            bpc=request.black_point_compensation,
        )

        started = time.perf_counter()
        try:
            # Alpha semantics are applied once, up front: the kernel converts
            # non-premultiplied color, so we normalize here and let the tiled
            # runner move plain color planes.
            from .kernel import _premultiply, _unpremultiply, build_report

            work = request.image
            repremultiply = False
            if work.alpha is not None and work.premultiplied:
                work = ImageData(
                    color=_unpremultiply(work.color, work.alpha),
                    mode=work.mode,
                    alpha=work.alpha,
                )
                repremultiply = True

            def convert_chunk(chunk):
                return self.kernel.convert_color_array(
                    chunk,
                    src,
                    dst,
                    request.rendering_intent,
                    request.black_point_compensation,
                )

            tiled, records = run_tiled(work, request.tile_size, convert_chunk)

            color = tiled.color
            # re-apply premultiplication against the original alpha
            if repremultiply:
                color = _premultiply(color, work.alpha)

            result = ImageData(
                color=color,
                mode=dst.color_mode,
                alpha=work.alpha,
                premultiplied=request.image.premultiplied,
            )
            report = build_report(
                src, dst, request.rendering_intent,
                request.black_point_compensation,
            )
        except ConversionError as exc:
            job.status = JobStatus.FAILED
            job.decisions.append(
                Decision(
                    outcome="failed",
                    reason=exc.message,
                    category=exc.category.value,
                )
            )
            log.error(
                "conversion failed",
                job_id=job.job_id,
                category=exc.category.value,
                reason=exc.message,
            )
            return ConversionOutcome(job=job, result=None)

        elapsed_ms = (time.perf_counter() - started) * 1000.0
        job.status = JobStatus.COMPLETED
        job.result = result
        job.report = {
            **report.__dict__,
            "tiles": len(records),
            "tile_size": request.tile_size,
            "elapsed_ms": round(elapsed_ms, 3),
            "pixels": request.image.pixels,
            "alpha": request.image.alpha is not None,
            "premultiplied": request.image.premultiplied,
        }
        log.info(
            "conversion completed",
            job_id=job.job_id,
            tiles=len(records),
            elapsed_ms=round(elapsed_ms, 3),
            lossy=report.lossy,
        )
        return ConversionOutcome(job=job, result=result)
