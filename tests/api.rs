//! HTTP 接口端到端夹具：状态码契约、页故障判别式、诊断 run_id、快照持久化。

use axum::body::Body;
use axum::http::{Request, StatusCode};
use mmu_teach::api::{router, AppState};
use mmu_teach::{Machine, RunLog};
use std::sync::Arc;
use tokio::sync::RwLock;
use tower::ServiceExt; // oneshot

fn json_post(uri: &str, body: serde_json::Value) -> Request<Body> {
    Request::builder()
        .method("POST")
        .uri(uri)
        .header("content-type", "application/json")
        .body(Body::from(body.to_string()))
        .unwrap()
}

async fn state(frames: u64) -> (AppState, Arc<RwLock<Machine>>) {
    let log = RunLog::new();
    let machine = Arc::new(RwLock::new(Machine::new(frames, log)));
    (AppState::from_shared(machine.clone()), machine)
}

async fn body_json(resp: axum::response::Response) -> serde_json::Value {
    let bytes = axum::body::to_bytes(resp.into_body(), 4 * 1024 * 1024)
        .await
        .unwrap();
    serde_json::from_slice(&bytes).unwrap()
}

#[tokio::test]
async fn happy_path_translate_returns_200_and_paddr() {
    let (s, _m) = state(4096).await;
    let app = router(s);

    let c = app
        .clone()
        .oneshot(json_post(
            "/api/v1/processes",
            serde_json::json!({"name":"p"}),
        ))
        .await
        .unwrap();
    assert_eq!(c.status(), StatusCode::CREATED);
    let pc = body_json(c).await;
    let asid = pc["asid"].as_u64().unwrap();

    let m = app
        .clone()
        .oneshot(json_post(
            &format!("/api/v1/processes/{asid}/mappings"),
            serde_json::json!({"vaddr":"0x4000","level":3,"ppn":77,"read":true,"write":true,"execute":true,"user":true}),
        ))
        .await
        .unwrap();
    assert_eq!(m.status(), StatusCode::CREATED);
    let mb = body_json(m).await;
    assert!(mb["flush"]["entries_removed"].is_number());

    let t = app
        .oneshot(json_post(
            "/api/v1/translate",
            serde_json::json!({"asid":asid,"vaddr":"0x4010","op":"read"}),
        ))
        .await
        .unwrap();
    assert_eq!(t.status(), StatusCode::OK);
    let tb = body_json(t).await;
    assert_eq!(tb["result"], "ok");
    assert_eq!(tb["translation"]["paddr"], 77 * 4096 + 0x10);
}

#[tokio::test]
async fn page_fault_is_200_discriminated_not_500() {
    let (s, _m) = state(4096).await;
    let app = router(s);
    let c = app
        .clone()
        .oneshot(json_post("/api/v1/processes", serde_json::json!({})))
        .await
        .unwrap();
    let asid = body_json(c).await["asid"].as_u64().unwrap();

    let r = app
        .oneshot(json_post(
            "/api/v1/translate",
            serde_json::json!({"asid":asid,"vaddr":"0xdead_0000","op":"read"}),
        ))
        .await
        .unwrap();
    assert_eq!(r.status(), StatusCode::OK, "页故障是正常模拟结果，应为 200");
    let b = body_json(r).await;
    assert_eq!(b["result"], "page_fault");
    assert_eq!(b["fault"]["kind"], "miss");
}

#[tokio::test]
async fn input_conflict_exhausted_have_distinct_status_and_kind() {
    // 400：非规范地址（但进程存在，先建进程）。
    let (s, _m) = state(4096).await;
    let app = router(s);
    let c = app
        .clone()
        .oneshot(json_post("/api/v1/processes", serde_json::json!({})))
        .await
        .unwrap();
    let asid = body_json(c).await["asid"].as_u64().unwrap();

    let bad = app
        .clone()
        .oneshot(json_post(
            "/api/v1/translate",
            serde_json::json!({"asid":asid,"vaddr":"0x1_0000_0000_0000","op":"read"}),
        ))
        .await
        .unwrap();
    assert_eq!(bad.status(), StatusCode::BAD_REQUEST);
    assert_eq!(body_json(bad).await["kind"], "input_error");

    // 409：向不存在的 ASID 建映射。
    let conf = app
        .clone()
        .oneshot(json_post(
            "/api/v1/processes/200/mappings",
            serde_json::json!({"vaddr":"0x1000","level":3,"ppn":1,"read":true}),
        ))
        .await
        .unwrap();
    assert_eq!(conf.status(), StatusCode::CONFLICT);
    assert_eq!(body_json(conf).await["kind"], "conflict");

    // 507：帧池极小，建映射耗尽。
    let (s2, _m2) = state(2).await;
    let app2 = router(s2);
    let c2 = app2
        .clone()
        .oneshot(json_post("/api/v1/processes", serde_json::json!({})))
        .await
        .unwrap();
    let a2 = body_json(c2).await["asid"].as_u64().unwrap();
    let exh = app2
        .oneshot(json_post(
            &format!("/api/v1/processes/{a2}/mappings"),
            serde_json::json!({"vaddr":"0x3000","level":3,"ppn":9,"read":true}),
        ))
        .await
        .unwrap();
    assert_eq!(exh.status(), StatusCode::INSUFFICIENT_STORAGE);
    assert_eq!(body_json(exh).await["kind"], "resource_exhausted");
}

#[tokio::test]
async fn cross_page_access_and_run_log_carry_diagnostic_state() {
    let (s, _m) = state(4096).await;
    let app = router(s);
    let c = app
        .clone()
        .oneshot(json_post("/api/v1/processes", serde_json::json!({})))
        .await
        .unwrap();
    let asid = body_json(c).await["asid"].as_u64().unwrap();

    for va in ["0x0000", "0x1000"] {
        app.clone()
            .oneshot(json_post(
                &format!("/api/v1/processes/{asid}/mappings"),
                serde_json::json!({"vaddr":va,"level":3,"ppn":if va=="0x0000" {10} else {11},"read":true,"write":true,"execute":true,"user":true}),
            ))
            .await
            .unwrap();
    }
    let acc = app
        .clone()
        .oneshot(json_post(
            "/api/v1/access",
            serde_json::json!({"asid":asid,"vaddr":"0x0ff8","len":16,"op":"write"}),
        ))
        .await
        .unwrap();
    assert_eq!(acc.status(), StatusCode::OK);
    let ab = body_json(acc).await;
    assert_eq!(ab["success"], true);
    assert_eq!(ab["crossed_page"], true);
    assert_eq!(ab["pieces"].as_array().unwrap().len(), 2);

    // 诊断：运行日志含 run_id，且能按 id 取回。
    let runs = app
        .clone()
        .oneshot(
            Request::builder()
                .uri("/api/v1/runs?limit=5")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    let rb = body_json(runs).await;
    let first_id = rb["events"][0]["run_id"].as_u64().unwrap();
    let one = app
        .oneshot(
            Request::builder()
                .uri(format!("/api/v1/runs/{first_id}"))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(one.status(), StatusCode::OK);
    assert_eq!(body_json(one).await["run_id"], first_id);
}

#[tokio::test]
async fn snapshot_save_and_load_preserves_translation() {
    let dir = std::env::temp_dir().join(format!("mmu-teach-snap-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let path = dir.join("snap.json");

    let (s, machine) = state(4096).await;
    {
        let g = machine.read().await;
        let asid = g.create_process("persist".into()).unwrap().asid;
        g.map(asid, 0x4000, 3, 123, mmu_teach::pte::Permissions::rwx())
            .unwrap();
    }
    let app = router(s);
    let save = app
        .clone()
        .oneshot(json_post(
            "/api/v1/snapshot/save",
            serde_json::json!({"path": path.to_string_lossy()}),
        ))
        .await
        .unwrap();
    assert_eq!(save.status(), StatusCode::OK);

    // 用一个全新的机器从文件加载，再翻译同一地址，物理地址应一致。
    let (s2, machine2) = state(4096).await;
    let app2 = router(s2);
    let load = app2
        .clone()
        .oneshot(json_post(
            "/api/v1/snapshot/load",
            serde_json::json!({"path": path.to_string_lossy()}),
        ))
        .await
        .unwrap();
    assert_eq!(load.status(), StatusCode::OK);
    let pa = {
        let g = machine2.read().await;
        g.translate(0, 0x4020, mmu_teach::pte::AccessOp::Read)
            .unwrap()
            .paddr
    };
    assert_eq!(pa, 123 * 4096 + 0x20);

    std::fs::remove_dir_all(&dir).ok();
}
