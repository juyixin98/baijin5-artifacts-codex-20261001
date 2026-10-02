//! 接口级验证：统一信封、HTTP 状态码与错误分类、页故障 200 返回、
//! run_id 可检索可重放、快照保存/载入往返。

#[path = "common/mod.rs"]
mod common;

use axum::http::{Method, StatusCode};
use common::harness::app;
use serde_json::json;

#[tokio::test]
async fn health_and_machine_constants() {
    let a = app(256, 8);
    let (s, v) = common::harness::call(a, Method::GET, "/health", None).await;
    assert_eq!(s, StatusCode::OK);
    assert_eq!(v["status"], "ok");
    assert_eq!(v["data"]["ok"], true);

    let a = app(256, 8);
    let (s, v) = common::harness::call(a, Method::GET, "/machine", None).await;
    assert_eq!(s, StatusCode::OK);
    assert_eq!(v["data"]["page_size"], 4096);
    assert_eq!(v["data"]["large_page_size"], 4 * 1024 * 1024);
    assert_eq!(v["data"]["levels"], 2);
    assert_eq!(v["data"]["split_example"]["l1"], 1);
}

#[tokio::test]
async fn end_to_end_translation_and_fault_envelope() {
    let a = app(4096, 8);

    // 创建两个地址空间。
    let (s, v) = common::harness::call(
        a.clone(),
        Method::POST,
        "/asids",
        Some(json!({"name": "proc-a"})),
    )
    .await;
    assert_eq!(s, StatusCode::OK, "{v}");
    let a1 = v["data"]["asid"].as_u64().unwrap() as u16;
    let (s, v) = common::harness::call(a.clone(), Method::POST, "/asids", Some(json!({}))).await;
    assert_eq!(s, StatusCode::OK);
    let a2 = v["data"]["asid"].as_u64().unwrap() as u16;
    assert_ne!(a1, a2);

    // 同 VA 各建映射。
    let body = json!({"asid": a1, "va": 0x1000, "page": "4K",
        "permissions": {"read": true, "write": true, "execute": true}});
    let (s, v) = common::harness::call(a.clone(), Method::POST, "/maps", Some(body)).await;
    assert_eq!(s, StatusCode::OK, "{v}");
    let pa1 = v["data"]["pa"].as_u64().unwrap();

    let body = json!({"asid": a2, "va": 0x1000, "page": "4K",
        "permissions": {"read": true, "write": false, "execute": true}});
    let (s, _) = common::harness::call(a.clone(), Method::POST, "/maps", Some(body)).await;
    assert_eq!(s, StatusCode::OK);

    // 翻译 a1：成功。
    let body = json!({"asid": a1, "va": 0x1234, "access": "read", "len": 1});
    let (s, v) = common::harness::call(a.clone(), Method::POST, "/translate", Some(body)).await;
    assert_eq!(s, StatusCode::OK);
    assert_eq!(v["data"]["outcome"], "translated");
    assert_eq!(v["data"]["translation"]["pa"], pa1 + 0x234);
    assert_eq!(v["data"]["translation"]["segments"][0]["source"], "walk");

    // a1 再翻译：TLB 命中。
    let body = json!({"asid": a1, "va": 0x1234, "access": "read", "len": 1});
    let (s, v) = common::harness::call(a.clone(), Method::POST, "/translate", Some(body)).await;
    assert_eq!(s, StatusCode::OK);
    assert_eq!(v["data"]["translation"]["tlb_hit"], true);
    assert_eq!(v["data"]["translation"]["segments"][0]["source"], "tlb");

    // a2 写只读映射：页故障以 200 + outcome=page_fault 返回，类型精确。
    let body = json!({"asid": a2, "va": 0x1234, "access": "write", "len": 1});
    let (s, v) = common::harness::call(a.clone(), Method::POST, "/translate", Some(body)).await;
    assert_eq!(s, StatusCode::OK, "页故障不是服务端错误");
    assert_eq!(v["data"]["outcome"], "page_fault");
    assert_eq!(v["data"]["fault"]["kind"]["type"], "permission_denied");
    assert_eq!(v["data"]["fault"]["kind"]["need"], "write");
    assert_eq!(v["data"]["fault"]["kind"]["writable"], false);
    assert!(v["data"]["fault"]["run_id"].is_string());
    assert!(v["data"]["fault"]["walk"].is_array());
}

#[tokio::test]
async fn error_categories_map_to_distinct_status_codes() {
    let a = app(4096, 8);

    // 未知 ASID：409 state_conflict / UNKNOWN_ASID。
    let body = json!({"asid": 77, "va": 0x1000, "page": "4K",
        "permissions": {"read": true, "write": true, "execute": true}});
    let (s, v) = common::harness::call(a.clone(), Method::POST, "/maps", Some(body)).await;
    assert_eq!(s, StatusCode::CONFLICT);
    assert_eq!(v["category"], "state_conflict");
    assert_eq!(v["code"], "UNKNOWN_ASID");

    // 坏 JSON：400 input_error / INVALID_JSON。
    let (s, v) = common::harness::call(
        a.clone(),
        Method::POST,
        "/asids",
        Some(json!("not-an-object")),
    )
    .await;
    assert_eq!(s, StatusCode::BAD_REQUEST);
    assert_eq!(v["code"], "INVALID_JSON");

    // 未对齐 VA：400 input_error / INVALID_REQUEST。
    let _ = common::harness::call(a.clone(), Method::POST, "/asids", Some(json!({}))).await;
    let body = json!({"asid": 1, "va": 0x1234, "page": "4K",
        "permissions": {"read": true, "write": true, "execute": true}});
    let (s, v) = common::harness::call(a.clone(), Method::POST, "/maps", Some(body)).await;
    assert_eq!(s, StatusCode::BAD_REQUEST);
    assert_eq!(v["category"], "input_error");

    // 不存在的 run_id：404 not_found。
    let (s, v) = common::harness::call(
        a.clone(),
        Method::GET,
        "/events/19700101T000000Z-999999",
        None,
    )
    .await;
    assert_eq!(s, StatusCode::NOT_FOUND);
    assert_eq!(v["category"], "not_found");
}

#[tokio::test]
async fn overlap_conflict_and_tlb_staleness_over_http() {
    let a = app(8192, 8);
    let _ = common::harness::call(a.clone(), Method::POST, "/asids", Some(json!({}))).await;

    // 大页。
    let body = json!({"asid": 1, "va": 0, "pa": 0x00400000, "page": "4M",
        "permissions": {"read": true, "write": true, "execute": true}});
    let (s, _) = common::harness::call(a.clone(), Method::POST, "/maps", Some(body)).await;
    assert_eq!(s, StatusCode::OK);

    // 内部建小页：409 LARGE_SMALL_OVERLAP。
    let body = json!({"asid": 1, "va": 0x2000, "page": "4K",
        "permissions": {"read": true, "write": true, "execute": true}});
    let (s, v) = common::harness::call(a.clone(), Method::POST, "/maps", Some(body)).await;
    assert_eq!(s, StatusCode::CONFLICT);
    assert_eq!(v["code"], "LARGE_SMALL_OVERLAP");
    assert_eq!(v["status"], "error");

    // 预热 TLB（大页内读）。
    let body = json!({"asid": 1, "va": 0x1000, "access": "read", "len": 1});
    let (s, _) = common::harness::call(a.clone(), Method::POST, "/translate", Some(body)).await;
    assert_eq!(s, StatusCode::OK);

    // 权限降级但跳过失效。
    let body = json!({"asid": 1, "va": 0, "global": false,
        "permissions": {"read": true, "write": false, "execute": true},
        "invalidate": false});
    let (s, v) = common::harness::call(a.clone(), Method::POST, "/maps/protect", Some(body)).await;
    assert_eq!(s, StatusCode::OK, "{v}");
    assert_eq!(v["data"]["tlb_invalidated"], 0);

    // 旧 TLB 条目仍允许写，且被标记 stale。
    let body = json!({"asid": 1, "va": 0x1000, "access": "write", "len": 1});
    let (s, v) = common::harness::call(a.clone(), Method::POST, "/translate", Some(body)).await;
    assert_eq!(s, StatusCode::OK);
    assert_eq!(v["data"]["outcome"], "translated", "未失效前旧条目继续命中");
    assert_eq!(v["data"]["translation"]["stale_segments"], 1);

    // 显式失效。
    let body = json!({"asid": 1, "va": 0, "page": "4M"});
    let (s, v) =
        common::harness::call(a.clone(), Method::POST, "/tlb/invalidate", Some(body)).await;
    assert_eq!(s, StatusCode::OK);
    assert!(v["data"]["removed"].as_u64().unwrap() >= 1);

    // 失效后写被拒。
    let body = json!({"asid": 1, "va": 0x1000, "access": "write", "len": 1});
    let (s, v) = common::harness::call(a.clone(), Method::POST, "/translate", Some(body)).await;
    assert_eq!(s, StatusCode::OK);
    assert_eq!(v["data"]["outcome"], "page_fault");
    assert_eq!(v["data"]["fault"]["kind"]["type"], "permission_denied");
}

#[tokio::test]
async fn run_id_is_recorded_and_replayable() {
    let a = app(4096, 256);
    let _ = common::harness::call(a.clone(), Method::POST, "/asids", Some(json!({}))).await;
    let body = json!({"asid": 1, "va": 0x1000, "page": "4K",
        "permissions": {"read": true, "write": true, "execute": true}});
    let (_, v) = common::harness::call(a.clone(), Method::POST, "/maps", Some(body)).await;
    let map_run = v["data"]["run_id"].as_str().unwrap().to_string();

    // 触发一次缺页。
    let body = json!({"asid": 1, "va": 0x9000, "access": "read", "len": 1});
    let (_, v) = common::harness::call(a.clone(), Method::POST, "/translate", Some(body)).await;
    let fault_run = v["data"]["fault"]["run_id"].as_str().unwrap().to_string();

    // 用 run_id 取回完整事件（含理由与中间状态）。
    let (s, v) = common::harness::call(
        a.clone(),
        Method::GET,
        &format!("/events/{fault_run}"),
        None,
    )
    .await;
    assert_eq!(s, StatusCode::OK);
    assert_eq!(v["data"]["run_id"], fault_run);
    assert_eq!(v["data"]["kind"], "translate_fault");
    assert_eq!(v["data"]["failure"]["code"], "PAGE_FAULT_NOT_PRESENT");
    assert!(v["data"]["state"]["walk"].is_array());
    assert!(v["data"]["rationale"].is_array());

    let (s, v) =
        common::harness::call(a.clone(), Method::GET, &format!("/events/{map_run}"), None).await;
    assert_eq!(s, StatusCode::OK);
    assert_eq!(v["data"]["kind"], "map");
}

#[tokio::test]
async fn snapshot_save_and_load_roundtrip() {
    let path = format!("/tmp/mmu-lab-it-snap-{}.json", std::process::id());
    let a = app(4096, 8);
    let _ = common::harness::call(a.clone(), Method::POST, "/asids", Some(json!({}))).await;
    let body = json!({"asid": 1, "va": 0x1000, "page": "4K",
        "permissions": {"read": true, "write": true, "execute": true}});
    let (_, v) = common::harness::call(a.clone(), Method::POST, "/maps", Some(body)).await;
    let pa = v["data"]["pa"].as_u64().unwrap();

    let (s, v) = common::harness::call(
        a.clone(),
        Method::POST,
        "/snapshot/save",
        Some(json!({"path": path})),
    )
    .await;
    assert_eq!(s, StatusCode::OK, "{v}");

    // 重置后映射消失：翻译缺页。
    let (s, _) = common::harness::call(a.clone(), Method::POST, "/reset", Some(json!({}))).await;
    assert_eq!(s, StatusCode::OK);
    let body = json!({"asid": 1, "va": 0x1234, "access": "read", "len": 1});
    let (s, v) = common::harness::call(a.clone(), Method::POST, "/translate", Some(body)).await;
    assert_eq!(s, StatusCode::CONFLICT, "重置后 ASID 也被清空");
    assert_eq!(v["code"], "UNKNOWN_ASID");

    // 载入快照：映射恢复，PA 保持。
    let (s, v) = common::harness::call(
        a.clone(),
        Method::POST,
        "/snapshot/load",
        Some(json!({"path": path})),
    )
    .await;
    assert_eq!(s, StatusCode::OK, "{v}");
    let body = json!({"asid": 1, "va": 0x1234, "access": "read", "len": 1, "fetch_pte": true});
    let (s, v) = common::harness::call(a.clone(), Method::POST, "/translate", Some(body)).await;
    assert_eq!(s, StatusCode::OK, "{v}");
    assert_eq!(v["data"]["outcome"], "translated");
    assert_eq!(v["data"]["translation"]["pa"], pa + 0x234);

    let _ = std::fs::remove_file(&path);
}

#[tokio::test]
async fn resource_exhaustion_returns_507() {
    // 容量极小：根表 1 + L2 表 1 + 数据 1 = 3 帧后耗尽。
    let a = app(3, 4);
    let _ = common::harness::call(a.clone(), Method::POST, "/asids", Some(json!({}))).await;
    let body = json!({"asid": 1, "va": 0x1000, "page": "4K",
        "permissions": {"read": true, "write": true, "execute": true}});
    let (s, _) = common::harness::call(a.clone(), Method::POST, "/maps", Some(body)).await;
    assert_eq!(s, StatusCode::OK);

    let body = json!({"asid": 1, "va": 0x00800000, "page": "4K",
        "permissions": {"read": true, "write": true, "execute": true}});
    let (s, v) = common::harness::call(a.clone(), Method::POST, "/maps", Some(body)).await;
    assert_eq!(s, StatusCode::INSUFFICIENT_STORAGE);
    assert_eq!(v["category"], "resource_exhausted");
    assert_eq!(v["code"], "OUT_OF_FRAMES");
}

#[tokio::test]
async fn remaining_endpoints_and_error_paths() {
    let path = format!("/tmp/mmu-lab-it-extra-{}.json", std::process::id());
    let a = app(256, 4);

    // 根信息。
    let (s, v) = common::harness::call(a.clone(), Method::GET, "/", None).await;
    assert_eq!(s, StatusCode::OK);
    assert_eq!(v["data"]["service"], "mmu-lab");

    // 空态列举。
    let (s, v) = common::harness::call(a.clone(), Method::GET, "/asids", None).await;
    assert_eq!(s, StatusCode::OK);
    assert_eq!(v["data"]["count"], 0);

    let _ =
        common::harness::call(a.clone(), Method::POST, "/asids", Some(json!({"name":"p"}))).await;
    let (s, v) = common::harness::call(a.clone(), Method::GET, "/asids/1", None).await;
    assert_eq!(s, StatusCode::OK);
    assert_eq!(v["data"]["name"], "p");

    // 建两条映射后列举（含 ?asid 过滤）。
    for va in [0x1000u64, 0x2000] {
        let body = json!({"asid":1,"va":va,"page":"4K","permissions":{"read":true,"write":true,"execute":true}});
        let (s, _) = common::harness::call(a.clone(), Method::POST, "/maps", Some(body)).await;
        assert_eq!(s, StatusCode::OK);
    }
    let (s, v) = common::harness::call(a.clone(), Method::GET, "/maps?asid=1", None).await;
    assert_eq!(s, StatusCode::OK);
    assert_eq!(v["data"]["count"], 2);

    // frames / tlb 状态端点。
    let (s, v) = common::harness::call(a.clone(), Method::GET, "/frames", None).await;
    assert_eq!(s, StatusCode::OK);
    assert_eq!(v["data"]["total"], 256);
    let (s, v) = common::harness::call(a.clone(), Method::GET, "/tlb", None).await;
    assert_eq!(s, StatusCode::OK);
    assert_eq!(v["data"]["capacity"], 4);

    // 非法失效参数组合：400。
    let body = json!({"asid":1,"va":4096});
    let (s, v) =
        common::harness::call(a.clone(), Method::POST, "/tlb/invalidate", Some(body)).await;
    assert_eq!(s, StatusCode::BAD_REQUEST, "{v}");

    // 不存在映射的 protect：404。
    let body =
        json!({"asid":1,"va":0x9000,"permissions":{"read":true,"write":false,"execute":true}});
    let (s, v) = common::harness::call(a.clone(), Method::POST, "/maps/protect", Some(body)).await;
    assert_eq!(s, StatusCode::NOT_FOUND);
    assert_eq!(v["category"], "not_found");

    // 全 0 权限 protect：400。
    let body =
        json!({"asid":1,"va":4096,"permissions":{"read":false,"write":false,"execute":false}});
    let (s, _) = common::harness::call(a.clone(), Method::POST, "/maps/protect", Some(body)).await;
    assert_eq!(s, StatusCode::BAD_REQUEST);

    // 翻译长度越界：400；未知 ASID 翻译：409。
    let body = json!({"asid":1,"va":0,"access":"read","len":4194305});
    let (s, _) = common::harness::call(a.clone(), Method::POST, "/translate", Some(body)).await;
    assert_eq!(s, StatusCode::BAD_REQUEST);
    let body = json!({"asid":50,"va":0,"access":"read","len":1});
    let (s, _) = common::harness::call(a.clone(), Method::POST, "/translate", Some(body)).await;
    assert_eq!(s, StatusCode::CONFLICT);

    // ASID 级失效与全局冲刷。
    let (s, v) = common::harness::call(
        a.clone(),
        Method::POST,
        "/tlb/invalidate",
        Some(json!({"asid":1})),
    )
    .await;
    assert_eq!(s, StatusCode::OK);
    assert_eq!(v["data"]["removed"], 0);
    let (s, _) =
        common::harness::call(a.clone(), Method::POST, "/tlb/invalidate", Some(json!({}))).await;
    assert_eq!(s, StatusCode::OK);

    // unmap 后映射列举减少。
    let body = json!({"asid":1,"va":4096});
    let (s, _) = common::harness::call(a.clone(), Method::POST, "/maps/unmap", Some(body)).await;
    assert_eq!(s, StatusCode::OK);
    let (_, v) = common::harness::call(a.clone(), Method::GET, "/maps?asid=1", None).await;
    assert_eq!(v["data"]["count"], 1);

    // 销毁 ASID 后再查：409。
    let (s, _) = common::harness::call(a.clone(), Method::DELETE, "/asids/1", None).await;
    assert_eq!(s, StatusCode::OK);
    let (s, v) = common::harness::call(a.clone(), Method::GET, "/asids/1", None).await;
    assert_eq!(s, StatusCode::CONFLICT);
    assert_eq!(v["code"], "UNKNOWN_ASID");

    // 载入损坏快照：400 输入错误。
    std::fs::write(&path, b"garbage").unwrap();
    let (s, v) = common::harness::call(
        a.clone(),
        Method::POST,
        "/snapshot/load",
        Some(json!({"path": path})),
    )
    .await;
    assert_eq!(s, StatusCode::BAD_REQUEST);
    assert_eq!(v["category"], "input_error");
    let _ = std::fs::remove_file(&path);
}

#[tokio::test]
async fn asid_exhaustion_returns_507() {
    // max_asids 在测试构造器里固定为 8。
    let a = app(8192, 4);
    for _ in 0..8 {
        let (s, _) =
            common::harness::call(a.clone(), Method::POST, "/asids", Some(json!({}))).await;
        assert_eq!(s, StatusCode::OK);
    }
    let (s, v) = common::harness::call(a.clone(), Method::POST, "/asids", Some(json!({}))).await;
    assert_eq!(s, StatusCode::INSUFFICIENT_STORAGE);
    assert_eq!(v["code"], "OUT_OF_ASIDS");
}

#[tokio::test]
async fn reset_with_invalid_config_is_rejected() {
    let a = app(256, 4);
    let body =
        json!({"config":{"total_frames":0,"tlb_capacity":4,"max_asids":8,"event_buffer":64}});
    let (s, _) = common::harness::call(a.clone(), Method::POST, "/reset", Some(body)).await;
    assert_eq!(s, StatusCode::BAD_REQUEST);
}
