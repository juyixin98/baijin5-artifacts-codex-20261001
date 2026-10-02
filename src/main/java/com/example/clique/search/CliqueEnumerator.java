package com.example.clique.search;

import com.example.clique.error.CliqueException;
import com.example.clique.error.FailureCategory;
import com.example.clique.graph.Graph;
import com.example.clique.runlog.RunLog;

import java.math.BigInteger;
import java.util.ArrayList;
import java.util.List;
import java.util.Objects;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Entry point for maximal clique enumeration. All-or-nothing contract: a run
 * either reports every maximal clique exactly once, or throws a categorized
 * {@link CliqueException}. There is deliberately no "enumeration incomplete"
 * flag and no resumable scan state.
 *
 * <p>Instances are not re-entrant: starting a run while another run is active
 * on the same instance is a {@link FailureCategory#STATE_CONFLICT}.
 */
public final class CliqueEnumerator {

    private final AtomicBoolean active = new AtomicBoolean(false);
    private final RunLog log;

    public CliqueEnumerator() {
        this(null);
    }

    public CliqueEnumerator(RunLog log) {
        this.log = log;
    }

    /** Collecting variant: returns all maximal cliques in deterministic (discovery) order. */
    public List<BigInteger> enumerateMaximal(Graph graph, Strategy strategy, EnumerationLimits limits) {
        List<BigInteger> out = new ArrayList<>();
        enumerateMaximal(graph, strategy, limits, out::add);
        return out;
    }

    public void enumerateMaximal(Graph graph, Strategy strategy, EnumerationLimits limits, CliqueSink sink) {
        requireInput(graph, "graph");
        requireInput(strategy, "strategy");
        requireInput(limits, "limits");
        requireInput(sink, "sink");
        String runId = log == null ? null : log.runId();
        if (!active.compareAndSet(false, true)) {
            throw new CliqueException(FailureCategory.STATE_CONFLICT,
                    "enumerator is already running (instances are not re-entrant)", runId);
        }
        long startNanos = System.nanoTime();
        try {
            logEvent("enumerate-start",
                    "strategy=" + strategy + " vertices=" + graph.vertexCount()
                            + " edges=" + graph.edgeCount() + " maxCliques=" + limits.maxCliques());
            if (graph.vertexCount() == 0) {
                logEvent("enumerate-done", "cliques=0 reason=empty-graph-contract");
                return;
            }
            CountingSink counting = new CountingSink(sink, limits, runId);
            switch (strategy) {
                case PIVOT -> BronKerboschPivot.enumerate(graph, counting);
                case DEGENERACY -> BronKerboschDegeneracy.enumerate(graph, counting);
            }
            long elapsedMs = (System.nanoTime() - startNanos) / 1_000_000;
            logEvent("enumerate-done", "cliques=" + counting.count + " elapsedMs=" + elapsedMs);
        } catch (CliqueException e) {
            logEvent("enumerate-failed", "category=" + e.category() + " reason=" + e.getMessage());
            throw e;
        } catch (RuntimeException e) {
            logEvent("enumerate-failed", "category=COMPUTATION_FAILED reason=" + e);
            throw new CliqueException(FailureCategory.COMPUTATION_FAILED,
                    "unexpected failure during enumeration: " + e, runId, e);
        } finally {
            active.set(false);
        }
    }

    private void logEvent(String kind, String detail) {
        if (log != null) {
            log.event(kind, detail);
        }
    }

    private static void requireInput(Object value, String name) {
        if (Objects.isNull(value)) {
            throw new CliqueException(FailureCategory.INPUT_ERROR, name + " must not be null");
        }
    }

    /** Counts reported cliques and enforces the resource guard. */
    private static final class CountingSink implements CliqueSink {
        private final CliqueSink delegate;
        private final EnumerationLimits limits;
        private final String runId;
        private long count;

        CountingSink(CliqueSink delegate, EnumerationLimits limits, String runId) {
            this.delegate = delegate;
            this.limits = limits;
            this.runId = runId;
        }

        @Override
        public void accept(BigInteger clique) {
            count++;
            if (count > limits.maxCliques()) {
                throw new CliqueException(FailureCategory.RESOURCE_EXHAUSTED,
                        "maximal clique count exceeded limit " + limits.maxCliques()
                                + "; run aborted, no partial result is returned",
                        runId);
            }
            delegate.accept(clique);
        }
    }
}
