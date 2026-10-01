//! Query operators: the IEJoin core, its physical primitives, and the
//! independent nested-loop reference used as a test oracle.

pub mod bitmap;
pub mod comparator;
pub mod iejoin;
pub mod permutation;
pub mod plan;
pub mod reference;

pub use bitmap::{BitMap, Counters};
pub use comparator::Comparator;
pub use iejoin::{JoinOutput, JoinRunner, MatchPair, Page, PreparedJoin};
pub use plan::{JoinPlan, Predicate};
pub use reference::{NestedLoopResult, NestedLoopStats};
