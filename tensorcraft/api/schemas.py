"""Pydantic request/response models (the API boundary)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


# --------------------------------------------------------------------- #
# Tensors
# --------------------------------------------------------------------- #

class TensorCreate(BaseModel):
    data: Any = Field(description="nested list / scalar of numeric values")
    dtype: str | None = Field(default=None, description="canonical dtype name")
    handle: str | None = Field(default=None, description="optional explicit handle")


class TensorDescription(BaseModel):
    handle: str
    shape: list[int]
    strides: list[int]
    storage_offset: int
    ndim: int
    size: int
    dtype: str
    storage_token: int
    c_contiguous: bool
    f_contiguous: bool
    self_overlapping: bool | None
    history: str


class TensorValues(BaseModel):
    handle: str
    values: Any
    order: str


class TransposeRequest(BaseModel):
    axes: list[int] | None = None


class ReshapeRequest(BaseModel):
    shape: list[int]
    order: Literal["C", "F"] = "C"
    allow_copy: bool = True


class SliceRequest(BaseModel):
    # Each entry: [start, stop, step] (nulls allowed), an int, "newaxis",
    # or "ellipsis".
    index: list[Any]


class BroadcastRequest(BaseModel):
    shape: list[int]


class MaterializeRequest(BaseModel):
    order: Literal["C", "F"] = "C"


class StridedViewRequest(BaseModel):
    shape: list[int]
    strides: list[int]
    offset: int = 0


class AstypeRequest(BaseModel):
    dtype: str


class AssignScalarRequest(BaseModel):
    value: float | int
    policy: Literal["reject", "temp_copy"] = "reject"


class AssignTensorRequest(BaseModel):
    source: str
    policy: Literal["reject", "temp_copy"] = "reject"


class WriteReportModel(BaseModel):
    elements_written: int
    buffered_source: bool
    temp_copy: bool
    policy: str


# --------------------------------------------------------------------- #
# Ops
# --------------------------------------------------------------------- #

BinaryOp = Literal[
    "add", "subtract", "multiply", "divide", "floor_divide", "mod", "power"]
ComparisonOp = Literal[
    "equal", "not_equal", "less", "less_equal", "greater", "greater_equal"]
UnaryOp = Literal["neg", "abs"]


class BinaryOpRequest(BaseModel):
    left: str
    right: str
    op: str


class ScalarOpRequest(BaseModel):
    tensor: str
    op: str
    value: float | int


class UnaryOpRequest(BaseModel):
    tensor: str
    op: str


class MatmulRequest(BaseModel):
    left: str
    right: str


class ReduceRequest(BaseModel):
    tensor: str
    axis: int | None = None
    keepdims: bool = False


# --------------------------------------------------------------------- #
# Graphs
# --------------------------------------------------------------------- #

class NodeModel(BaseModel):
    id: str
    op: str
    inputs: list[str] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)


class GraphCreate(BaseModel):
    nodes: list[NodeModel]
    outputs: list[str] = Field(default_factory=list)
    inputs: list[str] = Field(default_factory=list)
    handle: str | None = None


class GraphExecuteRequest(BaseModel):
    bindings: dict[str, str] = Field(
        description="graph input name -> tensor handle")
    outputs: list[str] | None = None
    include_values: bool = False


# --------------------------------------------------------------------- #
# Training
# --------------------------------------------------------------------- #

class TrainingCreate(BaseModel):
    features: str
    targets: str
    learning_rate: float = 0.05
    max_steps: int = 1000
    tol: float = 1e-10


class TrainingRunRequest(BaseModel):
    steps: int


class TrainingStatus(BaseModel):
    handle: str
    state: str
    step: int
    last_loss: float
    learning_rate: float
    max_steps: int
    n: int
    d: int
    weights_token: int
    failure_reason: str | None


# --------------------------------------------------------------------- #
# Verification
# --------------------------------------------------------------------- #

class ScenarioRequest(BaseModel):
    steps: list[dict[str, Any]]
