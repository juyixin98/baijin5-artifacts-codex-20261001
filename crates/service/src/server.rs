//! Dependency-free local HTTP front-end (synthesis/demo use only).

use crate::api::InterpolateRequest;
use crate::config::ServiceConfig;
use crate::handler::handle_interpolate;
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::thread;

pub fn serve(config: ServiceConfig) -> std::io::Result<()> {
    let address = format!("{}:{}", config.http_host, config.http_port);
    let listener = TcpListener::bind(&address)?;
    eprintln!("ipc service listening on http://{address}");
    for incoming in listener.incoming() {
        let stream = incoming?;
        let config = config.clone();
        thread::spawn(move || {
            if let Err(error) = handle_connection(stream, &config) {
                eprintln!("connection error: {error}");
            }
        });
    }
    Ok(())
}

fn handle_connection(mut stream: TcpStream, config: &ServiceConfig) -> std::io::Result<()> {
    let mut buffer = [0u8; 65536];
    let read = stream.read(&mut buffer)?;
    let raw = String::from_utf8_lossy(&buffer[..read]);
    let mut lines = raw.split("\r\n");
    let request_line = lines.next().unwrap_or("");
    let mut parts = request_line.split_whitespace();
    let method = parts.next().unwrap_or("GET");
    let path = parts.next().unwrap_or("/");

    let (status, body) = if method == "GET" && path == "/healthz" {
        (
            "200 OK",
            serde_json::json!({ "status": "ok", "service": "craig-ipc" }).to_string(),
        )
    } else if method == "POST" && path == "/interpolate" {
        let body_text = raw.split("\r\n\r\n").nth(1).unwrap_or("");
        match serde_json::from_str::<InterpolateRequest>(body_text) {
            Ok(request) => {
                let response = handle_interpolate(request, config);
                ("200 OK", serde_json::to_string_pretty(&response).unwrap())
            }
            Err(error) => (
                "400 Bad Request",
                serde_json::json!({
                    "status": "invalid_request",
                    "error": error.to_string()
                })
                .to_string(),
            ),
        }
    } else {
        (
            "404 Not Found",
            serde_json::json!({"error": "unknown route"}).to_string(),
        )
    };

    let response = format!(
        "HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
        body.len()
    );
    stream.write_all(response.as_bytes())?;
    Ok(())
}
