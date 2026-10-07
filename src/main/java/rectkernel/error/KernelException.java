package rectkernel.error;

/** Typed kernel failure: always carries a category and the run id for log replay. */
public class KernelException extends RuntimeException {

    private final ErrorCategory category;
    private final String runId;

    public KernelException(ErrorCategory category, String runId, String message) {
        super(message);
        this.category = category;
        this.runId = runId;
    }

    public ErrorCategory category() {
        return category;
    }

    public String runId() {
        return runId;
    }
}
