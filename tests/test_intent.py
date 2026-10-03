"""Intent support check (unit-level; fixtures support all four intents)."""

from __future__ import annotations

import pytest

from iccconv.contract import RenderingIntent
from iccconv.errors import IntentUnsupportedError
from iccconv.kernel import transform as transform_mod


class _FakeCore:
    def __init__(self, supported: bool):
        self._supported = supported

    def is_intent_supported(self, intent: int, direction: int) -> bool:
        return self._supported


class _FakeProfile:
    def __init__(self, supported: bool):
        self.profile = _FakeCore(supported)


def test_unsupported_intent_rejected(monkeypatch):
    monkeypatch.setattr(
        transform_mod, "_open_profile", lambda data: _FakeProfile(False)
    )
    with pytest.raises(IntentUnsupportedError):
        transform_mod.check_intent_supported(b"x", b"y", RenderingIntent.PERCEPTUAL)


def test_supported_intent_passes(registry):
    transform_mod.check_intent_supported(
        registry.load_bytes("sRGB.icc"),
        registry.load_bytes("SWOP_TR003_coated_3.icc"),
        RenderingIntent.RELATIVE_COLORIMETRIC,
    )
