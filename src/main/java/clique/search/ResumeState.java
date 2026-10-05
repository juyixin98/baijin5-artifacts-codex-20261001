package clique.search;

import clique.error.CliqueException;
import clique.error.ErrorCategory;
import clique.graph.Graph;

/**
 * 稳定续扫状态：以退化排序外层下标为粒度。
 * 合法范围：同一图（n、edgeCount、fingerprint 全部一致）且 0 <= nextOuterIndex <= n。
 * 外层下标 i 产出的团两两不相交（团含 order[i] 且其余顶点都在其之后），
 * 因此从 nextOuterIndex 续扫与全量运行的并集恰好等于全量结果，不重不漏。
 */
public final class ResumeState {
    public static final String FORMAT = "BKRS1";

    private final int n;
    private final int edgeCount;
    private final int fingerprint;
    private final int nextOuterIndex;
    private final long emittedBefore;

    public ResumeState(int n, int edgeCount, int fingerprint, int nextOuterIndex, long emittedBefore) {
        this.n = n;
        this.edgeCount = edgeCount;
        this.fingerprint = fingerprint;
        this.nextOuterIndex = nextOuterIndex;
        this.emittedBefore = emittedBefore;
    }

    public int nextOuterIndex() {
        return nextOuterIndex;
    }

    public long emittedBefore() {
        return emittedBefore;
    }

    /** 校验该状态能否用于给定图；不一致抛 STATE_CONFLICT。 */
    public void validateFor(Graph g) {
        if (n != g.n() || edgeCount != g.edgeCount() || fingerprint != g.fingerprint()) {
            throw new CliqueException(ErrorCategory.STATE_CONFLICT,
                    "resume state does not match graph: state(n=" + n + ",m=" + edgeCount
                            + ",fp=" + fingerprint + ") vs graph(n=" + g.n() + ",m=" + g.edgeCount()
                            + ",fp=" + g.fingerprint() + ")");
        }
        if (nextOuterIndex < 0 || nextOuterIndex > g.n()) {
            throw new CliqueException(ErrorCategory.STATE_CONFLICT,
                    "nextOuterIndex " + nextOuterIndex + " out of range [0," + g.n() + "]");
        }
    }

    public String encode() {
        return FORMAT + ":" + n + ":" + edgeCount + ":" + fingerprint + ":" + nextOuterIndex + ":" + emittedBefore;
    }

    public static ResumeState parse(String s) {
        if (s == null) {
            throw new CliqueException(ErrorCategory.INPUT_ERROR, "resume state string is null");
        }
        String[] parts = s.trim().split(":");
        if (parts.length != 6 || !parts[0].equals(FORMAT)) {
            throw new CliqueException(ErrorCategory.INPUT_ERROR,
                    "malformed resume state (expect " + FORMAT + ":n:m:fingerprint:next:emitted): '" + s + "'");
        }
        try {
            int n = Integer.parseInt(parts[1]);
            int m = Integer.parseInt(parts[2]);
            int fp = Integer.parseInt(parts[3]);
            int next = Integer.parseInt(parts[4]);
            long emitted = Long.parseLong(parts[5]);
            if (n < 0 || m < 0 || next < 0 || emitted < 0) {
                throw new CliqueException(ErrorCategory.INPUT_ERROR,
                        "resume state has negative fields: '" + s + "'");
            }
            return new ResumeState(n, m, fp, next, emitted);
        } catch (NumberFormatException e) {
            throw new CliqueException(ErrorCategory.INPUT_ERROR, "malformed resume state numbers: '" + s + "'");
        }
    }

    @Override
    public String toString() {
        return encode();
    }
}
