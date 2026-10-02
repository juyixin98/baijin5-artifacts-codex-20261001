package com.example.clique.cert;

import com.example.clique.error.CliqueException;
import com.example.clique.error.FailureCategory;
import com.example.clique.graph.Graph;
import com.example.clique.reference.BruteForceMaximalCliques;

import java.math.BigInteger;
import java.util.Collection;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.TreeSet;

/**
 * Certificates over clique results. Failures are reported as
 * {@link FailureCategory#COMPUTATION_FAILED} with the violated invariant in
 * the message, so a bad result is distinguishable from bad input.
 */
public final class CliqueCertificate {

    private CliqueCertificate() {
    }

    /** Every pair of distinct vertices in {@code clique} is adjacent. */
    public static boolean isClique(Graph graph, BigInteger clique) {
        BigInteger rest = clique;
        while (rest.signum() != 0) {
            int v = rest.getLowestSetBit();
            rest = rest.clearBit(v);
            if (graph.neighbors(v).and(clique).bitCount() != clique.bitCount() - 1) {
                return false;
            }
        }
        return true;
    }

    /** Clique and no outside vertex is adjacent to every member. */
    public static boolean isMaximalClique(Graph graph, BigInteger clique) {
        if (clique.signum() == 0 || !isClique(graph, clique)) {
            return false;
        }
        BigInteger outside = graph.vertices().andNot(clique);
        while (outside.signum() != 0) {
            int w = outside.getLowestSetBit();
            outside = outside.clearBit(w);
            if (graph.neighbors(w).and(clique).equals(clique)) {
                return false;
            }
        }
        return true;
    }

    /** Throws unless {@code clique} is a maximal clique of {@code graph}. */
    public static void requireMaximalClique(Graph graph, BigInteger clique, String runId) {
        if (clique.signum() == 0) {
            throw new CliqueException(FailureCategory.COMPUTATION_FAILED,
                    "certificate rejected: empty set is not a reportable clique", runId);
        }
        if (!isClique(graph, clique)) {
            throw new CliqueException(FailureCategory.COMPUTATION_FAILED,
                    "certificate rejected: " + format(clique) + " is not a clique", runId);
        }
        if (!isMaximalClique(graph, clique)) {
            throw new CliqueException(FailureCategory.COMPUTATION_FAILED,
                    "certificate rejected: " + format(clique) + " is a clique but not maximal", runId);
        }
    }

    /**
     * Cheap certificate for any graph size: every reported clique is maximal
     * and no clique is reported twice.
     */
    public static void verifyMaximalityAndUniqueness(Graph graph, Collection<BigInteger> cliques, String runId) {
        Set<BigInteger> seen = new HashSet<>();
        for (BigInteger clique : cliques) {
            requireMaximalClique(graph, clique, runId);
            if (!seen.add(clique)) {
                throw new CliqueException(FailureCategory.COMPUTATION_FAILED,
                        "certificate rejected: duplicate clique " + format(clique), runId);
            }
        }
    }

    /**
     * Strong certificate for small graphs: additionally proves completeness by
     * comparing against the independent brute-force reference. Guarded by
     * {@link BruteForceMaximalCliques#MAX_VERTICES}.
     */
    public static void verifyCompleteness(Graph graph, List<BigInteger> cliques, String runId) {
        verifyMaximalityAndUniqueness(graph, cliques, runId);
        Set<BigInteger> actual = new HashSet<>(cliques);
        Set<BigInteger> expected = new HashSet<>(BruteForceMaximalCliques.enumerate(graph));
        if (!actual.equals(expected)) {
            Set<BigInteger> missing = new TreeSet<>(expected);
            missing.removeAll(actual);
            Set<BigInteger> extra = new TreeSet<>(actual);
            extra.removeAll(expected);
            throw new CliqueException(FailureCategory.COMPUTATION_FAILED,
                    "certificate rejected: enumeration mismatch vs brute-force reference; missing="
                            + formatAll(missing) + " extra=" + formatAll(extra),
                    runId);
        }
    }

    /** Renders a clique bit mask as {@code {0,1,2}} for logs and CLI output. */
    public static String format(BigInteger clique) {
        StringBuilder sb = new StringBuilder("{");
        BigInteger rest = clique;
        boolean first = true;
        while (rest.signum() != 0) {
            int v = rest.getLowestSetBit();
            rest = rest.clearBit(v);
            if (!first) {
                sb.append(',');
            }
            sb.append(v);
            first = false;
        }
        return sb.append('}').toString();
    }

    private static String formatAll(Set<BigInteger> cliques) {
        StringBuilder sb = new StringBuilder("[");
        boolean first = true;
        for (BigInteger clique : cliques) {
            if (!first) {
                sb.append(' ');
            }
            sb.append(format(clique));
            first = false;
        }
        return sb.append(']').toString();
    }
}
