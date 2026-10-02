//! 配置解析、采样状态持久化、诊断脱敏、文件后端与轨迹 JSONL 的测试。

use std::path::PathBuf;

use arc_page_cache::config::{AppConfig, ArcConfig, ConfigError};
use arc_page_cache::diagnostics::redact_payload;
use arc_page_cache::state::SampledState;
use arc_page_cache::storage::FilePageStore;
use arc_page_cache::trace;
use arc_page_cache::types::CacheStats;
use tempfile::tempdir;

// ---- 配置 -----------------------------------------------------------------

#[test]
fn parses_full_config() {
    let toml = r#"
[cache]
capacity = 16
page_size = 8192
writeback_retries = 3

[server]
bind = "0.0.0.0:9090"
data_dir = "/tmp/arc-data"
"#;
    let cfg = AppConfig::from_toml_str(toml).unwrap();
    assert_eq!(cfg.cache.capacity, 16);
    assert_eq!(cfg.cache.page_size, 8192);
    assert_eq!(cfg.cache.writeback_retries, 3);
    assert_eq!(cfg.server.bind, "0.0.0.0:9090");
}

#[test]
fn zero_capacity_is_a_valid_config() {
    let toml = r#"
[cache]
capacity = 0
page_size = 4096
writeback_retries = 0

[server]
bind = "127.0.0.1:0"
data_dir = "/tmp/x"
"#;
    let cfg = AppConfig::from_toml_str(toml).unwrap();
    assert_eq!(cfg.cache.capacity, 0);
    assert!(cfg.cache.validate().is_ok());
}

#[test]
fn rejects_zero_page_size() {
    let cfg = ArcConfig {
        capacity: 4,
        page_size: 0,
        writeback_retries: 0,
    };
    match cfg.validate() {
        Err(ConfigError::InvalidField(msg)) => assert!(msg.contains("page_size")),
        other => panic!("expected invalid field error, got {other:?}"),
    }
}

#[test]
fn unknown_or_malformed_toml_errors() {
    let bad = "[cache\ncapacity = 1";
    assert!(AppConfig::from_toml_str(bad).is_err());

    let missing = "[cache]\ncapacity = 1\npage_size = 4096\n";
    assert!(
        AppConfig::from_toml_str(missing).is_err(),
        "missing [server]"
    );
}

#[test]
fn reads_config_from_file() {
    let dir = tempdir().unwrap();
    let path = dir.path().join("arc.toml");
    std::fs::write(
        &path,
        "[cache]\ncapacity=2\npage_size=512\nwriteback_retries=1\n\n[server]\nbind=\"127.0.0.1:0\"\ndata_dir=\"./x\"\n",
    )
    .unwrap();
    let cfg = AppConfig::from_toml_path(&path).unwrap();
    assert_eq!(cfg.cache.capacity, 2);
}

// ---- 采样状态持久化 -------------------------------------------------------

fn sample_stats() -> CacheStats {
    CacheStats {
        c: 8,
        p: 3,
        t1_len: 2,
        t2_len: 5,
        b1_len: 1,
        b2_len: 4,
        resident: 7,
        hits_t1: 10,
        hits_t2: 20,
        ghost_hits_b1: 3,
        ghost_hits_b2: 2,
        misses: 15,
        hits: 30,
        rejected_zero_capacity: 0,
        writeback_failures: 1,
        writebacks: 9,
        fetches: 20,
    }
}

#[test]
fn sampled_state_roundtrip_and_cold_start() {
    let dir = tempdir().unwrap();
    let path = dir.path().join("state.json");

    assert!(SampledState::load(&path).unwrap().is_none(), "cold start");

    let s = SampledState::new(8, 3, sample_stats());
    s.save(&path).unwrap();
    let loaded = SampledState::load(&path).unwrap().expect("saved state");
    assert_eq!(loaded, s);
    assert!(loaded.supported());
    assert_eq!(loaded.p, 3);
    assert_eq!(loaded.stats.ghost_hits_b1, 3);
}

#[test]
fn state_save_is_atomic_and_metadata_only() {
    let dir = tempdir().unwrap();
    let path = dir.path().join("state.json");
    SampledState::new(4, 1, sample_stats()).save(&path).unwrap();
    let text = std::fs::read_to_string(&path).unwrap();
    // 不含页内容相关字段，且为 JSON 元数据。
    assert!(text.contains("\"p\""));
    assert!(!text.contains("page_data"));
    // 保存后无遗留临时文件。
    let leftovers: Vec<PathBuf> = std::fs::read_dir(dir.path())
        .unwrap()
        .map(|e| e.unwrap().path())
        .filter(|p| p.file_name().unwrap().to_string_lossy().contains(".tmp"))
        .collect();
    assert!(leftovers.is_empty(), "no tmp files left: {leftovers:?}");
}

// ---- 诊断脱敏 -------------------------------------------------------------

#[test]
fn redaction_hides_payload_content() {
    let secret = b"secret-bytes-that-must-not-appear";
    let r = redact_payload(secret);
    assert!(!r.contains("secret"));
    assert!(r.contains("bytes"));
    assert!(r.contains(&secret.len().to_string()));
}

// ---- 文件后端 -------------------------------------------------------------

#[test]
fn file_store_synthesizes_fetches_and_persists_writeback() {
    let dir = tempdir().unwrap();
    let mut store = FilePageStore::new(dir.path(), 32).unwrap();
    use arc_page_cache::storage::PageStore;

    // 首次 fetch：确定性合成并落盘。
    let a = store.fetch(arc_page_cache::types::PageId(7)).unwrap();
    assert_eq!(a.len(), 32);
    // 同页再 fetch 得到相同内容（已落盘）。
    let b = store.fetch(arc_page_cache::types::PageId(7)).unwrap();
    assert_eq!(a, b);
    // 不同页内容不同。
    let c = store.fetch(arc_page_cache::types::PageId(8)).unwrap();
    assert_ne!(a, c);
    assert!(dir.path().join("pages").join("7.page").exists());

    // 回写后重新读取得到新内容。
    let mut changed = a.clone();
    changed[0] = changed[0].wrapping_add(1);
    store
        .write_back(arc_page_cache::types::PageId(7), &changed)
        .unwrap();
    let after = store.fetch(arc_page_cache::types::PageId(7)).unwrap();
    assert_eq!(after, changed);
    // 回写用临时文件+改名，无 .tmp 残留。
    let tmps: Vec<_> = std::fs::read_dir(dir.path().join("pages"))
        .unwrap()
        .map(|e| e.unwrap().file_name().to_string_lossy().to_string())
        .filter(|n| n.ends_with(".tmp"))
        .collect();
    assert!(tmps.is_empty());
}

// ---- 轨迹 JSONL -----------------------------------------------------------

#[test]
fn trace_jsonl_roundtrip() {
    let dir = tempdir().unwrap();
    let path = dir.path().join("mixed.jsonl");
    let original = trace::mixed_trace();
    trace::write_jsonl(&path, &original).unwrap();
    let loaded = trace::read_jsonl(&path).unwrap();
    assert_eq!(loaded, original);

    // 生成器长度符合预期。
    assert_eq!(trace::scan_trace(0, 32).len(), 32);
    let hs = trace::hotspot_switch_trace(&[1, 2], &[3, 4], 2, 2, true);
    assert_eq!(hs.len(), 2 * 2 + 2 * 2 + 2);
}

#[test]
fn trace_read_reports_line_number_on_bad_json() {
    let dir = tempdir().unwrap();
    let path = dir.path().join("bad.jsonl");
    std::fs::write(&path, "{\"page\":1,\"kind\":\"read\"}\nnot-json\n").unwrap();
    let err = trace::read_jsonl(&path).unwrap_err();
    assert!(err.to_string().contains(":2:"), "error mentions line 2");
}
