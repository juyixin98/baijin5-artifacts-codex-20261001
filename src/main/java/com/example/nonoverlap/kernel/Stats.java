package com.example.nonoverlap.kernel;

/** Mutable counters owned by one solve; copied into immutable api stats at the end. */
public final class Stats {
    public int nodes;
    public int branches;
    public int backtracks;
    public int prunes;
    public long pairChecks;

    public void incNodes() { nodes++; }
    public void incBranches() { branches++; }
    public void incBacktracks() { backtracks++; }
    public void addPrunes(int n) { prunes += n; }
    public void incChecks() { pairChecks++; }
}
