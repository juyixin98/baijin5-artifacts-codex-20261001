package com.example.nonoverlap.io;

import com.example.nonoverlap.model.Existence;
import com.example.nonoverlap.model.Placement;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class TextInstanceParserTest {

    private final TextInstanceParser parser = new TextInstanceParser();

    @Test
    void parsesCommentsAliasesAndPreassignments() {
        String text = """
                # a comment
                name demo
                grid 3 2
                rect A 2 1 REQUIRED
                rect B 1 1 ?
                at A 0 0
                at B 1 0
                at B 2 0
                """;
        var parsed = parser.parseString(text, "x");
        assertEquals("demo", parsed.instance.name());
        assertEquals(3, parsed.instance.gridWidth());
        assertEquals(Existence.TRUE, parsed.instance.rect(0).existence());
        assertEquals(Existence.UNKNOWN, parsed.instance.rect(1).existence());
        assertEquals(List.of(new Placement(0, 0)), parsed.preassign.get("A"));
        assertEquals(List.of(new Placement(1, 0), new Placement(2, 0)),
                parsed.preassign.get("B"));
    }

    @Test
    void reportsLineNumbersForBadExistenceAndTokens() {
        InstanceFormatException e1 = assertThrows(InstanceFormatException.class,
                () -> parser.parseString("grid 1 1\nrect A 1 1 MAYBE\n", "x"));
        assertEquals(2, e1.line());
        assertTrue(e1.getMessage().contains("existence"));

        InstanceFormatException e2 = assertThrows(InstanceFormatException.class,
                () -> parser.parseString("grid 2 2\nrect A 1\n", "x"));
        assertEquals(2, e2.line());
        assertTrue(e2.getMessage().contains("tokens"));
    }

    @Test
    void rejectsNonPositiveGridNegativeSizesUnknownAtAndMissingDirectives() {
        assertTrue(assertThrows(InstanceFormatException.class,
                () -> parser.parseString("grid 0 1\nrect A 1 1 TRUE\n", "x")).getMessage()
                .contains("positive"));
        assertTrue(assertThrows(InstanceFormatException.class,
                () -> parser.parseString("grid 2 2\nrect A -1 1 TRUE\n", "x")).getMessage()
                .contains("negative"));
        assertTrue(assertThrows(InstanceFormatException.class,
                () -> parser.parseString("grid 2 2\nrect A 1 1 TRUE\nat X 0 0\n", "x")).getMessage()
                .contains("unknown rect"));
        assertTrue(assertThrows(InstanceFormatException.class,
                () -> parser.parseString("name x\n", "x")).getMessage()
                .contains("grid"));
        assertTrue(assertThrows(InstanceFormatException.class,
                () -> parser.parseString("grid 2 2\n", "x")).getMessage()
                .contains("rect"));
    }
}
