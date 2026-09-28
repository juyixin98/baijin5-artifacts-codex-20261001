"""Layer 4: numerical validation.

Independent dense reference + sparse/dense equivalence verification.

The reference in :mod:`sparse_embeddings.validation.dense_reference` is written
from scratch against full dense matrices with explicit loops/masks. It does
**not** import or call any kernel from :mod:`sparse_embeddings.graph`, so an
agreement failure cannot be masked by shared code. Test oracles are produced
by this reference, never by the implementation under test.
"""

from sparse_embeddings.validation.dense_reference import (
    DenseReferenceModel,
    dense_clip,
)
from sparse_embeddings.validation.equivalence import (
    EquivalenceReport,
    assert_sparse_matches_dense,
    assert_step_matches_reference,
    compare_states,
)

__all__ = [
    "DenseReferenceModel",
    "EquivalenceReport",
    "assert_sparse_matches_dense",
    "assert_step_matches_reference",
    "compare_states",
    "dense_clip",
]
