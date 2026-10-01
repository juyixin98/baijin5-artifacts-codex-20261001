//! Binary entry: `serve` runs the Axum API, `demo` runs the phase-3 cases
//! offline (no network) and prints categorized verdicts.

use std::sync::Arc;

use collation_agg_contract::{
    api, build_state, verify, ApiRow, AppState, Category, Settings, VerifyRequest,
};

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env().unwrap_or_else(|_| "info".into()),
        )
        .init();

    let cmd = std::env::args().nth(1).unwrap_or_else(|| "serve".into());
    match cmd.as_str() {
        "serve" => serve().await,
        "demo" => demo(),
        other => {
            eprintln!("unknown subcommand {other:?}; expected `serve` or `demo`");
            std::process::exit(2);
        }
    }
}

async fn serve() {
    let state = build_state().unwrap_or_else(|e| fatal(e));
    let addr = state.settings.bind_addr.clone();
    let listener = tokio::net::TcpListener::bind(&addr)
        .await
        .unwrap_or_else(|e| fatal(format!("bind {addr}: {e}")));
    tracing::info!(%addr, "collation-agg-contract listening");
    let app = api::router(state);
    axum::serve(listener, app)
        .await
        .unwrap_or_else(|e| fatal(e));
}

fn fatal<E: std::fmt::Display>(e: E) -> ! {
    eprintln!("fatal: {e}");
    std::process::exit(1);
}

/// Offline run of the phase-3 acceptance cases. Expected answers are authored
/// literally here (not derived from the core), mirroring the integration
/// tests. Exits non-zero if any case lands in an unexpected category.
fn demo() {
    let state = Arc::new(AppState::new(Settings::default()));
    let mut failures = 0;

    // Case 1: accent/case + composed/decomposed equivalence.
    let accent = vec![
        row("r1", "Café"),
        row("r2", "CAFE"),
        row("r3", "cafe\u{0301}"),
        row("r4", "naïve"),
        row("r5", "NAIVE"),
    ];
    failures += run_case(
        &state,
        "accent-case-normalization",
        VerifyRequest {
            request_id: Some("demo-accent".into()),
            rule_version: 1,
            rows: accent,
            session_id: None,
            expect_group_count: Some(2),
            expect_distinct: Some(vec!["Café".into(), "naïve".into()]),
            sensitive: false,
        },
        Category::Accepted,
    );

    // Case 2: natural numeric sequences.
    let nums = vec![
        row("n1", "file2"),
        row("n2", "file10"),
        row("n3", "file2"),
        row("n4", "item1"),
        row("n5", "item01"),
    ];
    failures += run_case(
        &state,
        "numeric-sequences",
        VerifyRequest {
            request_id: Some("demo-numeric".into()),
            rule_version: 1,
            rows: nums,
            session_id: None,
            expect_group_count: Some(3),
            expect_distinct: Some(vec!["file2".into(), "file10".into(), "item1".into()]),
            sensitive: false,
        },
        Category::Accepted,
    );

    // Case 3: rule switch rejected (stateful session pinned to v1).
    let sid = "demo-session";
    let first = VerifyRequest {
        request_id: Some("demo-switch-1".into()),
        rule_version: 1,
        rows: vec![row("s1", "Café")],
        session_id: Some(sid.into()),
        expect_group_count: None,
        expect_distinct: None,
        sensitive: false,
    };
    failures += run_case(&state, "rule-switch-first-v1", first, Category::Accepted);
    let switched = VerifyRequest {
        request_id: Some("demo-switch-2".into()),
        rule_version: 2,
        rows: vec![row("s2", "Café")],
        session_id: Some(sid.into()),
        expect_group_count: None,
        expect_distinct: None,
        sensitive: false,
    };
    failures += run_case(
        &state,
        "rule-switch-then-v2-rejected",
        switched,
        Category::RejectRuleVersionMismatch,
    );

    // Case 4: unknown rule version rejected.
    failures += run_case(
        &state,
        "unknown-rule-rejected",
        VerifyRequest {
            request_id: Some("demo-unknown".into()),
            rule_version: 42,
            rows: vec![row("x", "a")],
            session_id: None,
            expect_group_count: None,
            expect_distinct: None,
            sensitive: false,
        },
        Category::RejectUnknownRule,
    );

    if failures == 0 {
        println!("\nALL DEMO CASES VERIFIED");
    } else {
        eprintln!("\n{failures} DEMO CASE(S) FAILED");
        std::process::exit(1);
    }
}

fn row(id: &str, v: &str) -> ApiRow {
    ApiRow {
        record_id: id.into(),
        value: Some(v.into()),
    }
}

fn run_case(state: &AppState, name: &str, req: VerifyRequest, expected: Category) -> usize {
    let rep = verify(state, req).unwrap_or_else(|e| fatal(e));
    let ok = rep.category == expected;
    println!(
        "[{}] {name}: status={} category={} groups={:?} :: {}",
        if ok { "PASS" } else { "FAIL" },
        rep.status,
        rep.category.as_str(),
        rep.group_count,
        rep.detail
    );
    if !ok {
        eprintln!(
            "  expected category {} but got {}",
            expected.as_str(),
            rep.category.as_str()
        );
        1
    } else {
        0
    }
}
