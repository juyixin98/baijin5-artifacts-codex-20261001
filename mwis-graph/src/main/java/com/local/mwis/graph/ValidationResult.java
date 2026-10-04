package com.local.mwis.graph;

import java.util.List;

/** Outcome of contract validation: either valid, or a non-empty list of failures. */
public record ValidationResult(List<ValidationFailure> failures) {

    public static ValidationResult ok() {
        return new ValidationResult(List.of());
    }

    public static ValidationResult of(List<ValidationFailure> failures) {
        return new ValidationResult(List.copyOf(failures));
    }

    public boolean isValid() {
        return failures.isEmpty();
    }

    /** First failure category, for callers that classify on the primary problem. */
    public FailureCategory primaryCategory() {
        if (failures.isEmpty()) {
            throw new IllegalStateException("no failures present");
        }
        return failures.get(0).category();
    }
}
