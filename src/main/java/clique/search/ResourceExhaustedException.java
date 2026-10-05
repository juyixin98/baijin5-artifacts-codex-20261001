package clique.search;

import clique.error.CliqueException;
import clique.error.ErrorCategory;

/** 资源预算耗尽：携带可续扫的部分结果，类别 RESOURCE_EXHAUSTED。 */
public final class ResourceExhaustedException extends CliqueException {
    private final SearchResult partial;

    public ResourceExhaustedException(SearchResult partial, String message) {
        super(ErrorCategory.RESOURCE_EXHAUSTED, message);
        this.partial = partial;
    }

    public SearchResult partial() {
        return partial;
    }
}
