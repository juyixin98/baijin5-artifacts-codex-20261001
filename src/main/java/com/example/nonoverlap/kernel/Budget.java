package com.example.nonoverlap.kernel;

/** Hard stop limits for a solve. Non-positive values are treated as "no limit" on checks;
 *  nodes must be &ge; 1 (validated by the service layer). */
public record Budget(int maxNodes, long maxPairChecks) {

    public static Budget unlimited() {
        return new Budget(Integer.MAX_VALUE, Long.MAX_VALUE);
    }

    public boolean exhausted(Stats stats) {
        return stats.nodes >= maxNodes || stats.pairChecks >= maxPairChecks;
    }
}
