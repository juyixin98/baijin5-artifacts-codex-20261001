package com.example.coloring.diag;

import java.util.UUID;

/** Correlation handle attached to every diagnostic event of one solve call. */
public record RequestContext(String requestId, boolean maskLabels) {

    public RequestContext {
        if (requestId == null || requestId.isBlank()) {
            throw new IllegalArgumentException("requestId must be non-blank");
        }
    }

    public static RequestContext create(boolean maskLabels) {
        return new RequestContext(UUID.randomUUID().toString(), maskLabels);
    }

    public static RequestContext of(String requestId, boolean maskLabels) {
        return new RequestContext(requestId, maskLabels);
    }
}
