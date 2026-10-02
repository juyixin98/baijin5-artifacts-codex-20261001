package com.example.clique.reference;

import com.example.clique.error.CliqueException;
import com.example.clique.error.FailureCategory;
import com.example.clique.graph.Graph;

import java.math.BigInteger;
import java.util.ArrayList;
import java.util.List;

/**
 * Independent reference implementation used for exhaustive cross-checking.
 * Enumerates every non-empty vertex subset and tests clique-ness and
 * maximality by direct pairwise adjacency queries - deliberately sharing no
 * code with the bitset-recursive Bron-Kerbosch core, so it can serve as an
 * oracle in tests and certificates.
 *
 * <p>Exponential in n; guarded to {@link #MAX_VERTICES} and practical only
 * for small graphs (roughly n <= 20).
 */
public final class BruteForceMaximalCliques {

    /** Resource guard: refuse to enumerate subsets above this vertex count. */
    public static final int MAX_VERTICES = 24;

    private BruteForceMaximalCliques() {
    }

    public static List<BigInteger> enumerate(Graph graph) {
        int n = graph.vertexCount();
        if (n > MAX_VERTICES) {
            throw new CliqueException(FailureCategory.RESOURCE_EXHAUSTED,
                    "brute-force reference supports at most " + MAX_VERTICES
                            + " vertices, got " + n);
        }
        List<BigInteger> result = new ArrayList<>();
        long subsets = 1L << n;
        for (long mask = 1; mask < subsets; mask++) {
            if (isClique(graph, mask) && isMaximal(graph, mask)) {
                result.add(toBitMask(mask));
            }
        }
        return result;
    }

    private static boolean isClique(Graph graph, long mask) {
        int n = graph.vertexCount();
        for (int u = 0; u < n; u++) {
            if ((mask & (1L << u)) == 0) {
                continue;
            }
            for (int v = u + 1; v < n; v++) {
                if ((mask & (1L << v)) != 0 && !graph.adjacent(u, v)) {
                    return false;
                }
            }
        }
        return true;
    }

    private static boolean isMaximal(Graph graph, long mask) {
        int n = graph.vertexCount();
        for (int w = 0; w < n; w++) {
            if ((mask & (1L << w)) != 0) {
                continue;
            }
            boolean adjacentToAll = true;
            for (int u = 0; u < n; u++) {
                if ((mask & (1L << u)) != 0 && !graph.adjacent(w, u)) {
                    adjacentToAll = false;
                    break;
                }
            }
            if (adjacentToAll) {
                return false;
            }
        }
        return true;
    }

    private static BigInteger toBitMask(long mask) {
        BigInteger out = BigInteger.ZERO;
        int bit = 0;
        long rest = mask;
        while (rest != 0) {
            if ((rest & 1L) != 0) {
                out = out.setBit(bit);
            }
            rest >>>= 1;
            bit++;
        }
        return out;
    }
}
