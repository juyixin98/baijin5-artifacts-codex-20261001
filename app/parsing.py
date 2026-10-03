"""Parse and validate synthetic variant/read payloads into domain objects.

Fixed, documented rules (see docs/usage.md):

* Allele strings are upper-cased and must be DNA (A/C/G/T/N).
* A variant's ref and alt must differ.
* A read observation whose allele equals ref maps to bit 0, alt to bit 1;
  anything else is an *unknown allele*: it is excluded from the MEC
  objective and counted in evidence (never silently re-interpreted).
* Quality -> cost: correcting an observation costs exactly its phred
  quality. Missing quality gets ``settings.default_quality``; qualities are
  clamped to [0, ``settings.max_quality``].
* A read may report at most one observation per variant; duplicates are
  rejected as INVALID_INPUT rather than arbitrarily merged.
"""

from __future__ import annotations

from app.config import Settings
from app.domain import Fragment, Observation, ParsedInput, Variant
from app.errors import FailureCategory, PhasingError
from app.models import PhaseRequest

DNA_ALPHABET = frozenset("ACGTN")


def _invalid(detail: str) -> PhasingError:
    return PhasingError(FailureCategory.INVALID_INPUT, detail)


def parse_variants(request: PhaseRequest) -> list[Variant]:
    seen_ids: set[str] = set()
    variants: list[Variant] = []
    for index, v in enumerate(request.variants):
        if v.id in seen_ids:
            raise _invalid(f"duplicate variant id {v.id!r}")
        seen_ids.add(v.id)
        ref = v.ref.upper()
        alt = v.alt.upper()
        if not set(ref) <= DNA_ALPHABET or not set(alt) <= DNA_ALPHABET:
            raise _invalid(
                f"variant {v.id!r}: ref/alt must be DNA bases (A/C/G/T/N), "
                f"got ref={v.ref!r} alt={v.alt!r}"
            )
        if ref == alt:
            raise _invalid(f"variant {v.id!r}: ref and alt are identical ({ref!r})")
        variants.append(Variant(index=index, id=v.id, chrom=v.chrom, pos=v.pos, ref=ref, alt=alt))
    return variants


def clamp_quality(quality: int | None, settings: Settings) -> tuple[float, bool]:
    """Return (cost, defaulted?). Cost rule: cost == phred quality, clamped."""
    if quality is None:
        return float(settings.default_quality), True
    return float(min(max(quality, 0), settings.max_quality)), False


def build_fragments(
    request: PhaseRequest, variants: list[Variant], settings: Settings
) -> ParsedInput:
    by_id = {v.id: v for v in variants}
    fragments: dict[str, Fragment] = {}
    seen_pairs: set[tuple[str, str]] = set()
    unknown = 0
    defaulted = 0

    for obs in request.reads:
        variant = by_id.get(obs.variant_id)
        if variant is None:
            raise _invalid(f"read {obs.read_id!r} references unknown variant {obs.variant_id!r}")
        pair = (obs.read_id, obs.variant_id)
        if pair in seen_pairs:
            raise _invalid(
                f"read {obs.read_id!r} reports variant {obs.variant_id!r} more than once"
            )
        seen_pairs.add(pair)

        allele = obs.allele.upper()
        if allele == variant.ref:
            bit = 0
        elif allele == variant.alt:
            bit = 1
        else:
            unknown += 1
            continue  # unknown allele: excluded from MEC, counted as evidence

        weight, was_defaulted = clamp_quality(obs.quality, settings)
        defaulted += int(was_defaulted)
        fragment = fragments.setdefault(obs.read_id, Fragment(read_id=obs.read_id))
        fragment.observations.append(
            Observation(site=variant.index, bit=bit, weight=weight)
        )

    usable = [f for f in fragments.values() if f.observations]
    for fragment in usable:
        fragment.observations.sort(key=lambda o: o.site)
    return ParsedInput(
        variants=variants,
        fragments=usable,
        unknown_allele_observations=unknown,
        defaulted_quality_observations=defaulted,
    )


def parse_request(request: PhaseRequest, settings: Settings) -> ParsedInput:
    variants = parse_variants(request)
    return build_fragments(request, variants, settings)
