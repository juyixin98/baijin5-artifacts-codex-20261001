//! IEJoin-style batch inequality join backend.
//!
//! Crate layout (each module has a distinct contract; see module docs
//! for boundary semantics):
//!
//! | module       | responsibility                                            |
//! |--------------|-----------------------------------------------------------|
//! | [`error`]    | four-category error contract (input/state/resource/compute)|
//! | [`batch`]    | typed native columns + arrow2 conversion                  |
//! | [`dto`]      | JSON wire contract and request validation                 |
//! | [`operator`] | query plan, permutations, bitmap, IEJoin core, reference  |
//! | [`resource`] | explicit budgets                                          |
//! | [`state`]    | checkpoints and cursor sessions                           |
//! | [`trace`]    | replayable run records                                    |
//! | [`engine`]   | orchestration, fingerprints, paging, arrow rendering      |
//! | [`fixtures`] | reusable synthetic scenarios and hand-computed answers    |
//! | [`api`]      | Axum HTTP adapter                                         |

pub mod api;
pub mod batch;
pub mod cli;
pub mod dto;
pub mod engine;
pub mod error;
pub mod fixtures;
pub mod operator;
pub mod resource;
pub mod state;
pub mod trace;
