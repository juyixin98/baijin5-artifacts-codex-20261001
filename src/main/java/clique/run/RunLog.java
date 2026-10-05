package clique.run;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.atomic.AtomicLong;

/**
 * 运行日志：每条记录带运行编号、序号、事件、关键中间状态（detail）与判断理由（reason），
 * 足以在失败后按 runId 重放定位。
 */
public final class RunLog {

    public record Entry(long seq, String runId, String event, String detail, String reason) {
        @Override
        public String toString() {
            return "#" + seq + " [" + runId + "] " + event + " | " + detail + " | reason=" + reason;
        }
    }

    private static final AtomicLong RUN_COUNTER = new AtomicLong();

    private final String runId;
    private final List<Entry> entries = new ArrayList<>();

    public RunLog(String runId) {
        this.runId = runId;
    }

    public static RunLog create() {
        return new RunLog(newRunId());
    }

    public static String newRunId() {
        return "run-" + RUN_COUNTER.incrementAndGet() + "-" + Long.toHexString(System.nanoTime());
    }

    public String runId() {
        return runId;
    }

    public synchronized void log(String event, String detail, String reason) {
        entries.add(new Entry(entries.size() + 1, runId, event, detail, reason));
    }

    public synchronized List<Entry> entries() {
        return List.copyOf(entries);
    }

    public synchronized List<Entry> byEvent(String event) {
        return entries.stream().filter(e -> e.event().equals(event)).toList();
    }

    public synchronized String render() {
        StringBuilder sb = new StringBuilder();
        for (Entry e : entries) {
            sb.append(e).append(System.lineSeparator());
        }
        return sb.toString();
    }
}
