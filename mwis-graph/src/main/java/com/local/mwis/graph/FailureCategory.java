package com.local.mwis.graph;

/**
 * Machine-readable rejection categories. Every refused request maps to exactly one
 * category so callers and tests can assert on the failure class, not the message text.
 */
public enum FailureCategory {
    /** Graph itself is malformed (bad vertex ids, negative sizes). */
    INVALID_GRAPH,
    /** Weight vector length does not match the vertex count. */
    WEIGHT_LENGTH_MISMATCH,
    /** Decomposition node/edge references are out of range or duplicated. */
    DECOMPOSITION_STRUCTURE_INVALID,
    /** The decomposition's node adjacency is not a tree (disconnected or cyclic). */
    DECOMPOSITION_NOT_A_TREE,
    /** A bag contains the same vertex twice. */
    DUPLICATE_VERTEX_IN_BAG,
    /** A bag references a vertex that does not exist in the graph. */
    BAG_VERTEX_OUT_OF_RANGE,
    /** Some graph edge is not contained in any single bag. */
    EDGE_NOT_COVERED,
    /** The bags containing a vertex do not form a connected subtree. */
    VERTEX_OCCURRENCES_DISCONNECTED,
    /** Decomposition has no bags at all. */
    EMPTY_DECOMPOSITION
}
