"""Newick serialization.

Leaves are emitted as stable synthetic names ``L<node_id>``; the mapping to
the caller's original labels is returned separately (``leaf_map``) so that
arbitrary label text never has to be escaped into Newick. Children are
serialized in ascending node-id order, which combined with the stable
tie-breaking in nj.py makes the output byte-identical across runs.
"""

from __future__ import annotations

from .nj import NJResult


def _format_length(value: float) -> str:
    # 12 significant digits round-trip typical float64 branch lengths while
    # keeping the output readable; repr-level noise is not needed here.
    return format(value, ".12g")


def serialize_newick(result: NJResult) -> tuple[str, dict[str, str]]:
    """Return ``(newick_string, leaf_map)`` for an NJResult."""
    leaf_map = {
        f"L{node_id}": label for node_id, label in sorted(result.leaf_labels.items())
    }
    adjacency = result.adjacency

    def render(node: int, parent: int | None) -> str:
        children = sorted(nb for nb in adjacency.get(node, {}) if nb != parent)
        if not children:
            return f"L{node}"
        inner = ",".join(
            f"{render(child, node)}:{_format_length(adjacency[node][child])}"
            for child in children
        )
        return f"({inner})"

    return render(result.root_id, None) + ";", leaf_map
