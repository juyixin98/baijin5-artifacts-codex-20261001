"""Supported dtypes and their rules.

Only a fixed, explicit numeric subset is supported -- never trust an
arbitrary dtype string from the boundary.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..errors import DTypeMismatchError, UnsupportedDTypeError


@dataclass(frozen=True)
class DTypeInfo:
    name: str          # canonical wire name, e.g. "float64"
    kind: str          # "int" | "uint" | "float"
    itemsize: int      # bytes per element
    np_dtype: np.dtype


def _info(name: str) -> DTypeInfo:
    dt = np.dtype(name)
    if dt.kind == "i":
        kind = "int"
    elif dt.kind == "u":
        kind = "uint"
    elif dt.kind == "f":
        kind = "float"
    else:  # pragma: no cover - registry below only admits numeric kinds
        raise UnsupportedDTypeError(f"{name!r} is not a numeric dtype")
    return DTypeInfo(name=name, kind=kind, itemsize=dt.itemsize, np_dtype=dt)


# Canonical names accepted on the wire / in configs.
SUPPORTED: dict[str, DTypeInfo] = {
    name: _info(name)
    for name in (
        "int8", "int16", "int32", "int64",
        "uint8", "uint16", "uint32", "uint64",
        "float32", "float64",
    )
}

# NumPy scalar/type -> canonical name, for normalising user-provided dtypes.
_NUMPY_ALIASES: dict[np.dtype, str] = {
    info.np_dtype: name for name, info in SUPPORTED.items()
}
# Common Python spelling variants accepted at the boundary.
_ALIAS_STRINGS: dict[str, str] = {
    "float": "float64",
    "double": "float64",
    "single": "float32",
    "int": "int64",
}


def resolve_dtype(spec: str | np.dtype | type) -> DTypeInfo:
    """Resolve anything dtype-like to a supported :class:`DTypeInfo`.

    Raises :class:`UnsupportedDTypeError` for anything outside the subset
    (booleans, complex, object/string dtypes, ...).
    """
    if isinstance(spec, DTypeInfo):
        return spec
    if isinstance(spec, np.dtype):
        candidate = spec
        name = candidate.name
    elif isinstance(spec, str):
        lowered = spec.strip().lower().replace(" ", "")
        name = _ALIAS_STRINGS.get(lowered, lowered)
        try:
            candidate = np.dtype(name)
        except TypeError as exc:
            raise UnsupportedDTypeError(
                f"dtype {spec!r} is not a recognized numeric dtype") from exc
    elif isinstance(spec, type) and issubclass(spec, np.generic):
        candidate = np.dtype(spec)
        name = candidate.name
    elif isinstance(spec, type) and issubclass(spec, (int, float)):
        candidate = np.dtype("int64" if issubclass(spec, int) else "float64")
        name = candidate.name
    else:
        raise UnsupportedDTypeError(f"cannot interpret dtype specification {spec!r}")
    canonical = _NUMPY_ALIASES.get(candidate)
    if canonical is None:
        raise UnsupportedDTypeError(
            f"dtype {spec!r} is not supported; choose from {sorted(SUPPORTED)}")
    return SUPPORTED[canonical]


def result_kind(*infos: DTypeInfo) -> str:
    """Classify the promoted arithmetic kind (``"float"`` wins)."""
    return "float" if any(info.kind == "float" for info in infos) else "int"


def assert_same_dtype(left: DTypeInfo, right: DTypeInfo, *, op: str) -> None:
    """Reject mixed-dtype elementwise ops with a typed error.

    The backend intentionally performs *no* implicit dtype promotion:
    clients must cast explicitly, keeping copies explainable.
    """
    if left.name != right.name:
        raise DTypeMismatchError(
            f"operation {op!r} requires identical dtypes, got "
            f"{left.name!r} and {right.name!r}; cast explicitly",
            details={"op": op, "left": left.name, "right": right.name})
