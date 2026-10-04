package com.local.mwis.service;

/** One structured log line: request identity, pipeline stage, sequence, message. */
public record LogEntry(String requestId, String stage, int sequence, String message) {

    @Override
    public String toString() {
        return "[" + requestId + "][" + stage + "][#" + sequence + "] " + message;
    }
}
