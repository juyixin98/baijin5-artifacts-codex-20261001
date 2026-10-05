package clique.search;

import java.math.BigInteger;
import java.util.List;
import java.util.TreeSet;

/**
 * 一次枚举运行的结果。
 * completed=true  表示枚举完整（resumeState 为 null）；
 * completed=false 表示被取消/预算截断，resumeState 非空，可从其续扫。
 */
public record SearchResult(List<BigInteger> cliques, boolean completed, long steps,
                           String runId, ResumeState resumeState) {
    public SearchResult {
        cliques = List.copyOf(cliques);
        if (completed && resumeState != null) {
            throw new IllegalArgumentException("completed result must not carry a resume state");
        }
        if (!completed && resumeState == null) {
            throw new IllegalArgumentException("incomplete result must carry a resume state");
        }
    }

    public TreeSet<BigInteger> cliqueSet() {
        return new TreeSet<>(cliques);
    }
}
