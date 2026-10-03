package com.example.coloring.diag;

import java.util.Map;

/**
 * One structured diagnostic record.
 *
 * @param requestId   id of the solver request this event belongs to
 * @param nodeId      monotonic decision-tree node id within the request
 * @param reason      why the branch was accepted, rejected, or could not be decided
 * @param vertex      raw vertex selected at this node (or -1 if not applicable)
 * @param color       color tried / refused (or -1 if not applicable)
 * @param targetK     k whose colorability is being decided
 * @param remaining   remaining node budget, or null for an unlimited budget
 * @param detail      small key/value view of key state (uncolored count etc.)
 */
public record DiagnosticEvent(
        String requestId,
        long nodeId,
        PruningReason reason,
        int vertex,
        int color,
        int targetK,
        java.math.BigInteger remaining,
        Map<String, Long> detail) {

    public DiagnosticEvent {
        detail = Map.copyOf(detail);
    }
}
