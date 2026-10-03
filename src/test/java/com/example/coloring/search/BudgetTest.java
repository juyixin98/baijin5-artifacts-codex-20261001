package com.example.coloring.search;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.example.coloring.TestFixtures;
import com.example.coloring.certificate.CertificateVerifier;
import com.example.coloring.certificate.CertificateVerdict;
import com.example.coloring.diag.CollectingDiagnostics;
import com.example.coloring.diag.PruningReason;
import com.example.coloring.model.Graph;
import com.example.coloring.model.Graphs;
import java.math.BigInteger;
import org.junit.jupiter.api.Test;

/** Budget stop must report only proven bounds and a precise failure category. */
class BudgetTest {

    @Test
    void zeroBudgetOnGapReportsProvenCliqueBoundAndFeasibleUpperBound() {
        Graph c5 = Graphs.cycle(5);
        CollectingDiagnostics diag = new CollectingDiagnostics();
        ColoringSolver solver = new ColoringSolver(diag);

        ColoringResult result = solver.solve(c5, BigInteger.ZERO);

        assertEquals(ColoringStatus.STOPPED, result.status());
        assertFalse(result.chromatic().isPresent(), "stopped result claims no exact value");
        assertEquals(2, result.lowerBound(), "clique bound is proven for free");
        assertEquals(3, result.upperBound(), "greedy coloring is a proven feasible bound");
        assertEquals(BigInteger.ZERO, result.nodesUsed());
        assertTrue(diag.count(PruningReason.BUDGET_EXHAUSTED) >= 1L);

        // Even a STOPPED certificate must be independently valid: both witnesses exist.
        CertificateVerdict verdict = CertificateVerifier.verify(c5, result);
        assertTrue(verdict.accepted(), verdict::detail);
    }

    @Test
    void oneBudgetSpendsExactlyOneNodeThenStops() {
        ColoringResult result = new ColoringSolver().solve(Graphs.cycle(5), BigInteger.ONE);
        assertEquals(ColoringStatus.STOPPED, result.status());
        assertEquals(BigInteger.ONE, result.nodesUsed());
        assertEquals(2, result.lowerBound());
        assertEquals(3, result.upperBound());
    }

    @Test
    void budgetNeverExceedsRequestedAmount() {
        Graph petersen = TestFixtures.load("petersen.graph");
        for (BigInteger budget : new BigInteger[] {
                BigInteger.valueOf(2), BigInteger.valueOf(7), BigInteger.valueOf(50)}) {
            ColoringResult result = new ColoringSolver().solve(petersen, budget);
            assertTrue(result.nodesUsed().compareTo(budget) <= 0,
                    "used " + result.nodesUsed() + " with budget " + budget);
        }
    }

    @Test
    void unlimitedBudgetReachesOptimalOnHardFixture() {
        Graph grotzsch = TestFixtures.load("grotzsch.graph");
        ColoringResult result = new ColoringSolver().solve(grotzsch, ColoringSolver.UNLIMITED_BUDGET);
        assertEquals(ColoringStatus.OPTIMAL, result.status());
        assertEquals(4, result.chromatic().orElseThrow());
        assertEquals(2, result.cliqueWitness().length, "Groetzsch is triangle-free");
    }

    @Test
    void negativeBudgetIsRejected() {
        assertThrows(IllegalArgumentException.class,
                () -> new ColoringSolver().solve(Graphs.cycle(3), BigInteger.valueOf(-1)));
        assertThrows(IllegalArgumentException.class,
                () -> new ColoringSolver().solve(Graphs.cycle(3), null));
    }
}
