//! Deterministic resolution budget.

use serde::{Deserialize, Serialize};

/// Resource budget expressed in number of binary resolution steps the engine
/// is allowed to perform. `None` means no explicit cap.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct ResolutionBudget {
    pub max_resolutions: Option<u64>,
}

impl ResolutionBudget {
    pub fn capped(max_resolutions: u64) -> Self {
        ResolutionBudget {
            max_resolutions: Some(max_resolutions),
        }
    }

    pub fn unlimited() -> Self {
        ResolutionBudget {
            max_resolutions: None,
        }
    }

    pub fn allows(&self, used: u64) -> bool {
        match self.max_resolutions {
            Some(cap) => used < cap,
            None => true,
        }
    }
}

/// Result of charging one resolution step against the budget.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Charge {
    Granted,
    Exhausted { used: u64, cap: u64 },
}

#[derive(Debug, Clone, Default)]
pub struct BudgetCounter {
    used: u64,
}

impl BudgetCounter {
    pub fn new() -> Self {
        BudgetCounter { used: 0 }
    }

    pub fn used(&self) -> u64 {
        self.used
    }

    pub fn charge(&mut self, budget: ResolutionBudget) -> Charge {
        match budget.max_resolutions {
            Some(cap) if self.used >= cap => Charge::Exhausted {
                used: self.used,
                cap,
            },
            _ => {
                self.used += 1;
                Charge::Granted
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn enforces_cap() {
        let mut counter = BudgetCounter::new();
        assert_eq!(counter.charge(ResolutionBudget::capped(1)), Charge::Granted);
        assert!(matches!(
            counter.charge(ResolutionBudget::capped(1)),
            Charge::Exhausted { used: 1, cap: 1 }
        ));
    }
}
