"""SASLprep (RFC 4013) tests using published-profile edge cases."""
from __future__ import annotations

import pytest

from scram_auth.saslprep import SaslprepError, sasl_prep


class TestSaslprep:
    def test_ascii_identity(self) -> None:
        assert sasl_prep("pencil") == "pencil"

    def test_maps_non_ascii_space_to_ascii_space(self) -> None:
        # U+00A0 NO-BREAK SPACE maps to U+0020.
        assert sasl_prep("a b") == "a b"

    def test_maps_soft_hyphen_to_nothing(self) -> None:
        # U+00AD SOFT HYPHEN is in table B.1.
        assert sasl_prep("soft­hyphen") == "softhyphen"

    def test_nfkc_normalization(self) -> None:
        # U+2168 ROMAN NUMERAL NINE -> "IX" under NFKC.
        assert sasl_prep("Ⅸ") == "IX"

    @pytest.mark.parametrize("bad", ["a\x00b", "ctrl\x07", "plain\rtext"])
    def test_prohibits_control_characters(self, bad: str) -> None:
        with pytest.raises(SaslprepError):
            sasl_prep(bad)

    def test_prohibits_non_ascii_space_in_output_after_mapping_edge(self) -> None:
        # U+FFFD replacement character is prohibited (table C.2.1/C.4).
        with pytest.raises(SaslprepError):
            sasl_prep("�")

    def test_type_error_on_bytes(self) -> None:
        with pytest.raises(TypeError):
            sasl_prep(b"pencil")  # type: ignore[arg-type]

    def test_bidirectional_violation_rejected(self) -> None:
        # R/AL character present without R/AL at both ends.
        with pytest.raises(SaslprepError):
            sasl_prep("abcاdef")
