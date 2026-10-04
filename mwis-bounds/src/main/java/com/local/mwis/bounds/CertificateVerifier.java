package com.local.mwis.bounds;

import com.local.mwis.graph.Edge;
import com.local.mwis.graph.Graph;

import java.math.BigInteger;
import java.util.ArrayList;
import java.util.Collection;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/**
 * Independently verifies a claimed weighted independent set. This is the certificate
 * checker: it trusts nothing about how the set was produced.
 */
public final class CertificateVerifier {

    public CertificateCheck verify(Graph graph, BigInteger[] weights, Collection<Integer> claimedSet,
                                   BigInteger claimedWeight) {
        Set<Integer> selected = new HashSet<>(claimedSet);
        List<Edge> violations = new ArrayList<>();
        for (Edge e : graph.edges()) {
            if (selected.contains(e.u()) && selected.contains(e.v())) {
                violations.add(e);
            }
        }
        List<Integer> unknown = new ArrayList<>();
        BigInteger computed = BigInteger.ZERO;
        for (int v : selected) {
            if (v < 0 || v >= graph.vertexCount()) {
                unknown.add(v);
            } else {
                computed = computed.add(weights[v]);
            }
        }
        boolean matches = claimedWeight != null && claimedWeight.equals(computed);
        boolean clean = violations.isEmpty() && unknown.isEmpty();
        return new CertificateCheck(clean, List.copyOf(violations), List.copyOf(unknown),
                computed, claimedWeight, matches);
    }
}
