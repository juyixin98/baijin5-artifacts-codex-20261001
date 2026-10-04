//! Resource-control knobs enforced at every trust boundary:
//! request body size (Axum layer), keys per encode, cells per table
//! (both when creating and when parsing foreign bytes).

use crate::error::IbltError;

#[derive(Debug, Clone, Copy)]
pub struct Limits {
    /// Maximum cells in any table this process will build or accept.
    pub max_cells: usize,
    /// Maximum keys accepted in a single encode request.
    pub max_keys: usize,
    /// Maximum HTTP request body size in bytes.
    pub max_body_bytes: usize,
}

impl Default for Limits {
    fn default() -> Self {
        Limits {
            max_cells: 1 << 16,
            max_keys: 1 << 14,
            max_body_bytes: 1 << 20,
        }
    }
}

impl Limits {
    pub fn check_cell_count(&self, cells: usize) -> Result<(), IbltError> {
        if cells > self.max_cells {
            return Err(IbltError::ResourceExhausted(format!(
                "cell count {cells} exceeds limit {}",
                self.max_cells
            )));
        }
        Ok(())
    }

    pub fn check_key_count(&self, keys: usize) -> Result<(), IbltError> {
        if keys > self.max_keys {
            return Err(IbltError::ResourceExhausted(format!(
                "key count {keys} exceeds limit {}",
                self.max_keys
            )));
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::error::ErrorCategory;

    #[test]
    fn limits_are_enforced_with_resource_category() {
        let l = Limits { max_cells: 10, max_keys: 5, max_body_bytes: 100 };
        assert!(l.check_cell_count(10).is_ok());
        let err = l.check_cell_count(11).unwrap_err();
        assert_eq!(err.category(), ErrorCategory::ResourceExhausted);
        assert!(l.check_key_count(5).is_ok());
        assert_eq!(
            l.check_key_count(6).unwrap_err().category(),
            ErrorCategory::ResourceExhausted
        );
    }
}
