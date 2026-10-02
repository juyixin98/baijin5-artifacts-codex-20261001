package com.example.clique.graph;

import com.example.clique.error.CliqueException;
import com.example.clique.error.FailureCategory;

import java.math.BigInteger;
import java.util.Arrays;

/**
 * Single-use builder for {@link Graph}. Adding the same edge twice is a no-op
 * (idempotent dedup). Self-loops and out-of-range vertices are input errors.
 * Calling {@link #build()} twice, or mutating after build, is a state conflict.
 */
public final class GraphBuilder {

    private final int vertexCount;
    private final BigInteger[] adjacency;
    private boolean built;

    GraphBuilder(int vertexCount) {
        if (vertexCount < 0) {
            throw new CliqueException(FailureCategory.INPUT_ERROR,
                    "vertex count must be >= 0, got " + vertexCount);
        }
        if (vertexCount > Graph.MAX_VERTICES) {
            throw new CliqueException(FailureCategory.RESOURCE_EXHAUSTED,
                    "vertex count " + vertexCount + " exceeds guard " + Graph.MAX_VERTICES);
        }
        this.vertexCount = vertexCount;
        this.adjacency = new BigInteger[vertexCount];
        Arrays.fill(this.adjacency, BigInteger.ZERO);
    }

    /** Adds undirected edge u-v. Duplicate calls are deduplicated. */
    public GraphBuilder addEdge(int u, int v) {
        checkMutable();
        checkVertex(u);
        checkVertex(v);
        if (u == v) {
            throw new CliqueException(FailureCategory.INPUT_ERROR,
                    "self-loop not allowed on vertex " + u);
        }
        adjacency[u] = adjacency[u].setBit(v);
        adjacency[v] = adjacency[v].setBit(u);
        return this;
    }

    /** Freezes and returns the graph. This builder must not be used afterwards. */
    public Graph build() {
        checkMutable();
        built = true;
        return new Graph(vertexCount, adjacency);
    }

    private void checkMutable() {
        if (built) {
            throw new CliqueException(FailureCategory.STATE_CONFLICT,
                    "builder already consumed by build()");
        }
    }

    private void checkVertex(int v) {
        if (v < 0 || v >= vertexCount) {
            throw new CliqueException(FailureCategory.INPUT_ERROR,
                    "vertex " + v + " out of range [0," + vertexCount + ")");
        }
    }
}
