package clique.search;

import clique.error.CliqueException;
import clique.error.ErrorCategory;

import java.util.concurrent.atomic.AtomicBoolean;

/**
 * 取消令牌。支持手工取消，也支持"提交 k 个团后自动取消"的确定性模式（便于测试与限流）。
 * 取消不是错误：算法以 completed=false + 续扫状态正常返回。
 */
public final class CancellationToken {
    private final AtomicBoolean cancelled = new AtomicBoolean(false);
    private final long cancelAfterCommits; // <0 表示不自动取消

    private CancellationToken(long cancelAfterCommits) {
        this.cancelAfterCommits = cancelAfterCommits;
    }

    public static CancellationToken none() {
        return new CancellationToken(-1);
    }

    /** 已提交团数达到 k 时自动取消。 */
    public static CancellationToken afterCommits(long k) {
        if (k < 0) {
            throw new CliqueException(ErrorCategory.INPUT_ERROR, "cancelAfterCommits must be >= 0, got " + k);
        }
        return new CancellationToken(k);
    }

    public void cancel() {
        cancelled.set(true);
    }

    public boolean isCancelled() {
        return cancelled.get();
    }

    /** 由搜索循环在每提交一个外层批次后调用。 */
    void notifyCommitted(long totalCommitted) {
        if (cancelAfterCommits >= 0 && totalCommitted >= cancelAfterCommits) {
            cancel();
        }
    }
}
