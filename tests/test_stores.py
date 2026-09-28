"""Tests for bounded tensor/graph/trainer registries."""

from __future__ import annotations

import pytest

from tensorcraft.errors import ConfigError, StateError
from tensorcraft.graph import Graph
from tensorcraft.state import GraphStore, TensorStore, TrainerStore
from tensorcraft.tensor import Tensor


class TestTensorStore:
    def test_put_get_delete_cycle(self):
        store = TensorStore(4)
        tensor = Tensor.from_nested([1, 2, 3], "int64")
        handle = store.put(tensor)
        assert store.get(handle) is tensor
        assert handle in store
        store.delete(handle)
        assert handle not in store

    def test_unknown_handle_is_state_error(self):
        store = TensorStore(2)
        with pytest.raises(StateError) as exc:
            store.get("ghost")
        assert exc.value.details["not_found"] is True

    def test_capacity_enforced(self):
        store = TensorStore(1)
        store.put(Tensor.from_nested([1], "int64"))
        with pytest.raises(StateError):
            store.put(Tensor.from_nested([2], "int64"))

    def test_explicit_handle(self):
        store = TensorStore(2)
        assert store.put(Tensor.from_nested([1], "int64"), handle="mine") == "mine"

    def test_invalid_capacity(self):
        with pytest.raises(ConfigError):
            TensorStore(0)

    def test_per_tensor_element_limit_enforced(self):
        from tensorcraft.errors import StorageLimitExceededError
        store = TensorStore(8, max_elements=3)
        small = Tensor.from_nested([1, 2], "int64")
        store.put(small)
        big = Tensor.from_nested([1, 2, 3, 4], "int64")
        with pytest.raises(StorageLimitExceededError) as exc:
            store.put(big)
        assert exc.value.details["size"] == 4
        assert exc.value.details["limit"] == 3

    def test_invalid_max_elements(self):
        with pytest.raises(ConfigError):
            TensorStore(8, max_elements=0)

    def test_delete_unknown_rejected(self):
        store = TensorStore(2)
        with pytest.raises(StateError):
            store.delete("ghost")


class TestGraphStore:
    def test_capacity_and_unknown(self):
        store = GraphStore(1)
        graph = Graph.from_dict({
            "nodes": [{"id": "n", "op": "neg", "inputs": ["x"]}],
            "outputs": ["n"], "inputs": ["x"],
        })
        store.put(graph)
        with pytest.raises(StateError):
            store.put(graph)
        assert store.get(store.list_handles()[0]) is graph
        with pytest.raises(StateError):
            store.get("nope")


class TestTrainerStore:
    def test_type_guarded(self):
        store = TrainerStore(2)
        with pytest.raises(TypeError):
            store.put("not a trainer")  # type: ignore[arg-type]

    def test_capacity_unknown_and_delete(self):
        from tensorcraft.state import LinearSGDTrainer
        import numpy as np
        X = Tensor.from_nested(np.ones((4, 2)).tolist(), "float64")
        y = Tensor.from_nested([1.0, 1.0, 1.0, 1.0], "float64")
        store = TrainerStore(1)
        trainer = LinearSGDTrainer(X, y, max_steps=2)
        handle = store.put(trainer)
        assert store.has(handle)
        assert store.get(handle) is trainer
        assert store.list_handles() == [handle]
        with pytest.raises(StateError):
            store.put(LinearSGDTrainer(X, y, max_steps=2))
        with pytest.raises(StateError):
            store.get("ghost")
        store.delete(handle)
        assert not store.has(handle)
        with pytest.raises(StateError):
            store.delete(handle)

    def test_invalid_capacity(self):
        with pytest.raises(ConfigError):
            TrainerStore(-1)
