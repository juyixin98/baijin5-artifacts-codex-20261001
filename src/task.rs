//! Task model: a synthetic workload script (Run/Sleep phases) plus the
//! runtime state the scheduler mutates. Blocked (Sleep) time is a distinct
//! state and is never counted as execution.

use serde::{Deserialize, Serialize};
use thiserror::Error;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "lowercase")]
pub enum Phase {
    Run { ms: u64 },
    Sleep { ms: u64 },
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TaskSpec {
    pub name: String,
    pub weight: u64,
    #[serde(default)]
    pub arrival_ms: u64,
    pub script: Vec<Phase>,
    /// How many times the script is executed; 1 = once.
    #[serde(default = "default_repeats")]
    pub repeats: u32,
}

fn default_repeats() -> u32 {
    1
}

#[derive(Debug, Clone, PartialEq, Eq, Error)]
pub enum SpecError {
    #[error("task '{task}' has weight 0")]
    ZeroWeight { task: String },
    #[error("task '{task}' has an empty script")]
    EmptyScript { task: String },
    #[error("task '{task}' has a zero-length phase")]
    ZeroPhase { task: String },
    #[error("task '{task}' has repeats = 0")]
    ZeroRepeats { task: String },
}

impl TaskSpec {
    pub fn validate(&self) -> Result<(), SpecError> {
        if self.weight == 0 {
            return Err(SpecError::ZeroWeight {
                task: self.name.clone(),
            });
        }
        if self.script.is_empty() {
            return Err(SpecError::EmptyScript {
                task: self.name.clone(),
            });
        }
        if self
            .script
            .iter()
            .any(|p| matches!(p, Phase::Run { ms: 0 } | Phase::Sleep { ms: 0 }))
        {
            return Err(SpecError::ZeroPhase {
                task: self.name.clone(),
            });
        }
        if self.repeats == 0 {
            return Err(SpecError::ZeroRepeats {
                task: self.name.clone(),
            });
        }
        Ok(())
    }

    pub fn has_sleep(&self) -> bool {
        self.script.iter().any(|p| matches!(p, Phase::Sleep { .. }))
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum TaskStatus {
    NotArrived,
    Ready,
    Running,
    Sleeping,
    Finished,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct TaskStats {
    pub exec_ms: u64,
    pub wait_ms: u64,
    pub sleep_ms: u64,
    pub max_wait_ms: u64,
    pub schedule_count: u64,
    pub first_scheduled_ms: Option<u64>,
    pub finish_ms: Option<u64>,
}

#[derive(Debug, Clone)]
pub struct Task {
    pub spec: TaskSpec,
    pub status: TaskStatus,
    pub vruntime: u64,
    phase_idx: usize,
    phase_remaining_ms: u64,
    repeats_left: u32,
    pub sleep_until_ms: u64,
    pub ready_since_ms: u64,
    pub stats: TaskStats,
}

impl Task {
    pub fn new(spec: TaskSpec) -> Result<Self, SpecError> {
        spec.validate()?;
        Ok(Self {
            spec,
            status: TaskStatus::NotArrived,
            vruntime: 0,
            phase_idx: 0,
            phase_remaining_ms: 0,
            repeats_left: 0,
            sleep_until_ms: 0,
            ready_since_ms: 0,
            stats: TaskStats::default(),
        })
    }

    /// Bring the task into the system at `now_ms` (its arrival time).
    pub fn activate(&mut self, now_ms: u64) {
        self.repeats_left = self.spec.repeats;
        self.phase_idx = 0;
        self.enter_phase(now_ms);
    }

    fn enter_phase(&mut self, now_ms: u64) {
        match self.spec.script[self.phase_idx] {
            Phase::Run { ms } => {
                self.phase_remaining_ms = ms;
                self.status = TaskStatus::Ready;
                self.ready_since_ms = now_ms;
            }
            Phase::Sleep { ms } => {
                self.phase_remaining_ms = ms;
                self.sleep_until_ms = now_ms + ms;
                self.status = TaskStatus::Sleeping;
            }
        }
    }

    /// Advance the script after `tick_ms` of execution; `now_ms` is the time
    /// at the end of the executed tick. Only valid while Running.
    pub fn on_exec_tick(&mut self, tick_ms: u64, now_ms: u64) {
        self.phase_remaining_ms = self.phase_remaining_ms.saturating_sub(tick_ms);
        if self.phase_remaining_ms > 0 {
            return;
        }
        self.advance_phase(now_ms);
    }

    /// A sleep phase expired at `now_ms`: move the script forward. The task
    /// may become Ready (next Run), keep Sleeping (consecutive Sleep phases)
    /// or Finish (script and repeats exhausted).
    pub fn on_sleep_done(&mut self, now_ms: u64) {
        self.advance_phase(now_ms);
    }

    fn advance_phase(&mut self, now_ms: u64) {
        self.phase_idx += 1;
        if self.phase_idx == self.spec.script.len() {
            self.repeats_left -= 1;
            if self.repeats_left == 0 {
                self.status = TaskStatus::Finished;
                self.stats.finish_ms = Some(now_ms);
                return;
            }
            self.phase_idx = 0;
        }
        // A Run phase hands the task back to the runqueue; a Sleep phase
        // parks it. Either way the engine must re-decide.
        self.enter_phase(now_ms);
    }
}
