package com.example.clique.error;

/**
 * Single exception type for the engine. Carries a {@link FailureCategory} and
 * the run id (when a {@code RunLog} was attached) so a failing run can be
 * replayed from its log file.
 */
public class CliqueException extends RuntimeException {

    private final FailureCategory category;
    private final String runId;

    public CliqueException(FailureCategory category, String message) {
        this(category, message, null, null);
    }

    public CliqueException(FailureCategory category, String message, String runId) {
        this(category, message, runId, null);
    }

    public CliqueException(FailureCategory category, String message, String runId, Throwable cause) {
        super(message, cause);
        this.category = category;
        this.runId = runId;
    }

    public FailureCategory category() {
        return category;
    }

    /** Run id for log replay, or {@code null} when no run log was attached. */
    public String runId() {
        return runId;
    }

    @Override
    public String getMessage() {
        String base = super.getMessage();
        return runId == null ? base : base + " [run=" + runId + "]";
    }
}
