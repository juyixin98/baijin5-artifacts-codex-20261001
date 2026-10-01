package com.example.nonoverlap.api;

import java.util.Map;

/**
 * One structured trace record. {@code detail} holds keyed intermediate state
 * (domains, pruned values, reason) so a run can be replayed without a debugger.
 */
public record TraceEvent(String phase, String reason, Map<String, ?> detail) {

    public static TraceEvent of(String phase, String reason) {
        return new TraceEvent(phase, reason, Map.of());
    }

    public static TraceEvent of(String phase, String reason, Map<String, ?> detail) {
        return new TraceEvent(phase, reason, Map.copyOf(detail));
    }
}
