package com.example.coloring.diag;

import java.util.Collections;
import java.util.EnumMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CopyOnWriteArrayList;

/** Diagnostic sink that keeps events and tallies counts per {@link PruningReason}. */
public final class CollectingDiagnostics implements DiagnosticSink {

    private final List<DiagnosticEvent> events = new CopyOnWriteArrayList<>();
    private final EnumMap<PruningReason, Long> counts = new EnumMap<>(PruningReason.class);

    @Override
    public void accept(DiagnosticEvent event) {
        events.add(event);
        counts.merge(event.reason(), 1L, Long::sum);
    }

    public List<DiagnosticEvent> events() {
        return Collections.unmodifiableList(events);
    }

    /** Number of recorded events with the given reason. */
    public long count(PruningReason reason) {
        return counts.getOrDefault(reason, 0L);
    }

    public Map<PruningReason, Long> counts() {
        return Collections.unmodifiableMap(new EnumMap<>(counts));
    }

    /** Events in recorded order for a single decision-tree node. */
    public List<DiagnosticEvent> atNode(long nodeId) {
        return events.stream().filter(e -> e.nodeId() == nodeId).toList();
    }

    /** Compact ordered view, useful for diagnostic output and tests. */
    public Map<Long, List<PruningReason>> timeline() {
        Map<Long, List<PruningReason>> out = new LinkedHashMap<>();
        for (DiagnosticEvent event : events) {
            out.computeIfAbsent(event.nodeId(), k -> new java.util.ArrayList<>()).add(event.reason());
        }
        return Collections.unmodifiableMap(out);
    }
}
