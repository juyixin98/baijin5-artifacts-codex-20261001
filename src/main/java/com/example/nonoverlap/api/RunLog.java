package com.example.nonoverlap.api;

import java.io.BufferedWriter;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicLong;

/**
 * File-backed run log. Every solve has a unique {@code runId}; the log captures
 * the initial instance, every branching/propagation decision with its reason,
 * and the final verdict - enough to replay the problem.
 *
 * <p>A failure to open the log target is raised as {@link LogUnavailableException}
 * so the service can classify it as COMPUTATION_FAILED.
 */
public final class RunLog implements Trace, AutoCloseable {

    private static final DateTimeFormatter TS =
            DateTimeFormatter.ofPattern("yyyyMMdd-HHmmss").withZone(ZoneId.systemDefault());
    private static final AtomicLong SEQ = new AtomicLong();

    private final String runId;
    private final Path file;
    private final BufferedWriter writer;

    public RunLog(Path logDir, String instanceName) throws LogUnavailableException {
        this.runId = TS.format(Instant.now()) + "-" + Long.toHexString(System.nanoTime())
                + "-" + UUID.randomUUID().toString().substring(0, 8);
        if (logDir == null) {
            throw new LogUnavailableException("log directory is null", runId, null);
        }
        try {
            if (!Files.exists(logDir)) {
                Files.createDirectories(logDir);
            }
            if (!Files.isDirectory(logDir)) {
                throw new IOException("log path is not a directory: " + logDir);
            }
            this.file = logDir.resolve("run-" + safe(instanceName) + "-" + runId + ".log");
            this.writer = Files.newBufferedWriter(file, StandardCharsets.UTF_8);
            emit("run", "start", Map.of("runId", runId, "instance", instanceName,
                    "logFile", file.toAbsolutePath().toString()));
        } catch (IOException | SecurityException e) {
            throw new LogUnavailableException("cannot open run log in " + logDir, runId, e);
        }
    }

    public static String newRunId() {
        return TS.format(Instant.now()) + "-" + Long.toHexString(System.nanoTime())
                + "-" + UUID.randomUUID().toString().substring(0, 8);
    }

    public String runId() {
        return runId;
    }

    public Path file() {
        return file;
    }

    @Override
    public void emit(TraceEvent event) {
        try {
            writer.write(Instant.now().toString());
            writer.write('\t');
            writer.write(event.phase());
            writer.write('\t');
            writer.write(event.reason());
            writer.write('\t');
            writer.write(event.detail().toString());
            writer.write(System.lineSeparator());
        } catch (IOException e) {
            throw new LogUnavailableException("log write failed for run " + runId, runId, e);
        }
    }

    @Override
    public void close() {
        try {
            writer.close();
        } catch (IOException e) {
            throw new LogUnavailableException("cannot close run log " + file, runId, e);
        }
    }

    private static String safe(String s) {
        return s.replaceAll("[^A-Za-z0-9_.-]", "_");
    }
}
