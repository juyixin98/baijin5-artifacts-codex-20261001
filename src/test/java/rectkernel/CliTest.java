package rectkernel;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.ByteArrayOutputStream;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import rectkernel.cli.Main;

/** End-to-end CLI tests: exit codes and machine-readable output per category. */
class CliTest {

    @TempDir
    Path dir;

    private static final class Captured {
        private final ByteArrayOutputStream bos = new ByteArrayOutputStream();
        final PrintStream ps = new PrintStream(bos, true, StandardCharsets.UTF_8);

        String text() {
            return bos.toString(StandardCharsets.UTF_8);
        }
    }

    private Path write(String name, String content) throws Exception {
        Path f = dir.resolve(name);
        Files.writeString(f, content);
        return f;
    }

    private static final class Run {
        int code;
        String out;
        String err;
    }

    private Run cli(String... args) {
        Captured o = new Captured();
        Captured e = new Captured();
        Run r = new Run();
        r.code = Main.run(args, o.ps, e.ps);
        r.out = o.text();
        r.err = e.text();
        return r;
    }

    @Test
    void satRunExitsZeroAndReportsSolutions() throws Exception {
        Path f = write("sat.txt", "run cli-sat\ngrid 2 1\nrect A 1 1 mandatory\nrect B 1 1 mandatory\n");
        Run r = cli("solve", f.toString(), "--all", "--log-dir", dir.toString());
        assertEquals(Main.EXIT_SAT, r.code, r.err);
        assertTrue(r.out.contains("status=SAT"), r.out);
        assertTrue(r.out.contains("solutions=2"), r.out);
        assertTrue(r.out.contains("run=cli-sat"), r.out);
        assertTrue(Files.exists(dir.resolve("run-cli-sat.log")), "run log file written");
    }

    @Test
    void unsatRunExitsTenWithStateConflict() throws Exception {
        Path f = write("unsat.txt", "grid 2 2\nrect A 2 2 mandatory\nrect B 1 1 mandatory\n");
        Run r = cli("solve", f.toString(), "--log-dir", dir.toString());
        assertEquals(Main.EXIT_UNSAT, r.code, r.err);
        assertTrue(r.out.contains("status=UNSAT"), r.out);
        assertTrue(r.out.contains("category=STATE_CONFLICT"), r.out);
    }

    @Test
    void optionalAbsorbedRunIsSat() throws Exception {
        Path f = write("opt.txt", "grid 2 2\nrect A 2 2 mandatory\nrect B 1 1 optional\n");
        Run r = cli("solve", f.toString(), "--all", "--log-dir", dir.toString());
        assertEquals(Main.EXIT_SAT, r.code, r.err);
        assertTrue(r.out.contains("B=A"), r.out);
    }

    @Test
    void zeroAreaRunCountsThreeLayouts() throws Exception {
        Path f = write("zero.txt", "grid 2 1\nrect A 1 1 mandatory at 0 0\nrect B 0 1 mandatory\n");
        Run r = cli("solve", f.toString(), "--all", "--log-dir", dir.toString());
        assertEquals(Main.EXIT_SAT, r.code, r.err);
        assertTrue(r.out.contains("solutions=3"), r.out);
    }

    @Test
    void limitRunExitsTwelveWithResourceExhausted() throws Exception {
        Path f = write("lim.txt",
                "grid 4 4\nrect A 1 1 mandatory\nrect B 1 1 mandatory\nrect C 1 1 mandatory\nlimits 3 10000\n");
        Run r = cli("solve", f.toString(), "--log-dir", dir.toString());
        assertEquals(Main.EXIT_LIMIT, r.code, r.err);
        assertTrue(r.out.contains("status=LIMIT"), r.out);
        assertTrue(r.out.contains("category=RESOURCE_EXHAUSTED"), r.out);
    }

    @Test
    void malformedInputExitsElevenWithLineNumber() throws Exception {
        Path f = write("bad.txt", "grid 2 2\nfrobnicate\n");
        Run r = cli("solve", f.toString());
        assertEquals(Main.EXIT_INPUT, r.code);
        assertTrue(r.err.contains("INPUT_ERROR"), r.err);
        assertTrue(r.err.contains("bad.txt:2"), r.err);

        Path f2 = write("neg.txt", "grid 2 2\nrect A -1 1 mandatory\n");
        Run r2 = cli("solve", f2.toString());
        assertEquals(Main.EXIT_INPUT, r2.code);
        assertTrue(r2.err.contains("INPUT_ERROR"), r2.err);
    }

    @Test
    void missingFileIsInputError() {
        Run r = cli("solve", dir.resolve("no-such-file.txt").toString());
        assertEquals(Main.EXIT_INPUT, r.code);
        assertTrue(r.err.contains("INPUT_ERROR"), r.err);
    }
}
