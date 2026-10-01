package com.example.nonoverlap.api;

/** Raised when the replay log target cannot be opened or written. */
public class LogUnavailableException extends RuntimeException {

    private final String runId;

    public LogUnavailableException(String message, String runId, Throwable cause) {
        super(message, cause);
        this.runId = runId;
    }

    public String runId() {
        return runId;
    }
}
