package com.example.clique.runlog;

import java.io.BufferedWriter;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.time.Instant;
import java.util.UUID;

/**
 * Append-only run log. Each run gets a unique run id; every line records the
 * run id, an event kind and a human-readable detail (key intermediate states
 * and the reason for decisions), so a failing run can be replayed from
 * {@code <logDir>/<runId>.log}.
 */
public final class RunLog implements AutoCloseable {

    private final String runId;
    private final Path file;
    private final BufferedWriter writer;

    private RunLog(String runId, Path file, BufferedWriter writer) {
        this.runId = runId;
        this.file = file;
        this.writer = writer;
    }

    public static RunLog create(Path directory) {
        return create(directory, UUID.randomUUID().toString());
    }

    public static RunLog create(Path directory, String runId) {
        try {
            Files.createDirectories(directory);
            Path file = directory.resolve(runId + ".log");
            BufferedWriter writer = Files.newBufferedWriter(file, StandardCharsets.UTF_8,
                    StandardOpenOption.CREATE, StandardOpenOption.APPEND);
            RunLog log = new RunLog(runId, file, writer);
            log.event("run-open", "logFile=" + file);
            return log;
        } catch (IOException e) {
            throw new UncheckedIOException("cannot open run log in " + directory, e);
        }
    }

    public String runId() {
        return runId;
    }

    public Path file() {
        return file;
    }

    public synchronized void event(String kind, String detail) {
        try {
            writer.write(Instant.now() + " run=" + runId + " " + kind + " " + detail);
            writer.newLine();
            writer.flush();
        } catch (IOException e) {
            throw new UncheckedIOException("cannot write run log " + file, e);
        }
    }

    @Override
    public synchronized void close() {
        try {
            event("run-close", "logFile=" + file);
            writer.close();
        } catch (IOException e) {
            throw new UncheckedIOException("cannot close run log " + file, e);
        }
    }
}
