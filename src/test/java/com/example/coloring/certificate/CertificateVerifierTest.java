package com.example.coloring.certificate;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.example.coloring.model.Graph;
import com.example.coloring.model.Graphs;
import com.example.coloring.model.SimpleGraph;
import com.example.coloring.search.ColoringResult;
import com.example.coloring.search.ColoringStatus;
import com.example.coloring.search.ColoringSolver;
import java.math.BigInteger;
import java.util.Map;
import org.junit.jupiter.api.Test;

class CertificateVerifierTest {

    private final Graph c5 = Graphs.cycle(5);
    private final ColoringResult valid = new ColoringSolver().solve(c5);

    @Test
    void acceptsValidOptimalCertificate() {
        CertificateVerdict verdict = CertificateVerifier.verify(c5, valid);
        assertTrue(verdict.accepted(), verdict::detail);
        assertTrue(verdict.detail().contains("chi=3"));
    }

    @Test
    void monochromaticEdgeIsTheReportedFailureCategory() {
        int[] broken = valid.coloring();
        broken[0] = broken[1];
        ColoringResult tampered = copyWith(valid, ColoringStatus.OPTIMAL,
                valid.lowerBound(), valid.upperBound(), broken, valid.cliqueWitness());

        CertificateVerdict verdict = CertificateVerifier.verify(c5, tampered);
        assertFalse(verdict.accepted());
        assertEquals(CertificateError.MONOCHROMATIC_EDGE, verdict.firstError().orElseThrow());
    }

    @Test
    void uncoloredVertexIsDetectedAsItsOwnCategory() {
        int[] broken = valid.coloring();
        broken[2] = -1;
        ColoringResult tampered = copyWith(valid, ColoringStatus.OPTIMAL,
                valid.lowerBound(), valid.upperBound(), broken, valid.cliqueWitness());

        CertificateVerdict verdict = CertificateVerifier.verify(c5, tampered);
        assertEquals(CertificateError.UNCOLORED_VERTEX, verdict.firstError().orElseThrow());
    }

    @Test
    void colorCountMismatchIsDistinctFromMonochromaticEdge() {
        // Proper coloring exists, but bound claims 2 colors while color 2 is used.
        ColoringResult tampered = copyWith(valid, ColoringStatus.OPTIMAL,
                valid.lowerBound(), 2, valid.coloring(), valid.cliqueWitness());

        CertificateVerdict verdict = CertificateVerifier.verify(c5, tampered);
        assertTrue(verdict.errors().contains(CertificateError.COLOR_COUNT_MISMATCH));
        assertFalse(verdict.errors().contains(CertificateError.MONOCHROMATIC_EDGE));
    }

    @Test
    void fakeCliqueWitnessIsRejectedAsNotAClique() {
        int[] fake = {0, 2};
        ColoringResult tampered = copyWith(valid, ColoringStatus.OPTIMAL,
                valid.lowerBound(), valid.upperBound(), valid.coloring(), fake);

        CertificateVerdict verdict = CertificateVerifier.verify(c5, tampered);
        assertEquals(CertificateError.NOT_A_CLIQUE, verdict.firstError().orElseThrow());
    }

    @Test
    void stoppedCertificateMayCarryAGap() {
        ColoringResult stopped = new ColoringSolver().solve(c5, BigInteger.ONE);
        assertEquals(ColoringStatus.STOPPED, stopped.status());
        CertificateVerdict verdict = CertificateVerifier.verify(c5, stopped);
        assertTrue(verdict.accepted(), verdict::detail);
        assertTrue(verdict.detail().contains("2 <= chi <= 3"));
    }

    @Test
    void optimalClaimWithGapIsItsOwnCategory() {
        ColoringResult tampered = copyWith(valid, ColoringStatus.OPTIMAL,
                2, valid.upperBound(), valid.coloring(), valid.cliqueWitness());
        CertificateVerdict verdict = CertificateVerifier.verify(c5, tampered);
        assertTrue(verdict.errors().contains(CertificateError.NOT_PROVEN_OPTIMAL));
    }

    private static ColoringResult copyWith(ColoringResult base, ColoringStatus status,
                                           int lower, int upper, int[] coloring, int[] clique) {
        return new ColoringResult(base.requestId(), status, lower, upper, coloring, clique,
                base.nodesUsed(), base.budget(), base.pruningCounts());
    }
}
