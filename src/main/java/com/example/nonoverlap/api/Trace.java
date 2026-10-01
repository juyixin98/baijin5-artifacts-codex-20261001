package com.example.nonoverlap.api;

import java.util.Map;

/** Sink for structured, replayable search trace events. */
public interface Trace {

    void emit(TraceEvent event);

    default void emit(String phase, String reason) {
        emit(TraceEvent.of(phase, reason));
    }

    default void emit(String phase, String reason, Map<String, ?> detail) {
        emit(TraceEvent.of(phase, reason, detail));
    }

    /** A no-op trace used unless the caller asks for a run log. */
    static Trace noop() {
        return event -> { };
    }
}
