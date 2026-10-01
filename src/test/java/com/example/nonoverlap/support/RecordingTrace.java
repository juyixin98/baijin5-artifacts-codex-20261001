package com.example.nonoverlap.support;

import com.example.nonoverlap.api.Trace;
import com.example.nonoverlap.api.TraceEvent;

import java.util.ArrayList;
import java.util.List;

/** In-memory trace used by tests to assert intermediate state and reasons. */
public final class RecordingTrace implements Trace {

    private final List<TraceEvent> events = new ArrayList<>();

    @Override
    public void emit(TraceEvent event) {
        events.add(event);
    }

    public List<TraceEvent> events() {
        return List.copyOf(events);
    }

    public List<TraceEvent> phase(String phase) {
        return events.stream().filter(e -> e.phase().equals(phase)).toList();
    }

    public boolean contains(String phase, String reasonFragment) {
        return events.stream().anyMatch(e -> e.phase().equals(phase)
                && e.reason().contains(reasonFragment));
    }
}
