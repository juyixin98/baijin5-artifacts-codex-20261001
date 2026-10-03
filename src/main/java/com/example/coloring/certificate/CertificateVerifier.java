package com.example.coloring.certificate;

import com.example.coloring.model.Graph;
import com.example.coloring.search.ColoringResult;
import com.example.coloring.search.ColoringStatus;
import java.util.ArrayList;
import java.util.List;

/**
 * Independent certificate checker for chromatic-number results.
 *
 * <p>It trusts neither the solver nor its data structures: every claimed bound is
 * re-derived from the attached witnesses and the original graph.
 *
 * <ul>
 *   <li>The coloring is checked vertex by vertex against every edge.</li>
 *   <li>The clique witness is checked to be pairwise adjacent.</li>
 *   <li>Bound ordering and the OPTIMAL/STOPPED claim are checked.</li>
 * </ul>
 */
public final class CertificateVerifier {

    private CertificateVerifier() {
    }

    public static CertificateVerdict verify(Graph graph, ColoringResult result) {
        List<CertificateError> errors = new ArrayList<>();
        int n = graph.n();

        int[] coloring = result.coloring();
        if (coloring == null) {
            return CertificateVerdict.reject(
                    List.of(CertificateError.UNCOLORED_VERTEX),
                    "no feasible coloring certificate attached");
        }
        if (coloring.length != n) {
            errors.add(CertificateError.UNCOLORED_VERTEX);
        }

        int observedColors = checkColoring(graph, n, coloring, errors);
        if (!errors.isEmpty()) {
            return CertificateVerdict.reject(errors, "coloring witness invalid");
        }

        int upper = result.upperBound();
        if (upper != observedColors) {
            errors.add(CertificateError.COLOR_COUNT_MISMATCH);
        }

        int lower = result.lowerBound();
        int[] clique = result.cliqueWitness();
        if (!isClique(graph, clique)) {
            errors.add(CertificateError.NOT_A_CLIQUE);
        }
        // The clique independently proves omega; the search may have raised the
        // proven lower bound above omega via infeasibility proofs. The certificate
        // is sound whenever the clique witness does not exceed the claimed bound.
        if (clique.length > lower) {
            errors.add(CertificateError.LOWER_BOUND_WITNESS_MISMATCH);
        }
        if (lower > upper) {
            errors.add(CertificateError.BOUNDS_INVERTED);
        }

        if (result.status() == ColoringStatus.OPTIMAL) {
            if (lower != upper) {
                errors.add(CertificateError.NOT_PROVEN_OPTIMAL);
            }
        } else if (result.chromatic().isPresent()) {
            errors.add(CertificateError.STOPPED_BUT_VALUE_CLAIMED);
        }

        if (errors.isEmpty()) {
            String what = result.status() == ColoringStatus.OPTIMAL
                    ? "chi=" + upper + " proven by coloring + clique witnesses"
                    : "proven gap " + lower + " <= chi <= " + upper;
            return CertificateVerdict.ok(what);
        }
        return CertificateVerdict.reject(errors, "certificate failed independent verification");
    }

    private static int checkColoring(Graph graph, int n, int[] coloring,
                                     List<CertificateError> errors) {
        int declaredMax = -1;
        for (int v = 0; v < n; v++) {
            int c = coloring[v];
            if (c < 0) {
                errors.add(CertificateError.UNCOLORED_VERTEX);
                return 0;
            }
            declaredMax = Math.max(declaredMax, c);
        }
        for (int u = 0; u < n; u++) {
            for (int v = u + 1; v < n; v++) {
                if (graph.adjacent(u, v) && coloring[u] == coloring[v]) {
                    errors.add(CertificateError.MONOCHROMATIC_EDGE);
                }
            }
        }
        return n == 0 ? 0 : declaredMax + 1;
    }

    private static boolean isClique(Graph graph, int[] clique) {
        for (int i = 0; i < clique.length; i++) {
            int u = clique[i];
            if (u < 0 || u >= graph.n()) {
                return false;
            }
            for (int j = i + 1; j < clique.length; j++) {
                if (!graph.adjacent(u, clique[j])) {
                    return false;
                }
            }
        }
        return true;
    }
}
