//! Black-box HTTP tests over a real loopback TCP connection: concrete status
//! codes, categories and results — no mocking, no "endpoint callable" checks.

use std::io::{Read, Write};
use std::net::TcpStream;
use std::process::{Child, Command, Stdio};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

fn unique_port() -> u16 {
    40000 + (SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos()
        % 20000) as u16
}

struct ServerGuard {
    child: Child,
    port: u16,
}

impl Drop for ServerGuard {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

fn start_server() -> ServerGuard {
    let port = unique_port();
    let bin = env!("CARGO_BIN_EXE_stlc-server");
    let child = Command::new(bin)
        .env("STLC_BIND", format!("127.0.0.1:{port}"))
        .env("STLC_LOG_DIR", "logs/test-runs")
        .arg("serve")
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .expect("server starts");
    let guard = ServerGuard { child, port };
    for _ in 0..100 {
        if TcpStream::connect(format!("127.0.0.1:{port}")).is_ok() {
            std::thread::sleep(Duration::from_millis(50));
            return guard;
        }
        std::thread::sleep(Duration::from_millis(20));
    }
    panic!("server did not become ready");
}

fn request(port: u16, method: &str, path: &str, body: &str) -> (u16, serde_json::Value) {
    let mut stream = TcpStream::connect(format!("127.0.0.1:{port}")).unwrap();
    let req = if method == "POST" {
        format!(
            "{method} {path} HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
            body.len()
        )
    } else {
        format!("{method} {path} HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
    };
    stream.write_all(req.as_bytes()).unwrap();
    let mut raw = String::new();
    stream.read_to_string(&mut raw).unwrap();
    let status: u16 = raw
        .lines()
        .next()
        .and_then(|line| line.split_whitespace().nth(1))
        .and_then(|s| s.parse().ok())
        .unwrap_or(0);
    let body_start = raw.find("\r\n\r\n").map(|p| p + 4).unwrap_or(raw.len());
    let json: serde_json::Value = serde_json::from_str(&raw[body_start..]).unwrap_or_else(|e| {
        panic!("non-JSON body ({e}): {}", &raw[body_start..])
    });
    (status, json)
}

#[test]
fn health_reports_versions() {
    let g = start_server();
    let (status, json) = request(g.port, "GET", "/v1/health", "");
    assert_eq!(status, 200);
    assert_eq!(json["status"], "healthy");
    assert!(json["service_version"].is_string());
    assert!(json["toolchain"].is_string());
}

#[test]
fn check_success_normalizes_and_preserves_type() {
    let g = start_server();
    let body = r#"{"term_source":"(lam x: Bool. x) true","budget":16}"#;
    let (status, json) = request(g.port, "POST", "/v1/check", body);
    assert_eq!(status, 200);
    assert_eq!(json["ok"], true);
    assert_eq!(json["response"]["steps_used"], 1);
    assert_eq!(json["response"]["normal_form"]["kind"], "bool_lit");
    assert_eq!(json["response"]["normal_form"]["value"], true);
    assert_eq!(json["response"]["before_type"]["kind"], "bool");
    assert_eq!(json["response"]["after_type"]["kind"], "bool");
    assert!(json["run_id"].as_str().unwrap().starts_with("run-"));
    for inv in json["response"]["invariants"].as_array().unwrap() {
        assert_eq!(inv["passed"], true, "invariant failed: {inv}");
    }
}

#[test]
fn check_rejects_type_error_with_422_and_category() {
    let g = start_server();
    let (status, json) = request(g.port, "POST", "/v1/check", r#"{"term_source":"true false"}"#);
    assert_eq!(status, 422);
    assert_eq!(json["ok"], false);
    assert_eq!(json["category"], "type_error");
    assert_eq!(json["error"]["category"], "expected_function");
}

#[test]
fn check_rejects_budget_exhaustion_separately() {
    let g = start_server();
    let body = r#"{"term_source":"(lam x: Bool. x) ((lam x: Bool. x) true)","budget":1}"#;
    let (status, json) = request(g.port, "POST", "/v1/check", body);
    assert_eq!(status, 422);
    assert_eq!(json["category"], "budget_exhausted");
    assert_eq!(json["error"]["steps_used"], 1);
}

#[test]
fn check_rejects_malformed_json_and_parse_errors_with_400() {
    let g = start_server();
    let (status, json) = request(g.port, "POST", "/v1/check", "{bad");
    assert_eq!(status, 400);
    assert_eq!(json["category"], "invalid_json");

    let (status, json) = request(
        g.port,
        "POST",
        "/v1/check",
        r#"{"term_source":"lam x: . x"}"#,
    );
    assert_eq!(status, 400);
    assert_eq!(json["category"], "parse_error");
}

#[test]
fn alpha_equiv_endpoint_distinguishes_terms() {
    let g = start_server();
    let (s1, j1) = request(
        g.port,
        "POST",
        "/v1/alpha-equiv",
        r#"{"term_source":"lam x: Bool. x","right_source":"lam y: Bool. y"}"#,
    );
    assert_eq!(s1, 200);
    assert_eq!(j1["alpha_equivalent"], true);

    let (s2, j2) = request(
        g.port,
        "POST",
        "/v1/alpha-equiv",
        r#"{"term_source":"true","right_source":"false"}"#,
    );
    assert_eq!(s2, 200);
    assert_eq!(j2["alpha_equivalent"], false);
}

#[test]
fn routing_status_codes_are_explicit() {
    let g = start_server();
    let (s404, j404) = request(g.port, "GET", "/nope", "");
    assert_eq!(s404, 404);
    assert_eq!(j404["category"], "not_found");
    let (s405, j405) = request(g.port, "GET", "/v1/check", "");
    assert_eq!(s405, 405);
    assert_eq!(j405["category"], "method_not_allowed");
}

#[test]
fn free_variable_signature_is_accepted_and_verified() {
    let g = start_server();
    let body = r#"{"term_source":"(lam f: Bool. f) g","free_signature_source":["g : Bool"],"budget":16}"#;
    let (status, json) = request(g.port, "POST", "/v1/check", body);
    assert_eq!(status, 200, "{json}");
    assert_eq!(
        json["response"]["free_vars_after"],
        serde_json::json!(["g"])
    );
    assert_eq!(json["response"]["normal_form"]["name"], "g");
}
