package com.example.coloring.diag;

import java.util.List;

/**
 * One diagnostic record.
 *
 * <p>Every event carries: the request id (correlation), a monotonically
 * increasing sequence, the decision kind, the reason, and the key search
 * state (depth, used colors, best upper bound). Vertex identities are given
 * as raw indices and, when labels are sensitive, rendered through
 * {@link #render(boolean, java.util.List)} which hashes them.</p>
 */
public record SearchEvent(
        String requestId,
        long sequence,
        DecisionKind kind,
        Reason reason,
        int depth,
        int vertex,
        int color,
        int usedColors,
        int bestUpperBound,
        long nodesVisited,
        List<Integer> cliqueWitness) {

    public String render(boolean maskLabels, List<String> labels) {
        String who = "<none>";
        if (vertex >= 0) {
            who = maskLabels ? "h#" + Integer.toHexString(mask(labels.get(vertex))) : labels.get(vertex);
        }
        String witness = "<none>";
        if (cliqueWitness != null && !cliqueWitness.isEmpty()) {
            StringBuilder sb = new StringBuilder("[");
            for (int i = 0; i < cliqueWitness.size(); i++) {
                if (i > 0) {
                    sb.append(',');
                }
                int v = cliqueWitness.get(i);
                sb.append(maskLabels ? "h#" + Integer.toHexString(mask(labels.get(v))) : labels.get(v));
            }
            witness = sb.append(']').toString();
        }
        return String.format(
                "req=%s seq=%d %s/%s depth=%d vertex=%s color=%d used=%d bestUB=%d nodes=%d clique=%s :: %s",
                requestId, sequence, kind, reason, depth, who, color, usedColors,
                bestUpperBound, nodesVisited, witness, reason.description());
    }

    /** Stable, non-reversible label fingerprint (FNV-1a 32-bit) for redacted logs. */
    private static int mask(String label) {
        int hash = 0x811C9DC5;
        for (int i = 0; i < label.length(); i++) {
            hash ^= label.charAt(i);
            hash *= 0x01000193;
        }
        return hash;
    }
}
