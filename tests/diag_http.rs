//! HTTP-level test of the diagnostics interface: identifiers are present,
//! decisions explain outcomes, and sensitive caller data is redacted.

use io_queue_runtime::adapter::scripted::ScriptedAdapter;
use io_queue_runtime::adapter::AdapterEvent;
use io_queue_runtime::diag::{self, DiagState};
use io_queue_runtime::model::Generation;
use io_queue_runtime::runtime::RuntimeCore;
use tower::ServiceExt;

fn app_with_core() -> (
    axum::Router,
    std::sync::Arc<DiagState<ScriptedAdapter>>,
) {
    let core = RuntimeCore::new(1, 1, 1_000, ScriptedAdapter::new());
    let state = DiagState::new(core, std::path::PathBuf::from("/tmp/unused-journal.jsonl"));
    (diag::router(std::sync::Arc::clone(&state)), state)
}

async fn body_string(response: axum::response::Response) -> String {
    let bytes = axum::body::to_bytes(response.into_body(), usize::MAX)
        .await
        .expect("body");
    String::from_utf8(bytes.to_vec()).expect("utf8")
}

#[tokio::test]
async fn diagnostics_carry_identifiers_and_redact_user_data() {
    let (app, state) = app_with_core();

    // Submit through HTTP with a distinctive user_data value.
    let response = app
        .clone()
        .oneshot(
            axum::http::Request::post("/io/submit")
                .header("content-type", "application/json")
                .body(axum::body::Body::from(
                    r#"{"user_data": 424242, "op": "Nop", "timeout_ms": 500}"#,
                ))
                .expect("request"),
        )
        .await
        .expect("response");
    assert_eq!(response.status(), 200);
    let submit_body = body_string(response).await;
    assert!(submit_body.contains("\"record_id\":1"), "{submit_body}");

    // Drive the completion directly through the scripted adapter.
    {
        let mut core = state.core.lock().expect("core");
        core.adapter_mut().push_event(AdapterEvent::Completed {
            slot: 0,
            generation: Generation(0),
            result: Ok(11),
        });
        core.poll(10);
    }

    // The record view carries identifiers but never the raw user_data.
    let response = app
        .clone()
        .oneshot(
            axum::http::Request::get("/diag/records/1")
                .body(axum::body::Body::empty())
                .expect("request"),
        )
        .await
        .expect("response");
    assert_eq!(response.status(), 200);
    let record_body = body_string(response).await;
    assert!(record_body.contains("\"record_id\":1"), "{record_body}");
    assert!(
        record_body.contains("sha256:"),
        "user_data appears only as a fingerprint: {record_body}"
    );
    assert!(
        !record_body.contains("424242"),
        "raw user_data must not leak: {record_body}"
    );
    assert!(
        record_body.contains("CompletionAccepted"),
        "decision trail explains the outcome: {record_body}"
    );

    // Unknown record: the API explains why it cannot decide.
    let response = app
        .oneshot(
            axum::http::Request::get("/diag/records/999")
                .body(axum::body::Body::empty())
                .expect("request"),
        )
        .await
        .expect("response");
    assert_eq!(response.status(), 404);
    let body = body_string(response).await;
    assert!(body.contains("unknown_record"), "{body}");
    assert!(body.contains("journal"), "points at the persistent source: {body}");
}

#[tokio::test]
async fn queue_full_is_reported_as_http_backpressure() {
    let (app, _state) = app_with_core(); // capacity 1

    let submit = |app: axum::Router| async move {
        app.oneshot(
            axum::http::Request::post("/io/submit")
                .header("content-type", "application/json")
                .body(axum::body::Body::from(
                    r#"{"user_data": 1, "op": "Nop", "timeout_ms": 0}"#,
                ))
                .expect("request"),
        )
        .await
        .expect("response")
    };

    assert_eq!(submit(app.clone()).await.status(), 200);
    let response = submit(app).await;
    assert_eq!(
        response.status(),
        429,
        "overflow surfaces as explicit backpressure"
    );
    let body = body_string(response).await;
    assert!(body.contains("queue_full"), "{body}");
    assert!(body.contains("1/1"), "reason includes queue state: {body}");
}
