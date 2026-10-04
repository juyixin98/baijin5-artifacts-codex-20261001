//! Client-side driver: performs the party role against a running server.
//! Raw elements never leave this process — only blinded points are sent.

use std::time::Duration;

use serde::Deserialize;
use thiserror::Error;

use crate::audit::AuditRecord;
use crate::crypto;
use crate::error::PsiError;
use crate::protocol::{PartyA, PartyB};

#[derive(Debug, Error)]
pub enum ClientError {
    #[error("transport error: {0}")]
    Transport(#[from] reqwest::Error),

    #[error("server rejected request: status={status} code={code} request_id={request_id}: {message}")]
    Server {
        status: u16,
        code: String,
        message: String,
        request_id: String,
    },

    #[error("protocol error: {0}")]
    Protocol(#[from] PsiError),

    #[error("timed out waiting for the other party (session state: {0})")]
    Timeout(String),
}

#[derive(Deserialize)]
struct ServerErrorBody {
    error: ServerErrorDetail,
}

#[derive(Deserialize)]
struct ServerErrorDetail {
    code: String,
    message: String,
    request_id: String,
}

#[derive(Deserialize)]
struct CreateSessionResponse {
    session_id: String,
}

#[derive(Deserialize)]
struct MessagesAResponse {
    state: String,
    points: Option<Vec<String>>,
}

#[derive(Deserialize)]
struct MessagesBResponse {
    state: String,
    b_blinded: Option<Vec<String>>,
    a_doubly: Option<Vec<String>>,
}

#[derive(Deserialize)]
struct AuditResponse {
    records: Vec<AuditRecord>,
}

pub struct Client {
    base: String,
    http: reqwest::blocking::Client,
    poll_interval: Duration,
    poll_timeout: Duration,
}

impl Client {
    pub fn new(base: &str) -> Self {
        Client {
            base: base.trim_end_matches('/').to_string(),
            http: reqwest::blocking::Client::new(),
            poll_interval: Duration::from_millis(200),
            poll_timeout: Duration::from_secs(120),
        }
    }

    pub fn with_polling(mut self, interval: Duration, timeout: Duration) -> Self {
        self.poll_interval = interval;
        self.poll_timeout = timeout;
        self
    }

    pub fn new_session(&self) -> Result<String, ClientError> {
        let resp = self
            .http
            .post(format!("{}/v1/sessions", self.base))
            .send()?;
        let resp = check(resp)?;
        Ok(resp.json::<CreateSessionResponse>()?.session_id)
    }

    /// Party A: submit blinded set, wait for B, compute the intersection
    /// locally. Returns the intersection as a sorted set of raw elements.
    pub fn run_party_a(
        &self,
        session_hex: &str,
        elements: &[Vec<u8>],
    ) -> Result<Vec<Vec<u8>>, ClientError> {
        let session_id = decode_session_id(session_hex)?;
        let (party, dedupe) = PartyA::new(session_id, elements);
        if dedupe.duplicates_removed() > 0 {
            eprintln!(
                "[psi-client] note: {} duplicate element(s) collapsed to set semantics",
                dedupe.duplicates_removed()
            );
        }
        let blinded: Vec<String> = party.blinded_message().iter().map(hex::encode).collect();
        let resp = self
            .http
            .post(format!("{}/v1/sessions/{session_hex}/submissions/a", self.base))
            .json(&serde_json::json!({ "points": blinded }))
            .send()?;
        check(resp)?;

        let deadline = std::time::Instant::now() + self.poll_timeout;
        loop {
            let resp = self
                .http
                .get(format!("{}/v1/sessions/{session_hex}/messages/b", self.base))
                .send()?;
            let msg: MessagesBResponse = check(resp)?.json()?;
            if let (Some(b_hex), Some(d_hex)) = (msg.b_blinded, msg.a_doubly) {
                let b_blinded = crypto::decode_points(&decode_hex_vec(&b_hex)?)?;
                let a_doubly = crypto::decode_points(&decode_hex_vec(&d_hex)?)?;
                return Ok(party.compute_intersection(&b_blinded, &a_doubly)?);
            }
            if std::time::Instant::now() > deadline {
                return Err(ClientError::Timeout(msg.state));
            }
            std::thread::sleep(self.poll_interval);
        }
    }

    /// Party B: wait for A's blinded set, respond, done. Learns nothing.
    pub fn run_party_b(&self, session_hex: &str, elements: &[Vec<u8>]) -> Result<(), ClientError> {
        let session_id = decode_session_id(session_hex)?;
        let deadline = std::time::Instant::now() + self.poll_timeout;
        loop {
            let resp = self
                .http
                .get(format!("{}/v1/sessions/{session_hex}/messages/a", self.base))
                .send()?;
            let msg: MessagesAResponse = check(resp)?.json()?;
            if let Some(points_hex) = msg.points {
                let a_blinded = crypto::decode_points(&decode_hex_vec(&points_hex)?)?;
                let response = PartyB::respond(&session_id, elements, &a_blinded);
                if response.dedupe.duplicates_removed() > 0 {
                    eprintln!(
                        "[psi-client] note: {} duplicate element(s) collapsed to set semantics",
                        response.dedupe.duplicates_removed()
                    );
                }
                let resp = self
                    .http
                    .post(format!("{}/v1/sessions/{session_hex}/submissions/b", self.base))
                    .json(&serde_json::json!({
                        "b_blinded": response.b_blinded.iter().map(hex::encode).collect::<Vec<_>>(),
                        "a_doubly": response.a_doubly.iter().map(hex::encode).collect::<Vec<_>>(),
                    }))
                    .send()?;
                check(resp)?;
                return Ok(());
            }
            if std::time::Instant::now() > deadline {
                return Err(ClientError::Timeout(msg.state));
            }
            std::thread::sleep(self.poll_interval);
        }
    }

    pub fn audit(&self, session_hex: &str) -> Result<Vec<AuditRecord>, ClientError> {
        let resp = self
            .http
            .get(format!("{}/v1/sessions/{session_hex}/audit", self.base))
            .send()?;
        Ok(check(resp)?.json::<AuditResponse>()?.records)
    }
}

fn decode_session_id(session_hex: &str) -> Result<[u8; crypto::SESSION_ID_LEN], ClientError> {
    let bytes = hex::decode(session_hex).map_err(|_| {
        ClientError::Protocol(PsiError::BadRequest("session id is not valid hex".into()))
    })?;
    bytes
        .as_slice()
        .try_into()
        .map_err(|_| ClientError::Protocol(PsiError::BadRequest("session id must be 32 bytes".into())))
}

fn decode_hex_vec(hexes: &[String]) -> Result<Vec<[u8; 32]>, ClientError> {
    let mut out = Vec::with_capacity(hexes.len());
    for (i, h) in hexes.iter().enumerate() {
        let bytes = hex::decode(h)
            .map_err(|_| PsiError::BadRequest(format!("point {i}: invalid hex in server message")))?;
        let arr: [u8; 32] = bytes
            .as_slice()
            .try_into()
            .map_err(|_| PsiError::BadPointLength {
                index: i,
                got: bytes.len(),
            })?;
        out.push(arr);
    }
    Ok(out)
}

fn check(resp: reqwest::blocking::Response) -> Result<reqwest::blocking::Response, ClientError> {
    if resp.status().is_success() {
        return Ok(resp);
    }
    let status = resp.status().as_u16();
    match resp.json::<ServerErrorBody>() {
        Ok(body) => Err(ClientError::Server {
            status,
            code: body.error.code,
            message: body.error.message,
            request_id: body.error.request_id,
        }),
        Err(_) => Err(ClientError::Server {
            status,
            code: "UNKNOWN".into(),
            message: "unparseable error body".into(),
            request_id: String::new(),
        }),
    }
}
