//! Boundary-stability analysis: given the boundary lists of an original and
//! a locally modified input, compute the byte range in which chunking
//! actually changed. Used by the evidence report (`cdc-report`) and the
//! stability integration tests.

/// Where the chunking of a modified input diverges from the original.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ChangedRange {
    /// Number of leading boundaries identical in both inputs.
    pub prefix_boundaries: usize,
    /// Number of trailing boundaries identical once the length shift is
    /// accounted for.
    pub suffix_boundaries: usize,
    /// First boundary (in modified coordinates) that does not match the
    /// original stream.
    pub first_changed: u64,
    /// First boundary (in modified coordinates) from which both streams agree
    /// again. `None` when the streams never resynchronize.
    pub resync_at: Option<u64>,
}

impl ChangedRange {
    /// Byte span affected by the modification, in modified coordinates.
    pub fn span(&self) -> Option<u64> {
        self.resync_at.map(|r| r.saturating_sub(self.first_changed))
    }
}

/// Compare boundary lists. `before` and `after` are absolute chunk-start
/// offsets (both starting at 0). `shift` is the length delta of the
/// modification (`after_len - before_len`); trailing boundaries of `before`
/// shifted by `shift` are expected to reappear in `after`.
pub fn changed_range(before: &[u64], after: &[u64], shift: i64) -> ChangedRange {
    let mut prefix = 0;
    while prefix < before.len() && prefix < after.len() && before[prefix] == after[prefix] {
        prefix += 1;
    }

    let mut suffix = 0;
    while suffix < before.len().saturating_sub(prefix)
        && suffix < after.len().saturating_sub(prefix)
    {
        let b = before[before.len() - 1 - suffix] as i64 + shift;
        let a = after[after.len() - 1 - suffix] as i64;
        if b != a {
            break;
        }
        suffix += 1;
    }

    let first_changed = after.get(prefix).copied().unwrap_or_else(|| {
        after.last().copied().unwrap_or(0)
    });
    let resync_at = if suffix > 0 {
        Some(after[after.len() - suffix])
    } else {
        None
    };

    ChangedRange {
        prefix_boundaries: prefix,
        suffix_boundaries: suffix,
        first_changed,
        resync_at,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn no_change_means_full_prefix() {
        let b = vec![0, 100, 250, 400];
        let r = changed_range(&b, &b, 0);
        assert_eq!(r.prefix_boundaries, 4);
        assert_eq!(r.suffix_boundaries, 0);
    }

    #[test]
    fn insertion_localizes_change() {
        let before = vec![0, 100, 200, 300, 400];
        // 50 bytes inserted at 150: boundaries at 0,100 survive; 200.. shift.
        let after = vec![0, 100, 180, 250, 350, 450];
        let r = changed_range(&before, &after, 50);
        assert_eq!(r.prefix_boundaries, 2);
        assert_eq!(r.first_changed, 180);
        assert_eq!(r.suffix_boundaries, 3); // 250==200+50, 350==300+50, 450==400+50
        assert_eq!(r.resync_at, Some(250));
        assert_eq!(r.span(), Some(70));
    }

    #[test]
    fn no_resync_is_reported() {
        let before = vec![0, 100];
        let after = vec![0, 55, 77];
        let r = changed_range(&before, &after, 1);
        assert_eq!(r.prefix_boundaries, 1);
        assert_eq!(r.suffix_boundaries, 0);
        assert_eq!(r.resync_at, None);
        assert_eq!(r.span(), None);
    }
}
