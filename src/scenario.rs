//! Scenario definitions: synthetic workload fixtures, either compiled-in
//! (used by the API and tests) or loaded from JSON files (CLI --fixture).

use serde::{Deserialize, Serialize};
use thiserror::Error;

use crate::config::{ConfigError, SchedulerConfig};
use crate::task::{Phase, SpecError, TaskSpec};

#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct CheckSpec {
    /// Assert long-term shares match w_i/sum(w) for the always-runnable cohort.
    pub shares: bool,
    /// Assert max wait <= scheduling period + tick (stable cohorts only).
    pub wait_bound: bool,
    /// Assert every task finishes its script within the duration.
    pub expect_all_finished: bool,
    pub share_tolerance: f64,
}

impl Default for CheckSpec {
    fn default() -> Self {
        Self {
            shares: false,
            wait_bound: false,
            expect_all_finished: false,
            share_tolerance: 0.02,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Scenario {
    pub name: String,
    #[serde(default)]
    pub description: String,
    pub duration_ms: u64,
    #[serde(default)]
    pub config: SchedulerConfig,
    #[serde(default)]
    pub checks: CheckSpec,
    pub tasks: Vec<TaskSpec>,
}

#[derive(Debug, Clone, PartialEq, Eq, Error)]
pub enum ScenarioError {
    #[error("scenario name is empty")]
    EmptyName,
    #[error("duration_ms must be >= 1")]
    ZeroDuration,
    #[error("scenario has no tasks")]
    NoTasks,
    #[error("invalid scheduler config: {0}")]
    Config(#[from] ConfigError),
    #[error("invalid task spec: {0}")]
    Spec(#[from] SpecError),
    #[error("duplicate task name '{0}'")]
    DuplicateTask(String),
    #[error("unknown scenario '{0}'")]
    Unknown(String),
    #[error("failed to parse scenario JSON: {0}")]
    Parse(String),
}

impl Scenario {
    pub fn validate(&self) -> Result<(), ScenarioError> {
        if self.name.is_empty() {
            return Err(ScenarioError::EmptyName);
        }
        if self.duration_ms == 0 {
            return Err(ScenarioError::ZeroDuration);
        }
        if self.tasks.is_empty() {
            return Err(ScenarioError::NoTasks);
        }
        self.config.validate()?;
        for spec in &self.tasks {
            spec.validate()?;
        }
        let mut names: Vec<_> = self.tasks.iter().map(|t| t.name.as_str()).collect();
        names.sort_unstable();
        if let Some(dup) = names.windows(2).find(|w| w[0] == w[1]) {
            return Err(ScenarioError::DuplicateTask(dup[0].to_string()));
        }
        Ok(())
    }

    pub fn from_json(json: &str) -> Result<Self, ScenarioError> {
        let scenario: Scenario =
            serde_json::from_str(json).map_err(|e| ScenarioError::Parse(e.to_string()))?;
        scenario.validate()?;
        Ok(scenario)
    }
}

fn always_on(name: &str, weight: u64, duration_ms: u64) -> TaskSpec {
    TaskSpec {
        name: name.to_string(),
        weight,
        arrival_ms: 0,
        script: vec![Phase::Run { ms: duration_ms }],
        repeats: 1,
    }
}

/// Two always-on tasks, weights 1:3. Expect 25%/75% long-term shares.
fn fair_weights_1_3() -> Scenario {
    let duration_ms = 6_000;
    Scenario {
        name: "fair-weights-1-3".to_string(),
        description: "two always-on tasks, weights 1024:3072 (1:3)".to_string(),
        duration_ms,
        config: SchedulerConfig::default(),
        checks: CheckSpec {
            shares: true,
            wait_bound: true,
            expect_all_finished: false,
            share_tolerance: 0.02,
        },
        tasks: vec![
            always_on("light", 1024, duration_ms),
            always_on("heavy", 3072, duration_ms),
        ],
    }
}

/// Three equal always-on tasks; exercises the wait bound with n=3.
fn equal_trio() -> Scenario {
    let duration_ms = 3_000;
    Scenario {
        name: "equal-trio".to_string(),
        description: "three equal-weight always-on tasks".to_string(),
        duration_ms,
        config: SchedulerConfig::default(),
        checks: CheckSpec {
            shares: true,
            wait_bound: true,
            expect_all_finished: false,
            share_tolerance: 0.02,
        },
        tasks: vec![
            always_on("a", 1024, duration_ms),
            always_on("b", 1024, duration_ms),
            always_on("c", 1024, duration_ms),
        ],
    }
}

/// One periodic task: run 3ms, sleep 5ms, 100 times. Blocked time must show
/// up as sleep (and CPU idle), never as execution.
fn periodic_sleeper() -> Scenario {
    Scenario {
        name: "periodic-sleeper".to_string(),
        description: "one task cycling run 3ms / sleep 5ms, 100 periods".to_string(),
        duration_ms: 800,
        config: SchedulerConfig::default(),
        checks: CheckSpec {
            shares: false,
            wait_bound: false,
            expect_all_finished: true,
            share_tolerance: 0.02,
        },
        tasks: vec![TaskSpec {
            name: "sleeper".to_string(),
            weight: 1024,
            arrival_ms: 0,
            script: vec![Phase::Run { ms: 3 }, Phase::Sleep { ms: 5 }],
            repeats: 100,
        }],
    }
}

/// An always-on background task plus 20 short tasks arriving every 50ms.
/// Short tasks must be served promptly; the CPU must never idle while work
/// is pending.
fn short_task_storm() -> Scenario {
    let duration_ms = 1_200;
    let mut tasks = vec![always_on("background", 1024, duration_ms)];
    for i in 0..20 {
        tasks.push(TaskSpec {
            name: format!("short-{i:02}"),
            weight: 1024,
            arrival_ms: 100 + 50 * i as u64,
            script: vec![Phase::Run { ms: 2 }],
            repeats: 1,
        });
    }
    Scenario {
        name: "short-task-storm".to_string(),
        description: "always-on background task plus 20 short tasks, one every 50ms"
            .to_string(),
        duration_ms,
        config: SchedulerConfig::default(),
        checks: CheckSpec {
            shares: false,
            wait_bound: false,
            expect_all_finished: false,
            share_tolerance: 0.02,
        },
        tasks,
    }
}

/// Nothing to do for the first 100ms: the CPU must idle, and idle time must
/// be conserved in the totals.
fn idle_trace() -> Scenario {
    Scenario {
        name: "idle-trace".to_string(),
        description: "single task arriving at t=100ms; CPU idles beforehand".to_string(),
        duration_ms: 300,
        config: SchedulerConfig::default(),
        checks: CheckSpec {
            shares: false,
            wait_bound: false,
            expect_all_finished: true,
            share_tolerance: 0.02,
        },
        tasks: vec![TaskSpec {
            name: "late".to_string(),
            weight: 1024,
            arrival_ms: 100,
            script: vec![Phase::Run { ms: 50 }],
            repeats: 1,
        }],
    }
}

pub fn builtin_scenarios() -> Vec<Scenario> {
    vec![
        fair_weights_1_3(),
        equal_trio(),
        periodic_sleeper(),
        short_task_storm(),
        idle_trace(),
    ]
}

pub fn find_builtin(name: &str) -> Result<Scenario, ScenarioError> {
    builtin_scenarios()
        .into_iter()
        .find(|s| s.name == name)
        .ok_or_else(|| ScenarioError::Unknown(name.to_string()))
}
