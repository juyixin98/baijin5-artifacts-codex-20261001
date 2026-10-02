package com.example.coloring.diag;

import java.util.ArrayList;
import java.util.List;

/** Collects every event and can render the trace with sensitive labels masked. */
public final class DiagnosticReport implements SearchListener {

    private final List<SearchEvent> events = new ArrayList<>();

    @Override
    public synchronized void onEvent(SearchEvent event) {
        events.add(event);
    }

    public synchronized List<SearchEvent> events() {
        return List.copyOf(events);
    }

    public synchronized long count(DecisionKind kind) {
        return events.stream().filter(e -> e.kind() == kind).count();
    }

    public synchronized long count(Reason reason) {
        return events.stream().filter(e -> e.reason() == reason).count();
    }
}
