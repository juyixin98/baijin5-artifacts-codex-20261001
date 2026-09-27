"""Exception taxonomy for minigrad.

Every rejection path raises one of these so callers (and the API layer) can
classify failures without string matching.
"""

from __future__ import annotations


class MinigradError(Exception):
    """Base class for all minigrad errors."""


class InplaceModificationError(MinigradError):
    """A tensor saved for backward was modified in place after capture.

    Raised at backward time when the version counter of a saved tensor no
    longer matches the version recorded when the graph node was created.
    """

    def __init__(self, op_name: str, tensor, expected_version: int, actual_version: int):
        self.op_name = op_name
        self.tensor_name = tensor.name
        self.expected_version = expected_version
        self.actual_version = actual_version
        super().__init__(
            f"in-place modification detected: tensor {tensor.name!r} "
            f"(shape={tuple(tensor.shape)}) was saved by op {op_name!r} at "
            f"version {expected_version} but is now at version {actual_version}; "
            "its values may have changed, so gradients would be wrong"
        )


class GraphFreedError(MinigradError):
    """Backward was attempted through a graph that was already released."""

    def __init__(self, op_name: str):
        self.op_name = op_name
        super().__init__(
            f"cannot backpropagate through op {op_name!r}: the graph was "
            "released by a previous backward() call; pass retain_graph=True "
            "to backward() if you need to differentiate through it again"
        )


class BackwardError(MinigradError):
    """Backward was requested on a tensor that cannot be differentiated."""


class NonScalarBackwardError(BackwardError):
    """backward() without an explicit gradient requires a scalar output."""

    def __init__(self, shape):
        super().__init__(
            f"backward() called on a non-scalar tensor of shape {tuple(shape)}; "
            "pass an explicit gradient matching the output shape"
        )


class UnknownCaseError(MinigradError):
    """A named validation case does not exist."""

    def __init__(self, name: str):
        self.case_name = name
        super().__init__(f"unknown validation case: {name!r}")
