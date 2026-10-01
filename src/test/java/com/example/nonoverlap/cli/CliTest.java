package com.example.nonoverlap.cli;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * End-to-end CLI test: spawns the packaged application as a separate native
 * JVM process (java -jar), feeds it a synthetic file, and asserts the concrete
 * status and documented exit codes.
 */
class CliTest {

    private record ProcResult(int exit, String out, String err) {
    }

    private ProcResult runCli(Path instance, String... extra) throws IOException, InterruptedException {
        Path classes = Path.of(System.getProperty("project.classes",
                "target/classes"));
        List<String> cmd = new ArrayList<>();
        cmd.add(Path.of(System.getProperty("java.home"), "bin", "java").toString());
        cmd.add("-cp");
        cmd.add(classes.toString());
        cmd.add("com.example.nonoverlap.cli.Main");
        cmd.add(instance.toString());
        cmd.addAll(List.of(extra));
        ProcessBuilder pb = new ProcessBuilder(cmd);
        pb.redirectErrorStream(false);
        Process p = pb.start();
        String out = new String(p.getInputStream().readAllBytes(), StandardCharsets.UTF_8);
        String err = new String(p.getErrorStream().readAllBytes(), StandardCharsets.UTF_8);
        int code = p.waitFor();
        return new ProcResult(code, out, err);
    }

    private void write(Path f, String content) throws IOException {
        Files.writeString(f, content);
    }

    @Test
    void satExit0WithRunIdAndGrid(@TempDir Path tmp) throws Exception {
        Path f = tmp.resolve("touch.txt");
        write(f, "grid 2 1\nrect A 1 1 TRUE\nrect B 1 1 TRUE\n");
        ProcResult r = runCli(f, "--all", "10", "--log-dir", tmp.resolve("logs").toString());
        assertEquals(0, r.exit(), r.err + r.out);
        assertTrue(r.out.contains("status=SAT"));
        assertTrue(r.out.contains("runId="));
        assertTrue(r.out.contains("solutionCount=2"));
    }

    @Test
    void unsatExit10(@TempDir Path tmp) throws Exception {
        Path f = tmp.resolve("u.txt");
        write(f, "grid 1 1\nrect A 1 1 TRUE\nrect B 1 1 TRUE\n");
        ProcResult r = runCli(f, "--log-dir", tmp.resolve("logs").toString());
        assertEquals(10, r.exit, r.out + r.err);
        assertTrue(r.out.contains("status=UNSAT"));
    }

    @Test
    void stateConflictExit30ForForcedOverlap(@TempDir Path tmp) throws Exception {
        Path f = tmp.resolve("s.txt");
        write(f, "grid 2 2\nrect A 1 1 TRUE\nrect B 1 1 TRUE\nat A 1 1\nat B 1 1\n");
        ProcResult r = runCli(f, "--log-dir", tmp.resolve("logs").toString());
        assertEquals(30, r.exit, r.out + r.err);
        assertTrue(r.out.contains("status=STATE_CONFLICT"));
        assertTrue(r.out.contains("failureKind=STATE"));
    }

    @Test
    void invalidInputExit20AndResourceExit40(@TempDir Path tmp) throws Exception {
        Path bad = tmp.resolve("bad.txt");
        write(bad, "grid x y\n");
        ProcResult inv = runCli(bad);
        assertEquals(20, inv.exit);
        assertTrue(inv.err.contains("INVALID_INPUT"));

        Path f = tmp.resolve("big.txt");
        write(f, "grid 2 1\nrect A 1 1 TRUE\nrect B 1 1 TRUE\nrect C 1 1 UNKNOWN\n");
        ProcResult res = runCli(f, "--max-nodes", "1",
                "--log-dir", tmp.resolve("logs").toString());
        assertEquals(40, res.exit, res.out + res.err);
        assertTrue(res.out.contains("status=RESOURCE_EXHAUSTED"));
    }

    @Test
    void missingFileExit20(@TempDir Path tmp) throws Exception {
        ProcResult r = runCli(tmp.resolve("nope.txt"));
        assertEquals(20, r.exit);
        assertTrue(r.err.contains("not found"));
    }
}
