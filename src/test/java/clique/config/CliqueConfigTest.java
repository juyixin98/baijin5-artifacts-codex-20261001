package clique.config;

import clique.error.CliqueException;
import clique.error.ErrorCategory;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class CliqueConfigTest {

    @TempDir
    Path dir;

    @Test
    void bundledDefaultsLoad() {
        CliqueConfig c = CliqueConfig.defaults();
        assertTrue(c.maxSteps() > 0);
        assertEquals(CliqueConfig.PivotStrategy.MAX_INTERSECTION, c.pivot());
        assertEquals(CliqueConfig.Verbosity.SUMMARY, c.verbosity());
        assertTrue(c.bruteForceMaxN() >= 16);
    }

    @Test
    void fileOverridesDefaults() throws IOException {
        Path p = dir.resolve("custom.properties");
        Files.write(p, List.of("clique.maxSteps=1234", "clique.pivot=first", "clique.verbosity=verbose"));
        CliqueConfig c = CliqueConfig.load(p);
        assertEquals(1234L, c.maxSteps());
        assertEquals(CliqueConfig.PivotStrategy.FIRST, c.pivot());
        assertEquals(CliqueConfig.Verbosity.VERBOSE, c.verbosity());
    }

    @Test
    void badNumberIsInputError() throws IOException {
        Path p = dir.resolve("bad.properties");
        Files.write(p, List.of("clique.maxSteps=abc"));
        CliqueException e = assertThrows(CliqueException.class, () -> CliqueConfig.load(p));
        assertEquals(ErrorCategory.INPUT_ERROR, e.category());
    }

    @Test
    void unknownPivotIsInputError() throws IOException {
        Path p = dir.resolve("bad2.properties");
        Files.write(p, List.of("clique.pivot=random"));
        CliqueException e = assertThrows(CliqueException.class, () -> CliqueConfig.load(p));
        assertEquals(ErrorCategory.INPUT_ERROR, e.category());
    }

    @Test
    void missingFileIsInputError() {
        CliqueException e = assertThrows(CliqueException.class,
                () -> CliqueConfig.load(dir.resolve("nope.properties")));
        assertEquals(ErrorCategory.INPUT_ERROR, e.category());
    }
}
