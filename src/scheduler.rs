//! The scheduler engine: tick-based simulation loop implementing the
//! CFS-style policy on a single virtual CPU.
//!
//! Per tick, in order:
//! 1. arrivals become Ready (placed at min_vruntime, no bonus);
//! 2. sleepers whose sleep expired wake (placed at
//!    max(own_v, min_vruntime - wakeup_bonus), so the bonus is bounded and a
//!    waking task cannot preempt others indefinitely);
//! 3. the current task is preempted if a strictly smaller-vruntime task is
//!    queued AND it has consumed its full slice;
//! 4. the leftmost task is picked if the CPU is free;
//! 5. one tick of execution: exec/vruntime accounting, script advance;
//! 6. wait/sleep accounting (blocked time is never counted as execution);
//! 7. min_vruntime is advanced monotonically.

use serde::Serialize;
use thiserror::Error;

use crate::config::{delta_vruntime, SchedulerConfig};
use crate::runqueue::RunQueue;
use crate::task::{SpecError, Task, TaskSpec, TaskStatus};

const MAX_EVENTS: usize = 100_000;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub enum PlaceReason {
    NewTask,
    Wakeup,
    Preempted,
    PhaseYield,
}

#[derive(Debug, Clone, Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum Event {
    Placed {
        tick_ms: u64,
        task: String,
        vruntime: u64,
        min_vruntime: u64,
        reason: PlaceReason,
    },
    Finished {
        tick_ms: u64,
        task: String,
    },
}

#[derive(Debug, Clone, Serialize, serde::Deserialize)]
pub struct TaskSample {
    pub name: String,
    pub exec_ms: u64,
    pub vruntime: u64,
    pub status: TaskStatus,
}

#[derive(Debug, Clone, Serialize, serde::Deserialize)]
pub struct Sample {
    pub tick_ms: u64,
    pub idle_ms: u64,
    pub min_vruntime: u64,
    pub tasks: Vec<TaskSample>,
}

#[derive(Debug)]
pub struct RunOutcome {
    pub elapsed_ms: u64,
    pub idle_ms: u64,
    pub busy_ms: u64,
    pub min_vruntime: u64,
    pub tasks: Vec<Task>,
    pub events: Vec<Event>,
    pub samples: Vec<Sample>,
}

#[derive(Debug, Clone, PartialEq, Eq, Error)]
pub enum RunFailure {
    #[error("exceeded max tick budget of {max_ticks}")]
    ExceededMaxTicks { max_ticks: u64 },
}

pub struct Engine {
    cfg: SchedulerConfig,
    tasks: Vec<Task>,
    rq: RunQueue,
    clock_ms: u64,
    current: Option<usize>,
    /// Task that ran during the current tick, even if it has since blocked
    /// or finished; its vruntime still advances min_vruntime.
    executed_this_tick: Option<usize>,
    run_started_ms: u64,
    idle_ms: u64,
    min_vruntime: u64,
    duration_ms: u64,
    max_ticks: u64,
    sample_every_ms: u64,
    events: Vec<Event>,
    samples: Vec<Sample>,
}

impl Engine {
    pub fn new(
        cfg: SchedulerConfig,
        specs: Vec<TaskSpec>,
        duration_ms: u64,
    ) -> Result<Self, SpecError> {
        let tasks = specs
            .into_iter()
            .map(Task::new)
            .collect::<Result<Vec<_>, _>>()?;
        Ok(Self::with_limits(cfg, tasks, duration_ms, 5_000_000))
    }

    fn with_limits(
        cfg: SchedulerConfig,
        tasks: Vec<Task>,
        duration_ms: u64,
        max_ticks: u64,
    ) -> Self {
        // Aim for ~500 samples per run regardless of duration.
        let sample_every_ms = (duration_ms / 500).max(1);
        Self {
            cfg,
            tasks,
            rq: RunQueue::new(),
            clock_ms: 0,
            current: None,
            executed_this_tick: None,
            run_started_ms: 0,
            idle_ms: 0,
            min_vruntime: 0,
            duration_ms,
            max_ticks,
            sample_every_ms,
            events: Vec::new(),
            samples: Vec::new(),
        }
    }

    /// Test hook: shrink the tick budget so the failure path is cheap to hit.
    pub fn with_max_ticks(mut self, max_ticks: u64) -> Self {
        self.max_ticks = max_ticks;
        self
    }

    pub fn run(mut self) -> Result<RunOutcome, RunFailure> {
        loop {
            // Stop early once every task has run its script to completion.
            // NotArrived tasks with a future arrival keep the loop going so
            // the CPU idles until they show up.
            if self
                .tasks
                .iter()
                .all(|t| t.status == TaskStatus::Finished)
            {
                break;
            }
            if self.clock_ms >= self.duration_ms {
                // Include events landing exactly on the window boundary
                // (a final wakeup may complete a task), then stop.
                let now = self.clock_ms;
                self.process_arrivals(now);
                self.process_wakeups(now);
                break;
            }
            let ticks = self.clock_ms / self.cfg.tick_ms;
            if ticks >= self.max_ticks {
                return Err(RunFailure::ExceededMaxTicks {
                    max_ticks: self.max_ticks,
                });
            }
            self.step();
        }
        let busy_ms: u64 = self.tasks.iter().map(|t| t.stats.exec_ms).sum();
        Ok(RunOutcome {
            elapsed_ms: self.clock_ms,
            idle_ms: self.idle_ms,
            busy_ms,
            min_vruntime: self.min_vruntime,
            tasks: self.tasks,
            events: self.events,
            samples: self.samples,
        })
    }

    fn step(&mut self) {
        let now = self.clock_ms;
        self.executed_this_tick = None;
        self.process_arrivals(now);
        self.process_wakeups(now);
        self.maybe_preempt(now);
        if self.current.is_none() {
            self.pick_next(now);
        }
        self.execute_tick(now);
        self.account_waiting_and_sleeping();
        self.update_min_vruntime();
        if now.is_multiple_of(self.sample_every_ms) {
            self.record_sample(now);
        }
        self.clock_ms += self.cfg.tick_ms;
    }

    fn process_arrivals(&mut self, now: u64) {
        for id in 0..self.tasks.len() {
            let task = &self.tasks[id];
            if task.status != TaskStatus::NotArrived || task.spec.arrival_ms > now {
                continue;
            }
            self.tasks[id].activate(now);
            if self.tasks[id].status == TaskStatus::Ready {
                self.enqueue(id, PlaceReason::NewTask);
            }
        }
    }

    fn process_wakeups(&mut self, now: u64) {
        for id in 0..self.tasks.len() {
            let task = &self.tasks[id];
            if task.status != TaskStatus::Sleeping || task.sleep_until_ms > now {
                continue;
            }
            // The sleep phase is over: advance the script first, then act on
            // the phase it lands on.
            self.tasks[id].on_sleep_done(now);
            match self.tasks[id].status {
                TaskStatus::Ready => {
                    self.tasks[id].ready_since_ms = now;
                    self.enqueue(id, PlaceReason::Wakeup);
                }
                TaskStatus::Finished => {
                    self.push_event(Event::Finished {
                        tick_ms: now,
                        task: self.tasks[id].spec.name.clone(),
                    });
                }
                TaskStatus::Sleeping => {} // consecutive Sleep phases
                _ => unreachable!("sleep completion only yields Ready/Sleeping/Finished"),
            }
        }
    }

    fn maybe_preempt(&mut self, now: u64) {
        let Some(cur) = self.current else { return };
        let Some((_, left_v)) = self.rq.peek_min() else { return };
        if left_v >= self.tasks[cur].vruntime {
            return; // current is still the most-deserving task
        }
        let total_weight = self.rq.total_weight() + self.tasks[cur].spec.weight;
        let slice = self
            .cfg
            .slice_ms(self.tasks[cur].spec.weight, total_weight);
        if now - self.run_started_ms >= slice {
            self.tasks[cur].status = TaskStatus::Ready;
            self.enqueue(cur, PlaceReason::Preempted);
            self.current = None;
        }
    }

    fn pick_next(&mut self, now: u64) {
        let Some((id, _)) = self.rq.pop_min() else {
            return;
        };
        self.rq.remove_weight(self.tasks[id].spec.weight);
        let task = &mut self.tasks[id];
        task.status = TaskStatus::Running;
        task.stats.schedule_count += 1;
        let waited = now - task.ready_since_ms;
        task.stats.max_wait_ms = task.stats.max_wait_ms.max(waited);
        if task.stats.first_scheduled_ms.is_none() {
            task.stats.first_scheduled_ms = Some(now);
        }
        self.current = Some(id);
        self.run_started_ms = now;
    }

    fn execute_tick(&mut self, now: u64) {
        let tick = self.cfg.tick_ms;
        let Some(cur) = self.current else {
            self.idle_ms += tick;
            return;
        };
        let end_of_tick = now + tick;
        let weight = self.tasks[cur].spec.weight;
        let status_after;
        {
            let task = &mut self.tasks[cur];
            task.stats.exec_ms += tick;
            task.vruntime += delta_vruntime(tick, weight);
            task.on_exec_tick(tick, end_of_tick);
            status_after = task.status;
        }
        self.executed_this_tick = Some(cur);
        match status_after {
            TaskStatus::Running => {}
            TaskStatus::Ready => {
                // Run phase ended into another Run phase: requeue fairly.
                self.enqueue(cur, PlaceReason::PhaseYield);
                self.current = None;
            }
            TaskStatus::Sleeping => {
                self.current = None;
            }
            TaskStatus::Finished => {
                self.push_event(Event::Finished {
                    tick_ms: end_of_tick,
                    task: self.tasks[cur].spec.name.clone(),
                });
                self.current = None;
            }
            TaskStatus::NotArrived => unreachable!("running task cannot be NotArrived"),
        }
    }

    /// Every tick an arrived, unfinished task is in exactly one state;
    /// Running was already accounted as exec in execute_tick. The task that
    /// executed this tick is skipped even if its script advanced into a
    /// Sleep/Ready phase mid-tick: the tick belongs to execution.
    fn account_waiting_and_sleeping(&mut self) {
        let tick = self.cfg.tick_ms;
        for (id, task) in self.tasks.iter_mut().enumerate() {
            if self.executed_this_tick == Some(id) {
                continue;
            }
            match task.status {
                TaskStatus::Ready => task.stats.wait_ms += tick,
                TaskStatus::Sleeping => task.stats.sleep_ms += tick,
                _ => {}
            }
        }
    }

    fn update_min_vruntime(&mut self) {
        let cur_v = self.current.map(|id| self.tasks[id].vruntime);
        let rq_v = self.rq.peek_min().map(|(_, v)| v);
        let ran_v = self.executed_this_tick.map(|id| self.tasks[id].vruntime);
        if let Some(candidate) = cur_v.into_iter().chain(rq_v).chain(ran_v).min() {
            self.min_vruntime = self.min_vruntime.max(candidate);
        }
    }

    /// Placement rule: new tasks start at min_vruntime; woken tasks keep
    /// their own vruntime but may be credited at most `wakeup_bonus` behind
    /// min_vruntime; preempted/yielding tasks keep their exact vruntime.
    fn enqueue(&mut self, id: usize, reason: PlaceReason) {
        let weight = self.tasks[id].spec.weight;
        let vruntime = match reason {
            PlaceReason::NewTask => self.min_vruntime,
            PlaceReason::Wakeup => {
                let floor = self
                    .min_vruntime
                    .saturating_sub(self.cfg.wakeup_bonus_vruntime(weight));
                self.tasks[id].vruntime.max(floor)
            }
            PlaceReason::Preempted | PlaceReason::PhaseYield => self.tasks[id].vruntime,
        };
        self.tasks[id].vruntime = vruntime;
        self.tasks[id].status = TaskStatus::Ready;
        self.tasks[id].ready_since_ms = self.clock_ms;
        if matches!(reason, PlaceReason::NewTask | PlaceReason::Wakeup) {
            self.push_event(Event::Placed {
                tick_ms: self.clock_ms,
                task: self.tasks[id].spec.name.clone(),
                vruntime,
                min_vruntime: self.min_vruntime,
                reason,
            });
        }
        self.rq.insert(id, vruntime, weight);
    }

    fn record_sample(&mut self, now: u64) {
        let tasks = self
            .tasks
            .iter()
            .map(|t| TaskSample {
                name: t.spec.name.clone(),
                exec_ms: t.stats.exec_ms,
                vruntime: t.vruntime,
                status: t.status,
            })
            .collect();
        self.samples.push(Sample {
            tick_ms: now,
            idle_ms: self.idle_ms,
            min_vruntime: self.min_vruntime,
            tasks,
        });
    }

    fn push_event(&mut self, event: Event) {
        if self.events.len() < MAX_EVENTS {
            self.events.push(event);
        }
    }
}
