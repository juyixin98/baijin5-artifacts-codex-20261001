package com.example.clique.search;

import com.example.clique.error.CliqueException;
import com.example.clique.error.FailureCategory;

/**
 * Resource guards for one enumeration run. When a guard fires the run fails
 * with {@link FailureCategory#RESOURCE_EXHAUSTED}; no partial result and no
 * "incomplete" flag is ever returned (all-or-nothing contract).
 */
public record EnumerationLimits(long maxCliques) {

    private static final EnumerationLimits UNLIMITED = new EnumerationLimits(Long.MAX_VALUE);

    public EnumerationLimits {
        if (maxCliques <= 0) {
            throw new CliqueException(FailureCategory.INPUT_ERROR,
                    "maxCliques must be positive, got " + maxCliques);
        }
    }

    public static EnumerationLimits unlimited() {
        return UNLIMITED;
    }

    public static EnumerationLimits ofMaxCliques(long maxCliques) {
        return new EnumerationLimits(maxCliques);
    }
}
