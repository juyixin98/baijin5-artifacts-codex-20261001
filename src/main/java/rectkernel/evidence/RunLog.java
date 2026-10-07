package rectkernel.evidence;

import java.io.IOException;
import java.io.PrintWriter;
import java.io.UncheckedIOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

/**
 * Structured run log. Every event carries a sequence number, the run id, an
 * event kind (branch / prune / force-absent / conflict / backtrack / limit /
 * solution / finish / cross-check ...) and a human-readable reason, so any
 * run can be replayed from its log alone.
 */
public final class RunLog implements AutoCloseable {

    public record Event(long seq, String runId, String kind, String detail) {
    }

    private final List<Event> events = new ArrayList<>();
    private final PrintWriter sink;
    private long seq;

    private RunLog(PrintWriter sink) {
        this.sink = sink;
    }

    public static RunLog inMemory() {
        return new RunLog(null);
    }

    public static RunLog toFile(Path file) {
        try {
            Files.createDirectories(file.getParent());
            return new RunLog(new PrintWriter(Files.newBufferedWriter(file)));
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }

    public synchronized void event(String runId, String kind, String detail) {
        Event e = new Event(++seq, runId, kind, detail);
        events.add(e);
        if (sink != null) {
            sink.println("seq=" + e.seq() + " run=" + e.runId() + " kind=" + e.kind() + " :: " + e.detail());
            sink.flush();
        }
    }

    public synchronized List<Event> events() {
        return List.copyOf(events);
    }

    public boolean hasKind(String kind) {
        return events().stream().anyMatch(e -> e.kind().equals(kind));
    }

    @Override
    public void close() {
        if (sink != null) {
            sink.close();
        }
    }
}
