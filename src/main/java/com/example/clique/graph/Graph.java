package com.example.clique.graph;

import com.example.clique.error.CliqueException;
import com.example.clique.error.FailureCategory;

import java.math.BigInteger;

/**
 * Graph contract: an undirected simple graph on vertices {@code 0..n-1},
 * adjacency stored as {@link BigInteger} bit masks (bit v set == vertex v in set).
 *
 * <p>Contract rules:
 * <ul>
 *   <li>Immutable after {@link GraphBuilder#build()}.</li>
 *   <li>No self-loops; duplicate edges are idempotently deduplicated by the builder.</li>
 *   <li>Adjacency is symmetric: {@code adjacent(u,v) == adjacent(v,u)}.</li>
 *   <li>The empty graph (n = 0) is valid and has zero maximal cliques by contract
 *       (the empty clique is never reported).</li>
 * </ul>
 */
public final class Graph {

    /** Hard resource guard on vertex count. */
    public static final int MAX_VERTICES = 100_000;

    private final int vertexCount;
    private final BigInteger[] adjacency;
    private final BigInteger vertexMask;
    private final long edgeCount;

    Graph(int vertexCount, BigInteger[] adjacency) {
        this.vertexCount = vertexCount;
        this.adjacency = adjacency;
        this.vertexMask = vertexCount == 0
                ? BigInteger.ZERO
                : BigInteger.ONE.shiftLeft(vertexCount).subtract(BigInteger.ONE);
        long degreeSum = 0;
        for (BigInteger mask : adjacency) {
            degreeSum += mask.bitCount();
        }
        this.edgeCount = degreeSum / 2;
    }

    public static GraphBuilder builder(int vertexCount) {
        return new GraphBuilder(vertexCount);
    }

    public int vertexCount() {
        return vertexCount;
    }

    /** Bit mask of all vertices {@code 0..n-1}. */
    public BigInteger vertices() {
        return vertexMask;
    }

    /** Bit mask of neighbours of {@code v}. */
    public BigInteger neighbors(int v) {
        checkVertex(v);
        return adjacency[v];
    }

    public boolean adjacent(int u, int v) {
        checkVertex(u);
        checkVertex(v);
        return adjacency[u].testBit(v);
    }

    public int degree(int v) {
        checkVertex(v);
        return adjacency[v].bitCount();
    }

    public long edgeCount() {
        return edgeCount;
    }

    private void checkVertex(int v) {
        if (v < 0 || v >= vertexCount) {
            throw new CliqueException(FailureCategory.INPUT_ERROR,
                    "vertex " + v + " out of range [0," + vertexCount + ")");
        }
    }
}
