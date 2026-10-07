package rectkernel.search;

/** Resource limits and enumeration mode for one solver run. */
public record SolveOptions(long maxNodes, long maxTimeMillis, boolean findAll, long maxSolutions) {

    public static SolveOptions first() {
        return new SolveOptions(1_000_000, 10_000, false, 1);
    }

    public static SolveOptions all(long maxSolutions) {
        return new SolveOptions(2_000_000, 20_000, true, maxSolutions);
    }

    @Override
    public String toString() {
        return "maxNodes=" + maxNodes + " maxTimeMs=" + maxTimeMillis
                + " findAll=" + findAll + " maxSolutions=" + maxSolutions;
    }
}
