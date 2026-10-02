//! Minimal HTTP/1.1 server using only `std`. JSON in, JSON out.
//!
//! Routes:
//!   POST /v1/check           type check + normalize + invariant report
//!   POST /v1/alpha-equiv     alpha equivalence of two terms
//!   GET  /v1/health          liveness + versions
//!   GET  /v1/info            request schema summary
//!
//! Status mapping is explicit and never collapses errors into success:
//!   200 success, 400 malformed JSON/syntax, 422 typing or budget failure,
//!   404 unknown route, 405 wrong method.

use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::thread;

use stlc_core::error::DriverError;
use stlc_core::{alpha_equivalent, run};
use stlc_syntax::db::to_locally_nameless;

use crate::config::Config;
use crate::protocol::{AlphaRequest, ApiRequest};
use crate::runlog::{
    digest_hex, new_run_id, now_millis, RunLogger, RunRecord, RUSTC_VERSION, SERVICE_VERSION,
};

fn send_json(stream: &mut TcpStream, status: u16, reason: &str, body: &str) -> std::io::Result<()> {
    let bytes = body.as_bytes();
    let head = format!(
        "HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
        bytes.len()
    );
    stream.write_all(head.as_bytes())?;
    stream.write_all(bytes)?;
    stream.flush()
}

fn read_request(stream: &mut TcpStream) -> std::io::Result<(String, String, Vec<u8>)> {
    let mut buf = Vec::new();
    let mut chunk = [0u8; 4096];
    loop {
        let n = stream.read(&mut chunk)?;
        if n == 0 {
            break;
        }
        buf.extend_from_slice(&chunk[..n]);
        if let Some(header_end) = find_header_end(&buf) {
            let header = String::from_utf8_lossy(&buf[..header_end]).to_string();
            let content_length = header
                .lines()
                .find_map(|line| {
                    let line = line.trim();
                    if line.to_ascii_lowercase().starts_with("content-length:") {
                        line.split(':').nth(1)?.trim().parse::<usize>().ok()
                    } else {
                        None
                    }
                })
                .unwrap_or(0);
            let body_start = header_end + 4;
            if buf.len() >= body_start + content_length {
                buf.truncate(body_start + content_length);
                let first_line = header.lines().next().unwrap_or("").to_string();
                let path = first_line.split_whitespace().nth(1).unwrap_or("/").to_string();
                let body = buf[body_start..].to_vec();
                return Ok((first_line, path, body));
            }
        }
        if buf.len() > 8 * 1024 * 1024 {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                "request too large",
            ));
        }
    }
    Err(std::io::Error::new(
        std::io::ErrorKind::UnexpectedEof,
        "incomplete request",
    ))
}

fn find_header_end(buf: &[u8]) -> Option<usize> {
    buf.windows(4).position(|w| w == b"\r\n\r\n")
}

fn error_body(category: &str, message: &str) -> String {
    serde_json::json!({
        "ok": false,
        "category": category,
        "message": message,
    })
    .to_string()
}

fn classify_driver(err: &DriverError) -> (u16, &'static str, &'static str, String) {
    match err {
        DriverError::Parse { .. } => (400, "Bad Request", "parse_error", err.to_string()),
        DriverError::Check(ce) => match ce {
            stlc_core::error::NormError::Type(_) => {
                (422, "Unprocessable Entity", "type_error", ce.to_string())
            }
            stlc_core::error::NormError::BudgetExhausted { .. } => (
                422,
                "Unprocessable Entity",
                "budget_exhausted",
                ce.to_string(),
            ),
            stlc_core::error::NormError::Internal { .. } => {
                (500, "Internal Server Error", "internal", ce.to_string())
            }
        },
    }
}

fn handle_check(body: &[u8], cfg: &Config, logger: &RunLogger) -> (u16, &'static str, String) {
    let run_id = new_run_id();
    let input_digest = digest_hex(body);

    let parsed: Result<ApiRequest, _> = serde_json::from_slice(body);
    let request = match parsed {
        Ok(api) => match api.into_core(cfg.budget) {
            Ok(req) => req,
            Err(message) => {
                let out = error_body("parse_error", &message);
                logger.record(&RunRecord {
                    run_id: &run_id,
                    ts_unix_ms: now_millis(),
                    service_version: SERVICE_VERSION,
                    toolchain: RUSTC_VERSION,
                    endpoint: "/v1/check",
                    input_digest: &input_digest,
                    verdict: "rejected",
                    category: "parse_error",
                    steps_used: None,
                    budget: None,
                    detail: &message,
                });
                return (400, "Bad Request", out);
            }
        },
        Err(e) => {
            let message = format!("malformed JSON request: {e}");
            let out = error_body("invalid_json", &message);
            logger.record(&RunRecord {
                run_id: &run_id,
                ts_unix_ms: now_millis(),
                service_version: SERVICE_VERSION,
                toolchain: RUSTC_VERSION,
                endpoint: "/v1/check",
                input_digest: &input_digest,
                verdict: "rejected",
                category: "invalid_json",
                steps_used: None,
                budget: None,
                detail: &message,
            });
            return (400, "Bad Request", out);
        }
    };

    let budget = request.budget;
    match run(request) {
        Ok(resp) => {
            let envelope = serde_json::json!({
                "ok": true,
                "run_id": run_id,
                "input_digest": input_digest,
                "service_version": SERVICE_VERSION,
                "response": resp,
            });
            let out = envelope.to_string();
            logger.record(&RunRecord {
                run_id: &run_id,
                ts_unix_ms: now_millis(),
                service_version: SERVICE_VERSION,
                toolchain: RUSTC_VERSION,
                endpoint: "/v1/check",
                input_digest: &input_digest,
                verdict: "ok",
                category: "ok",
                steps_used: Some(resp.steps_used),
                budget: Some(budget),
                detail: "type-preserving normal form produced",
            });
            (200, "OK", out)
        }
        Err(err) => {
            let (status, reason, category, message) = classify_driver(&err);
            let out = serde_json::json!({
                "ok": false,
                "run_id": run_id,
                "input_digest": input_digest,
                "category": category,
                "message": message,
                "error": serde_json::to_value(&err).unwrap_or(serde_json::Value::Null),
            })
            .to_string();
            logger.record(&RunRecord {
                run_id: &run_id,
                ts_unix_ms: now_millis(),
                service_version: SERVICE_VERSION,
                toolchain: RUSTC_VERSION,
                endpoint: "/v1/check",
                input_digest: &input_digest,
                verdict: "rejected",
                category,
                steps_used: None,
                budget: Some(budget),
                detail: &message,
            });
            (status, reason, out)
        }
    }
}

fn handle_alpha(body: &[u8], logger: &RunLogger) -> (u16, &'static str, String) {
    let run_id = new_run_id();
    let input_digest = digest_hex(body);
    let req: AlphaRequest = match serde_json::from_slice(body) {
        Ok(r) => r,
        Err(e) => {
            let message = format!("malformed JSON request: {e}");
            return (400, "Bad Request", error_body("invalid_json", &message));
        }
    };
    match req.terms() {
        Ok((left, right)) => {
            let ldb = to_locally_nameless(&left);
            let rdb = to_locally_nameless(&right);
            let equivalent = alpha_equivalent(&ldb, &rdb);
            let out = serde_json::json!({
                "ok": true,
                "run_id": run_id,
                "input_digest": input_digest,
                "alpha_equivalent": equivalent,
                "well_formed": ldb.is_locally_closed() && rdb.is_locally_closed(),
            })
            .to_string();
            logger.record(&RunRecord {
                run_id: &run_id,
                ts_unix_ms: now_millis(),
                service_version: SERVICE_VERSION,
                toolchain: RUSTC_VERSION,
                endpoint: "/v1/alpha-equiv",
                input_digest: &input_digest,
                verdict: if equivalent { "ok" } else { "ok_not_equivalent" },
                category: "ok",
                steps_used: None,
                budget: None,
                detail: if equivalent {
                    "terms are alpha-equivalent"
                } else {
                    "terms are not alpha-equivalent (or ill-scoped)"
                },
            });
            (200, "OK", out)
        }
        Err(message) => {
            logger.record(&RunRecord {
                run_id: &run_id,
                ts_unix_ms: now_millis(),
                service_version: SERVICE_VERSION,
                toolchain: RUSTC_VERSION,
                endpoint: "/v1/alpha-equiv",
                input_digest: &input_digest,
                verdict: "rejected",
                category: "parse_error",
                steps_used: None,
                budget: None,
                detail: &message,
            });
            (400, "Bad Request", error_body("parse_error", &message))
        }
    }
}

fn handle_connection(mut stream: TcpStream, cfg: Config, logger: RunLogger) {
    let (request_line, path, body) = match read_request(&mut stream) {
        Ok(parts) => parts,
        Err(e) => {
            let _ = send_json(
                &mut stream,
                400,
                "Bad Request",
                &error_body("bad_request", &e.to_string()),
            );
            return;
        }
    };
    let method = request_line.split_whitespace().next().unwrap_or("");
    let (status, reason, payload) = match (method, path.as_str()) {
        ("POST", "/v1/check") => handle_check(&body, &cfg, &logger),
        ("POST", "/v1/alpha-equiv") => handle_alpha(&body, &logger),
        ("GET", "/v1/health") => (
            200,
            "OK",
            serde_json::json!({
                "ok": true,
                "status": "healthy",
                "service_version": SERVICE_VERSION,
                "toolchain": RUSTC_VERSION,
            })
            .to_string(),
        ),
        ("GET", "/v1/info") => (
            200,
            "OK",
            serde_json::json!({
                "service": "stlc-proof-checker",
                "version": SERVICE_VERSION,
                "routes": [
                    "POST /v1/check",
                    "POST /v1/alpha-equiv",
                    "GET /v1/health",
                    "GET /v1/info"
                ],
                "request_fields": ["term | term_source", "free_signature[]", "free_signature_source[]", "budget"],
            })
            .to_string(),
        ),
        (_, "/v1/check") | (_, "/v1/alpha-equiv") => (
            405,
            "Method Not Allowed",
            error_body("method_not_allowed", "use POST"),
        ),
        _ => (
            404,
            "Not Found",
            error_body("not_found", "unknown route"),
        ),
    };
    let _ = send_json(&mut stream, status, reason, &payload);
}

pub fn serve(cfg: Config) -> std::io::Result<()> {
    let listener = TcpListener::bind(&cfg.bind)?;
    let logger = RunLogger::new(&cfg.log_dir);
    eprintln!(
        "[stlc-service] listening on http://{} (service v{}, {})",
        cfg.bind, SERVICE_VERSION, RUSTC_VERSION
    );
    for stream in listener.incoming() {
        match stream {
            Ok(stream) => {
                let cfg = cfg.clone();
                let logger = RunLogger::new(&cfg.log_dir);
                thread::spawn(move || handle_connection(stream, cfg, logger));
            }
            Err(e) => eprintln!("[stlc-service] accept failed: {e}"),
        }
    }
    let _ = logger;
    Ok(())
}
