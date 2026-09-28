"""Tests for dtype resolution rules and storage invariants."""

from __future__ import annotations

import numpy as np
import pytest

from tensorcraft.errors import OverflowErrorCore, UnsupportedDTypeError
from tensorcraft.tensor.dtypes import assert_same_dtype, resolve_dtype
from tensorcraft.tensor.storage import Storage


class TestResolveDtype:
    def test_canonical_names(self):
        assert resolve_dtype("float32").name == "float32"
        assert resolve_dtype("int64").name == "int64"

    def test_aliases(self):
        assert resolve_dtype("float").name == "float64"
        assert resolve_dtype("double").name == "float64"
        assert resolve_dtype("int").name == "int64"

    def test_numpy_dtype_objects(self):
        assert resolve_dtype(np.dtype("int32")).name == "int32"

    def test_python_scalar_types(self):
        assert resolve_dtype(float).name == "float64"
        assert resolve_dtype(int).name == "int64"

    @pytest.mark.parametrize("spec", [
        "bool", "complex128", "object", "str", "uint128"])
    def test_unsupported_dtypes(self, spec):
        with pytest.raises(UnsupportedDTypeError):
            resolve_dtype(spec)

    def test_uninterpretable_spec(self):
        with pytest.raises(UnsupportedDTypeError):
            resolve_dtype(123)  # type: ignore[arg-type]

    def test_assert_same_dtype(self):
        left = resolve_dtype("int64")
        assert_same_dtype(left, resolve_dtype("int64"), op="add")
        from tensorcraft.errors import DTypeMismatchError
        with pytest.raises(DTypeMismatchError):
            assert_same_dtype(left, resolve_dtype("int32"), op="add")


class TestStorage:
    def test_requires_ndarray(self):
        with pytest.raises(TypeError):
            Storage([1, 2, 3], resolve_dtype("int64"))  # type: ignore[arg-type]

    def test_requires_1d(self):
        with pytest.raises(ValueError):
            Storage(np.zeros((2, 2), dtype=np.int64),
                    resolve_dtype("int64"))

    def test_dtype_must_match(self):
        with pytest.raises(ValueError):
            Storage(np.zeros(3, dtype=np.int32),
                    resolve_dtype("int64"))

    def test_allocate_validates_size(self):
        with pytest.raises(OverflowErrorCore):
            Storage.allocate(-1, resolve_dtype("int64"))
        with pytest.raises(OverflowErrorCore):
            Storage.allocate(1.5, resolve_dtype("int64"))  # type: ignore[arg-type]

    def test_from_flat_copies_and_flattens(self):
        storage = Storage.from_flat([[1, 2], [3, 4]], resolve_dtype("int64"))
        assert storage.length == 4
        assert storage.buffer.tolist() == [1, 2, 3, 4]

    def test_token_stable_and_unique(self):
        a = Storage.allocate(2, resolve_dtype("int64"))
        b = Storage.allocate(2, resolve_dtype("int64"))
        assert a.token > 0
        assert a.token != b.token
