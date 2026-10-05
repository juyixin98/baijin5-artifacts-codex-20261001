package clique.error;

/** 所有可预期失败都带类别，调用方可按 category() 区分处理。 */
public class CliqueException extends RuntimeException {
    private final ErrorCategory category;

    public CliqueException(ErrorCategory category, String message) {
        super(message);
        this.category = category;
    }

    public ErrorCategory category() {
        return category;
    }
}
