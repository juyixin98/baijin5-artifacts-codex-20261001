"""Tests for graph validation, topological execution and traces."""

from __future__ import annotations

import numpy as np
import pytest

from tensorcraft.errors import GraphError
from tensorcraft.graph import Graph, execute_graph
from tensorcraft.tensor import Tensor


def _bindings(**arrays) -> dict[str, Tensor]:
    return {name: Tensor.from_nested(values, "int64")
            for name, values in arrays.items()}


class TestGraphValidation:
    def test_unknown_reference_rejected(self):
        with pytest.raises(GraphError) as exc:
            Graph.from_dict({
                "nodes": [{"id": "n", "op": "neg", "inputs": ["ghost"]}],
                "outputs": ["n"],
            })
        assert "unknown input" in exc.value.message
        assert exc.value.details["reference"] == "ghost"

    def test_self_reference_rejected(self):
        with pytest.raises(GraphError):
            Graph.from_dict({
                "nodes": [{"id": "n", "op": "neg", "inputs": ["n"]}],
                "outputs": ["n"],
            })

    def test_cycle_detected(self):
        with pytest.raises(GraphError) as exc:
            Graph.from_dict({
                "nodes": [
                    {"id": "a", "op": "neg", "inputs": ["b"]},
                    {"id": "b", "op": "neg", "inputs": ["a"]},
                ],
                "outputs": ["a"],
            })
        assert exc.value.details["cyclic_nodes"] == ["a", "b"]

    def test_duplicate_node_id(self):
        with pytest.raises(GraphError):
            Graph.from_dict({
                "nodes": [
                    {"id": "a", "op": "neg", "inputs": ["x"]},
                    {"id": "a", "op": "neg", "inputs": ["x"]},
                ],
                "outputs": ["a"], "inputs": ["x"],
            })

    def test_unknown_output(self):
        with pytest.raises(GraphError):
            Graph.from_dict({
                "nodes": [{"id": "a", "op": "neg", "inputs": ["x"]}],
                "outputs": ["nope"], "inputs": ["x"],
            })

    def test_missing_binding(self):
        graph = Graph.from_dict({
            "nodes": [{"id": "a", "op": "neg", "inputs": ["x"]}],
            "outputs": ["a"], "inputs": ["x"],
        })
        with pytest.raises(GraphError) as exc:
            execute_graph(graph, {})
        assert exc.value.details["missing"] == "x"


class TestTopologicalOrder:
    def test_executes_dependencies_first(self):
        graph = Graph.from_dict({
            "nodes": [
                {"id": "c", "op": "add", "inputs": ["a", "b"]},
                {"id": "d", "op": "neg", "inputs": ["c"]},
            ],
            "outputs": ["d"], "inputs": ["a", "b"],
        })
        execution = execute_graph(
            graph, _bindings(a=[1], b=[2]))
        assert [t.node_id for t in execution.traces] == ["c", "d"]
        assert execution.outputs["d"].to_numpy().tolist() == [-3]

    def test_prunes_unused_nodes(self):
        graph = Graph.from_dict({
            "nodes": [
                {"id": "used", "op": "neg", "inputs": ["x"]},
                {"id": "unused", "op": "neg", "inputs": ["x"]},
            ],
            "outputs": ["used", "unused"], "inputs": ["x"],
        })
        execution = execute_graph(
            graph, _bindings(x=[1, 2]), output_names=["used"])
        assert [t.node_id for t in execution.traces] == ["used"]

    def test_diamond_diamond_share_view(self):
        graph = Graph.from_dict({
            "nodes": [
                {"id": "t", "op": "transpose", "inputs": ["x"]},
                {"id": "left", "op": "neg", "inputs": ["t"]},
                {"id": "right", "op": "abs", "inputs": ["t"]},
                {"id": "sum", "op": "add", "inputs": ["left", "right"]},
            ],
            "outputs": ["sum"], "inputs": ["x"],
        })
        bindings = _bindings(x=[[1, 2], [3, 4]])
        execution = execute_graph(graph, bindings)
        trace_by_id = {t.node_id: t for t in execution.traces}
        # transpose is a view; neg/abs/sum allocate fresh storage.
        assert trace_by_id["t"].copied is False
        assert trace_by_id["left"].copied is True
        assert trace_by_id["right"].copied is True
        assert trace_by_id["sum"].copied is True
        # The view node aliases its input; computing nodes do not.
        assert trace_by_id["t"].aliases_input is not None
        assert trace_by_id["sum"].aliases_input is None
        assert execution.outputs["sum"].to_numpy().tolist() == [
            [0, 0], [0, 0]]


class TestViewOps:
    def test_transpose_reshape_chain_records_copy(self):
        graph = Graph.from_dict({
            "nodes": [
                {"id": "t", "op": "transpose", "inputs": ["x"]},
                {"id": "flat", "op": "reshape", "inputs": ["t"],
                 "params": {"shape": [6], "order": "C"}},
            ],
            "outputs": ["flat"], "inputs": ["x"],
        })
        execution = execute_graph(
            graph, _bindings(x=[[1, 2, 3], [4, 5, 6]]))
        traces = {t.node_id: t for t in execution.traces}
        assert traces["t"].copied is False
        assert traces["flat"].copied is True
        assert execution.outputs["flat"].to_numpy().tolist() == [1, 4, 2, 5, 3, 6]

    def test_slice_op(self):
        graph = Graph.from_dict({
            "nodes": [{"id": "s", "op": "slice", "inputs": ["x"],
                       "params": {"index": [[None, None, -1]]}}],
            "outputs": ["s"], "inputs": ["x"],
        })
        execution = execute_graph(graph, _bindings(x=[1, 2, 3, 4]))
        assert execution.outputs["s"].to_numpy().tolist() == [4, 3, 2, 1]

    def test_reduce_and_matmul(self):
        x = np.arange(6).reshape(2, 3)
        graph = Graph.from_dict({
            "nodes": [
                {"id": "p", "op": "matmul", "inputs": ["a", "b"]},
                {"id": "tot", "op": "reduce_sum", "inputs": ["p"]},
            ],
            "outputs": ["p", "tot"],
            "inputs": ["a", "b"],
        })
        execution = execute_graph(graph, {
            "a": Tensor.from_nested(x.tolist(), "int64"),
            "b": Tensor.from_nested(np.arange(6, 12).reshape(3, 2).tolist(), "int64"),
        })
        assert execution.outputs["tot"].to_numpy().reshape(()).item() == int((x @ np.arange(6, 12).reshape(3, 2)).sum())

    def test_unknown_op_rejected(self):
        graph = Graph.from_dict({
            "nodes": [{"id": "n", "op": "frobnicate", "inputs": ["x"]}],
            "outputs": ["n"], "inputs": ["x"],
        })
        with pytest.raises(GraphError):
            execute_graph(graph, _bindings(x=[1]))

    def test_arity_mismatch_rejected(self):
        graph = Graph.from_dict({
            "nodes": [{"id": "n", "op": "neg", "inputs": ["a", "b"]}],
            "outputs": ["n"], "inputs": ["a", "b"],
        })
        with pytest.raises(GraphError):
            execute_graph(graph, _bindings(a=[1], b=[2]))
