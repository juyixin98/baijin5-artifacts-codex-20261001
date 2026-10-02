//! 引擎与调度器集成测试。
//!
//! 参考值来源：全部手工按模型公式推算（见各测试注释），不是从被测实现回抄。
//! 设备模型（默认）：seek = 0.5ms + 磁道差 * 0.02ms，传输 = 扇区数 * 0.01ms，
//! 每磁道 256 扇区。

use iosched_compare::engine::{CancelOutcome, Engine, Event};
use iosched_compare::model::{
    CancelSpec, DeviceModel, Direction, ErrorCategory, RequestSpec, Trace,
};
use iosched_compare::scheduler::Scheduler;
use iosched_compare::scheduler::deadline::{DeadlineConfig, DeadlineScheduler};
use iosched_compare::scheduler::scan::ScanScheduler;

fn req(
    id: &str,
    lba: u64,
    sectors: u64,
    dir: Direction,
    arrival: u64,
    deadline: Option<u64>,
) -> RequestSpec {
    RequestSpec {
        id: id.to_string(),
        lba,
        sectors,
        direction: dir,
        arrival_ms: arrival,
        deadline_ms: deadline,
    }
}

fn read(id: &str, lba: u64, sectors: u64, arrival: u64) -> RequestSpec {
    req(id, lba, sectors, Direction::Read, arrival, None)
}

fn run_with(scheduler: &mut dyn Scheduler, trace: &Trace) -> iosched_compare::engine::RunOutcome {
    let engine = Engine::new(DeviceModel::default()).unwrap();
    engine
        .run(trace, scheduler, 500, 5000)
        .expect("trace should be valid")
}

fn dispatch_reasons(outcome: &iosched_compare::engine::RunOutcome) -> Vec<String> {
    outcome
        .events
        .iter()
        .filter_map(|e| match e {
            Event::Dispatched { reason, .. } => Some(reason.clone()),
            _ => None,
        })
        .collect()
}

/// 浮点断言用近似比较（f64 求和顺序相关，容差 1e-9）。
fn approx(a: f64, b: f64) -> bool {
    (a - b).abs() < 1e-9
}

/// 顺序读：4 个相邻请求合并为一个批次，但 4 个身份各自完成、各记一笔。
#[test]
fn sequential_merge_preserves_all_identities() {
    let trace = Trace {
        requests: vec![
            read("seq-r1", 0, 8, 0),
            read("seq-r2", 8, 8, 0),
            read("seq-r3", 16, 8, 0),
            read("seq-r4", 24, 8, 0),
        ],
        cancels: vec![],
    };
    for name in ["deadline", "scan"] {
        let outcome = match name {
            "deadline" => run_with(
                &mut DeadlineScheduler::new(DeadlineConfig::default()),
                &trace,
            ),
            _ => run_with(&mut ScanScheduler::new(), &trace),
        };
        // 合并：只有 1 次下发，但 4 条完成记录。
        let dispatches = dispatch_reasons(&outcome);
        assert_eq!(
            dispatches.len(),
            1,
            "{name}: adjacent requests must merge into one batch"
        );
        assert_eq!(
            outcome.completions.len(),
            4,
            "{name}: every original identity completes"
        );
        assert_eq!(
            outcome.metrics.dispatch_order,
            vec!["seq-r1", "seq-r2", "seq-r3", "seq-r4"],
            "{name}: merged batch keeps original order"
        );
        // 手算：seek = 0.5 + 0 磁道差，传输 = 32 * 0.01 = 0.32 → 完成于 0.82ms。
        for c in &outcome.completions {
            assert_eq!(c.wait_ms, 0.0, "{name}: {} dispatched at t=0", c.request_id);
            assert!(
                approx(c.finish_ms, 0.82),
                "{name}: {} finishes at 0.82, got {}",
                c.request_id,
                c.finish_ms
            );
            assert_eq!(
                c.merged_with.len(),
                3,
                "{name}: {} sees the other 3 group members",
                c.request_id
            );
        }
        assert_eq!(
            outcome.metrics.total_seek_sectors, 0,
            "{name}: head starts at lba 0"
        );
        // 不丢不重：完成数 == 提交数，且每个 id 恰好一次。
        assert_eq!(outcome.metrics.completed, outcome.metrics.submitted);
        let mut ids: Vec<_> = outcome
            .completions
            .iter()
            .map(|c| c.request_id.clone())
            .collect();
        ids.sort();
        ids.dedup();
        assert_eq!(ids.len(), 4, "{name}: no duplicate completions");
    }
}

/// 随机读：SCAN 按电梯序，deadline 按到达 FIFO；两者的下发顺序与寻道成本
/// 都是手算的参考值。
#[test]
fn random_trace_order_and_seek_cost() {
    let trace = Trace {
        requests: vec![
            read("rnd-a", 1000, 8, 0),
            read("rnd-b", 200, 8, 0),
            read("rnd-c", 5000, 8, 0),
            read("rnd-d", 300, 8, 0),
        ],
        cancels: vec![],
    };

    // SCAN（磁头从 0 向上）：200 → 300 → 1000 → 5000。
    // 手算寻道（注意磁头停在上一请求的末尾之后）：
    // 0→200 = 200；208→300 = 92；308→1000 = 692；1008→5000 = 3992；合计 4976。
    let scan = run_with(&mut ScanScheduler::new(), &trace);
    assert_eq!(
        scan.metrics.dispatch_order,
        vec!["rnd-b", "rnd-d", "rnd-a", "rnd-c"]
    );
    assert_eq!(scan.metrics.total_seek_sectors, 4976);

    // deadline（同方向 FIFO，全部未过期）：按到达序 a → b → c → d。
    // 手算寻道：0→1000 = 1000；1008→200 = 808；208→5000 = 4792；5008→300 = 4708；合计 11308。
    let dl = run_with(
        &mut DeadlineScheduler::new(DeadlineConfig::default()),
        &trace,
    );
    assert_eq!(
        dl.metrics.dispatch_order,
        vec!["rnd-a", "rnd-b", "rnd-c", "rnd-d"]
    );
    assert_eq!(dl.metrics.total_seek_sectors, 11308);

    // 结论可解释：同一轨迹下 SCAN 的寻道成本显著低于 FIFO 序。
    assert!(scan.metrics.total_seek_sectors < dl.metrics.total_seek_sectors);
}

/// 过期规则可解释：写请求在等一个长读期间过期，下一批必须以
/// `deadline_expired:write_head ...` 的原因被选中。
#[test]
fn deadline_expiry_is_reported_with_reason() {
    let trace = Trace {
        requests: vec![
            // r0 先被选中（deadline 1 最早），服务耗时 0.5 + 2.56 = 3.06ms。
            req("exp-r0", 0, 256, Direction::Read, 0, Some(1)),
            // w1 在 t=2 过期，但设备忙到 3.06。
            req("exp-w1", 1000, 8, Direction::Write, 0, Some(2)),
            // r1 截止 500，远未过期。
            req("exp-r1", 2000, 8, Direction::Read, 0, Some(500)),
        ],
        cancels: vec![],
    };
    let outcome = run_with(
        &mut DeadlineScheduler::new(DeadlineConfig::default()),
        &trace,
    );
    assert_eq!(
        outcome.metrics.dispatch_order,
        vec!["exp-r0", "exp-w1", "exp-r1"]
    );
    let reasons = dispatch_reasons(&outcome);
    assert_eq!(reasons[0], "earliest_deadline:read at t=1");
    assert_eq!(
        reasons[1],
        "deadline_expired:write_head expired at t=2 (now=3.060)"
    );
    // exp-r0 完成于 3.06 > 1（miss）；exp-w1 完成于 3.06 + 0.62 = 3.68 > 2（miss）；
    // exp-r1 完成于 3.68 + 0.66 = 4.34 < 500（未 miss）。合计 2 次 miss。
    assert_eq!(outcome.metrics.deadline_misses, 2);
    let w1 = outcome
        .completions
        .iter()
        .find(|c| c.request_id == "exp-w1")
        .unwrap();
    assert!(w1.deadline_missed);
    assert!(
        approx(w1.finish_ms, 3.68),
        "w1 finishes at 3.68, got {}",
        w1.finish_ms
    );
}

/// 写饥饿保护：连续 2 批读后，第 3 批必须让位给写，原因记录为 starvation_guard。
#[test]
fn starvation_guard_forces_write_after_two_read_batches() {
    let trace = Trace {
        requests: vec![
            read("mix-r1", 100, 8, 0),
            read("mix-r2", 200, 8, 0),
            read("mix-r3", 300, 8, 0),
            req("mix-w1", 400, 8, Direction::Write, 0, None),
        ],
        cancels: vec![],
    };
    let outcome = run_with(
        &mut DeadlineScheduler::new(DeadlineConfig::default()),
        &trace,
    );
    assert_eq!(
        outcome.metrics.dispatch_order,
        vec!["mix-r1", "mix-r2", "mix-w1", "mix-r3"],
        "writes_starved=2 must preempt the third consecutive read batch"
    );
    let reasons = dispatch_reasons(&outcome);
    assert!(
        reasons[2].starts_with("starvation_guard:writes_starved>=2"),
        "third dispatch must cite the starvation guard, got: {}",
        reasons[2]
    );

    // 对照：SCAN 无饥饿概念，严格按 LBA 序。
    let scan = run_with(&mut ScanScheduler::new(), &trace);
    assert_eq!(
        scan.metrics.dispatch_order,
        vec!["mix-r1", "mix-r2", "mix-r3", "mix-w1"]
    );
}

/// 边界取消：四种取消结果类别各命中一次，且账目平衡（不丢不重）。
#[test]
fn cancel_outcome_depends_on_request_state() {
    let trace = Trace {
        requests: vec![
            read("cx-a", 0, 256, 0),   // t=0 下发，服务 3.06ms
            read("cx-b", 1000, 8, 0),  // 排队中
            read("cx-c", 2000, 8, 10), // t=10 才到达
            read("cx-d", 3000, 8, 0),  // 排队中，稍后完成
        ],
        cancels: vec![
            CancelSpec {
                request_id: "cx-a".into(),
                at_ms: 1,
            }, // 在飞 → 拒绝
            CancelSpec {
                request_id: "cx-b".into(),
                at_ms: 1,
            }, // 在队列 → 成功
            CancelSpec {
                request_id: "cx-c".into(),
                at_ms: 1,
            }, // 未到达 → 拒绝
            CancelSpec {
                request_id: "cx-d".into(),
                at_ms: 100,
            }, // 已完成 → 拒绝
        ],
    };
    let outcome = run_with(&mut ScanScheduler::new(), &trace);

    let cancel_outcome = |id: &str| {
        outcome.events.iter().find_map(|e| match e {
            Event::Cancelled {
                request_id,
                outcome,
                ..
            } if request_id == id => Some(outcome.clone()),
            _ => None,
        })
    };
    assert_eq!(
        cancel_outcome("cx-a"),
        Some(CancelOutcome::RejectedInFlight)
    );
    assert_eq!(cancel_outcome("cx-b"), Some(CancelOutcome::CancelledQueued));
    assert_eq!(
        cancel_outcome("cx-c"),
        Some(CancelOutcome::RejectedNotArrived)
    );
    assert_eq!(
        cancel_outcome("cx-d"),
        Some(CancelOutcome::RejectedTerminal)
    );

    // cx-a 的取消被拒绝后必须照常完成。
    assert!(outcome.completions.iter().any(|c| c.request_id == "cx-a"));
    // cx-b 被成功取消，绝不能完成。
    assert!(!outcome.completions.iter().any(|c| c.request_id == "cx-b"));

    // 账目：提交 4 = 完成 3 + 成功取消 1；每个 id 终态唯一。
    assert_eq!(outcome.metrics.submitted, 4);
    assert_eq!(outcome.metrics.completed, 3);
    assert_eq!(outcome.metrics.cancelled_queued, 1);
    assert_eq!(outcome.metrics.cancel_rejected_in_flight, 1);
    assert_eq!(outcome.metrics.cancel_rejected_not_arrived, 1);
    assert_eq!(outcome.metrics.cancel_rejected_terminal, 1);

    // 手算顺序与寻道：a(0→256) → d(256→3000) → c(3008→2000)。
    assert_eq!(outcome.metrics.dispatch_order, vec!["cx-a", "cx-d", "cx-c"]);
    assert_eq!(outcome.metrics.total_seek_sectors, 2744 + 1008);
}

/// 所有夹具 × 两个调度器：不丢不重恒等式。
#[test]
fn no_request_lost_or_duplicated_across_fixtures() {
    for fixture in ["sequential", "random", "mixed_rw", "cancel_boundary"] {
        let text = std::fs::read_to_string(format!("fixtures/{fixture}.json")).unwrap();
        let trace: Trace = serde_json::from_str(&text).unwrap();
        let submitted: std::collections::HashSet<String> =
            trace.requests.iter().map(|r| r.id.clone()).collect();

        for name in ["deadline", "scan"] {
            let outcome = match name {
                "deadline" => run_with(
                    &mut DeadlineScheduler::new(DeadlineConfig::default()),
                    &trace,
                ),
                _ => run_with(&mut ScanScheduler::new(), &trace),
            };
            let completed: std::collections::HashSet<String> = outcome
                .completions
                .iter()
                .map(|c| c.request_id.clone())
                .collect();
            let cancelled: std::collections::HashSet<String> = outcome
                .events
                .iter()
                .filter_map(|e| match e {
                    Event::Cancelled {
                        request_id,
                        outcome: CancelOutcome::CancelledQueued,
                        ..
                    } => Some(request_id.clone()),
                    _ => None,
                })
                .collect();
            assert!(
                completed.is_disjoint(&cancelled),
                "{fixture}/{name}: a request cannot both complete and be cancelled"
            );
            assert_eq!(
                completed
                    .union(&cancelled)
                    .cloned()
                    .collect::<std::collections::HashSet<_>>(),
                submitted,
                "{fixture}/{name}: every submitted request reaches exactly one terminal state"
            );
            // 下发序列无重复。
            let mut order = outcome.metrics.dispatch_order.clone();
            order.sort();
            order.dedup();
            assert_eq!(
                order.len(),
                outcome.metrics.dispatch_order.len(),
                "{fixture}/{name}: duplicate dispatch"
            );
        }
    }
}

/// 输入校验：每种非法输入对应明确的失败类别。
#[test]
fn validation_errors_have_specific_categories() {
    let engine = Engine::new(DeviceModel::default()).unwrap();
    let mut sched = ScanScheduler::new();

    // 越界：capacity 1_048_576，lba 1_048_570 + 8 = 1_048_578 超出。
    let trace = Trace {
        requests: vec![read("bad-range", 1_048_570, 8, 0)],
        cancels: vec![],
    };
    let errors = engine.run(&trace, &mut sched, 500, 5000).unwrap_err();
    assert_eq!(errors[0].category, ErrorCategory::SectorOutOfRange);
    assert_eq!(errors[0].request_id.as_deref(), Some("bad-range"));

    // 空请求。
    let trace = Trace {
        requests: vec![read("bad-empty", 0, 0, 0)],
        cancels: vec![],
    };
    let errors = engine.run(&trace, &mut sched, 500, 5000).unwrap_err();
    assert_eq!(errors[0].category, ErrorCategory::EmptyRequest);

    // 重复 id。
    let trace = Trace {
        requests: vec![read("dup", 0, 8, 0), read("dup", 8, 8, 0)],
        cancels: vec![],
    };
    let errors = engine.run(&trace, &mut sched, 500, 5000).unwrap_err();
    assert_eq!(errors[0].category, ErrorCategory::DuplicateRequestId);

    // 取消不存在的请求。
    let trace = Trace {
        requests: vec![read("ok", 0, 8, 0)],
        cancels: vec![CancelSpec {
            request_id: "ghost".into(),
            at_ms: 0,
        }],
    };
    let errors = engine.run(&trace, &mut sched, 500, 5000).unwrap_err();
    assert_eq!(errors[0].category, ErrorCategory::UnknownRequest);
}

/// 截止等待：deadline 调度器同方向是 FIFO（只看队头截止期），排在长请求后面的
/// 紧截止请求会发生队头阻塞；miss 计数只统计真正超时的完成。
#[test]
fn deadline_miss_count_reflects_actual_lateness() {
    // 同一轨迹，仅 dl-tight 的截止期不同：t=1 必超时，t=4 刚好赶得上。
    // 手算：dl-long 服务 0.5 + 256*0.01 = 3.06ms；dl-tight 于 3.06 下发，
    // 服务 0.5 + 磁道差(256→1000: 1→3 = 2)*0.02 + 0.08 = 0.62，完成于 3.68。
    for (tight_deadline, expected_misses) in [(1u64, 1usize), (4, 0)] {
        let trace = Trace {
            requests: vec![
                req("dl-long", 0, 256, Direction::Read, 0, Some(5000)),
                req(
                    "dl-tight",
                    1000,
                    8,
                    Direction::Read,
                    0,
                    Some(tight_deadline),
                ),
            ],
            cancels: vec![],
        };
        let outcome = run_with(
            &mut DeadlineScheduler::new(DeadlineConfig::default()),
            &trace,
        );
        // FIFO：dl-long 是队头，即使 dl-tight 截止更早也不能插队。
        assert_eq!(outcome.metrics.dispatch_order, vec!["dl-long", "dl-tight"]);
        assert_eq!(
            outcome.metrics.deadline_misses, expected_misses,
            "tight deadline {tight_deadline} should yield {expected_misses} miss(es)"
        );
        let tight = outcome
            .completions
            .iter()
            .find(|c| c.request_id == "dl-tight")
            .unwrap();
        assert_eq!(tight.deadline_missed, expected_misses == 1);
        assert!(
            approx(tight.wait_ms, 3.06),
            "waits for the long request's service, got {}",
            tight.wait_ms
        );
        assert!(
            approx(tight.finish_ms, 3.68),
            "finishes at 3.68, got {}",
            tight.finish_ms
        );
    }
}
