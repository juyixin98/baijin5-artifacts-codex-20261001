package rectkernel;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.List;
import org.junit.jupiter.api.Test;
import rectkernel.error.ErrorCategory;
import rectkernel.error.KernelException;
import rectkernel.evidence.RunLog;
import rectkernel.model.Domain;
import rectkernel.model.Problem;
import rectkernel.model.RectSpec;
import rectkernel.search.SolveOptions;
import rectkernel.search.SolveResult;
import rectkernel.search.Solver;

/** Error-contract tests: each failure class is raised and stays distinguishable. */
class ErrorContractTest {

    private static RectSpec m(String id, long w, long h, Domain d) {
        return new RectSpec(id, w, h, false, d);
    }

    @Test
    void negativeSizeIsInputError() {
        KernelException e = assertThrows(KernelException.class, () ->
                Problem.of(2, 2, List.of(m("A", -1, 1, new Domain(0, 1, 0, 1))), "E-neg"));
        assertEquals(ErrorCategory.INPUT_ERROR, e.category());
        assertEquals("E-neg", e.runId());
        assertTrue(e.getMessage().contains("negative size"), e.getMessage());
    }

    @Test
    void invertedDomainIsInputError() {
        KernelException e = assertThrows(KernelException.class, () ->
                Problem.of(4, 4, List.of(m("A", 1, 1, new Domain(2, 1, 0, 0))), "E-inv"));
        assertEquals(ErrorCategory.INPUT_ERROR, e.category());
        assertTrue(e.getMessage().contains("empty position domain"), e.getMessage());
    }

    @Test
    void domainEscapingGridIsInputError() {
        KernelException e = assertThrows(KernelException.class, () ->
                Problem.of(2, 2, List.of(m("A", 2, 2, new Domain(1, 1, 0, 0))), "E-esc"));
        assertEquals(ErrorCategory.INPUT_ERROR, e.category());
        assertTrue(e.getMessage().contains("escapes grid"), e.getMessage());
    }

    @Test
    void duplicateIdIsInputError() {
        KernelException e = assertThrows(KernelException.class, () ->
                Problem.of(2, 2, List.of(
                        m("A", 1, 1, new Domain(0, 1, 0, 1)),
                        m("A", 1, 1, new Domain(0, 1, 0, 1))), "E-dup"));
        assertEquals(ErrorCategory.INPUT_ERROR, e.category());
        assertTrue(e.getMessage().contains("duplicate"), e.getMessage());
    }

    @Test
    void outcomeCategoriesAreDistinguishable() {
        // The four categories are distinct values, and SAT/UNSAT/LIMIT map differently.
        assertEquals(4, ErrorCategory.values().length);
        assertNotEquals(ErrorCategory.STATE_CONFLICT, ErrorCategory.RESOURCE_EXHAUSTED);
        assertNotEquals(ErrorCategory.INPUT_ERROR, ErrorCategory.COMPUTATION_FAILED);

        Problem sat = Problem.of(1, 1, List.of(m("A", 1, 1, Domain.singleton(0, 0))), "E-cat");
        SolveResult satR = new Solver(SolveOptions.first(), RunLog.inMemory(), "E-cat").solve(sat);
        assertEquals(null, satR.category());

        Problem unsat = Problem.of(1, 1, List.of(
                m("A", 1, 1, Domain.singleton(0, 0)),
                m("B", 1, 1, Domain.singleton(0, 0))), "E-cat");
        SolveResult unsatR = new Solver(SolveOptions.first(), RunLog.inMemory(), "E-cat").solve(unsat);
        assertEquals(ErrorCategory.STATE_CONFLICT, unsatR.category());

        Problem hard = Problem.of(4, 4, List.of(
                m("A", 1, 1, new Domain(0, 3, 0, 3)),
                m("B", 1, 1, new Domain(0, 3, 0, 3))), "E-cat");
        SolveResult limitR = new Solver(new SolveOptions(1, 10_000, false, 1),
                RunLog.inMemory(), "E-cat").solve(hard);
        assertEquals(ErrorCategory.RESOURCE_EXHAUSTED, limitR.category());
    }
}
