package com.local.mwis.bounds;

import com.local.mwis.graph.Edge;

import java.math.BigInteger;
import java.util.List;

/**
 * Result of verifying a claimed independent set against a graph and weight vector.
 *
 * @param independent      true when no selected pair is adjacent and all vertices exist
 * @param violations       edges edges whose both endpoints were selected
 * @param unknownVertices  selected vertex ids that do not exist in the graph
 * @param computedWeight   sum of weights of the (existing) selected vertices
 * @param claimedWeight    weight supplied by the claimant, null when not provided
 * @param weightMatches    true when claimedWeight equals computedWeight
 */
public record CertificateCheck(
        boolean independent,
        List<Edge> violations,
        List<Integer> unknownVertices,
        BigInteger computedWeight,
        BigInteger claimedWeight,
        boolean weightMatches) {

    public boolean accepted() {
        return independent && weightMatches;
    }
}
