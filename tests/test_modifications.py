"""Tests for modification resolution and terminus-specific placement.

These cover orchestration-level rules that the pure digest engine assumes its
caller has already enforced.
"""

from __future__ import annotations

import pytest

from app.api.schemas import DigestRequest, ModificationSpec
from app.domain.constants import MODIFICATIONS
from app.domain.errors import DigestError, ErrorCode
from app.services.orchestrator import run_digest
from app.services.parse import parse_sequence

pytestmark = pytest.mark.integration


def test_nterminal_mod_applies_only_to_nterminal_fragment(settings, store) -> None:
    request = DigestRequest(
        sequence="AK",
        enzyme="trypsin",
        fixed_modifications=[ModificationSpec(key="acetyl_nterm", kind="fixed")],
    )
    response = run_digest(request, settings=settings, store=store)
    physical = response["fragments"][0]
    empty_c = response["fragments"][1]
    acetyl = MODIFICATIONS["acetyl_nterm"].delta
    assert physical["modification_forms"][0]["nterm_mod"] == "acetyl_nterm"
    assert physical["modification_forms"][0]["delta"] == pytest.approx(acetyl, abs=1e-6)
    # C-terminal empty segment is not N-terminal: no terminal mod.
    assert empty_c["modification_forms"] == []


def test_terminal_mod_on_internal_fragment_is_absent(settings, store) -> None:
    request = DigestRequest(
        sequence="AAKAAAKAA",
        enzyme="trypsin",
        fixed_modifications=[ModificationSpec(key="acetyl_nterm", kind="fixed")],
    )
    response = run_digest(request, settings=settings, store=store)
    internal = response["fragments"][1]  # AAAK, not N-terminal
    assert internal["modification_forms"][0]["nterm_mod"] is None
    assert internal["modification_forms"][0]["delta"] == 0.0


def test_fixed_mod_targeting_ambiguous_residue_is_rejected(settings, store) -> None:
    # X could be cysteine; a fixed C modification cannot be placed certainly.
    request = DigestRequest(
        sequence="AXG",
        enzyme="cnbr",
        fixed_modifications=[
            ModificationSpec(key="carbamidomethyl_c", kind="fixed")
        ],
    )
    with pytest.raises(DigestError) as exc:
        run_digest(request, settings=settings, store=store)
    assert exc.value.code is ErrorCode.MOD_TARGET_UNKNOWN_RESIDUE


def test_unknown_modification_key_is_rejected(settings, store) -> None:
    request = DigestRequest(
        sequence="AK",
        enzyme="trypsin",
        fixed_modifications=[ModificationSpec(key="nope", kind="fixed")],
    )
    with pytest.raises(DigestError) as exc:
        run_digest(request, settings=settings, store=store)
    assert exc.value.code is ErrorCode.INVALID_VARIABLE_MOD


def test_mod_form_limit_is_enforced(settings, store) -> None:
    settings.max_modification_forms = 2
    request = DigestRequest(
        sequence="MM",
        enzyme="chymotrypsin",  # no cleavage, one fragment
        variable_modifications=[ModificationSpec(key="oxidation_m", kind="variable")],
    )
    with pytest.raises(DigestError) as exc:
        run_digest(request, settings=settings, store=store)
    assert exc.value.code is ErrorCode.MOD_FORM_LIMIT


def test_missed_cleavages_out_of_range_is_rejected(settings, store) -> None:
    request = DigestRequest(
        sequence="AK", enzyme="trypsin", missed_cleavages=10_000
    )
    with pytest.raises(DigestError) as exc:
        run_digest(request, settings=settings, store=store)
    assert exc.value.code is ErrorCode.INVALID_MISSED_CLEAVAGES


def test_custom_rule_rejects_unsupported_residue(settings, store) -> None:
    request = DigestRequest(
        sequence="AK",
        enzyme="custom",
        custom_rule={"cleave_after": "B"},  # ambiguous letter not allowed in a rule
    )
    with pytest.raises(DigestError) as exc:
        run_digest(request, settings=settings, store=store)
    assert exc.value.code is ErrorCode.INVALID_CUSTOM_RULE


def test_custom_rule_requires_a_cleavage_side(settings, store) -> None:
    request = DigestRequest(
        sequence="AK", enzyme="custom", custom_rule={"cleave_after": "", "cleave_before": ""}
    )
    with pytest.raises(DigestError) as exc:
        run_digest(request, settings=settings, store=store)
    assert exc.value.code is ErrorCode.INVALID_CUSTOM_RULE


def test_resolved_mods_partition_fixed_and_variable() -> None:
    from app.services.orchestrator import resolve_modifications

    parsed = parse_sequence("CM", max_length=100)
    specs = [
        ModificationSpec(key="carbamidomethyl_c", kind="fixed"),
        ModificationSpec(key="oxidation_m", kind="variable"),
    ]
    fixed, variable = resolve_modifications(specs, parsed)
    assert [m.key for m in fixed] == ["carbamidomethyl_c"]
    assert [m.key for m in variable] == ["oxidation_m"]


def test_cterminal_variable_mod_binary_on_terminal_fragment(settings, store) -> None:
    # No acetyl C-term preset exists, so build via the N preset semantics is
    # not possible; assert terminal variable option enumeration using N-term
    # variable acetylation on the N-terminal whole fragment.
    request = DigestRequest(
        sequence="AAAA",
        enzyme="trypsin",  # no cuts -> single N-and-C terminal fragment
        variable_modifications=[
            ModificationSpec(key="acetyl_nterm", kind="variable")
        ],
    )
    response = run_digest(request, settings=settings, store=store)
    forms = response["fragments"][0]["modification_forms"]
    # one binary terminal choice => two forms: unmodified and acetylated
    assert len(forms) == 2
    assert forms[0]["delta"] == 0.0 and forms[0]["nterm_mod"] is None
    acetyl = MODIFICATIONS["acetyl_nterm"].delta
    assert forms[1]["delta"] == pytest.approx(acetyl, abs=1e-6)
    assert forms[1]["nterm_mod"] == "acetyl_nterm"


def test_cterminal_fixed_amidation_applies_only_to_cterminal(settings, store) -> None:
    request = DigestRequest(
        sequence="AAKAA",
        enzyme="trypsin",  # cuts after K -> AAK (N-term) and AA (C-term)
        fixed_modifications=[ModificationSpec(key="amide_cterm", kind="fixed")],
    )
    response = run_digest(request, settings=settings, store=store)
    amide = MODIFICATIONS["amide_cterm"].delta
    # The physical C-terminal fragment is AA (second fragment; last is empty).
    aa = next(
        f for f in response["fragments"] if f["sequence"] == "AA"
    )
    assert aa["c_terminal"] is True
    assert aa["modification_forms"][0]["cterm_mod"] == "amide_cterm"
    assert aa["modification_forms"][0]["delta"] == pytest.approx(amide, abs=1e-6)
    # N-terminal fragment is not amidated.
    aak = next(f for f in response["fragments"] if f["sequence"] == "AAK")
    assert aak["modification_forms"][0]["cterm_mod"] is None
