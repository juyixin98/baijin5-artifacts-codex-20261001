//! Server configuration. Environment-driven so the binary needs no config
//! file to run; see `config/server.env.example` for the documented knobs.

pub struct ServerConfig {
    pub bind_addr: String,
    pub db_path: String,
    pub max_set_size: usize,
    pub max_body_bytes: usize,
}

pub const DEFAULT_BIND_ADDR: &str = "127.0.0.1:38711";
pub const DEFAULT_DB_PATH: &str = "psi.db";
pub const DEFAULT_MAX_SET_SIZE: usize = 100_000;
pub const DEFAULT_MAX_BODY_BYTES: usize = 64 * 1024 * 1024;

impl ServerConfig {
    pub fn from_env() -> Self {
        ServerConfig {
            bind_addr: env_or("PSI_BIND_ADDR", DEFAULT_BIND_ADDR),
            db_path: env_or("PSI_DB_PATH", DEFAULT_DB_PATH),
            max_set_size: env_or_parse("PSI_MAX_SET_SIZE", DEFAULT_MAX_SET_SIZE),
            max_body_bytes: env_or_parse("PSI_MAX_BODY_BYTES", DEFAULT_MAX_BODY_BYTES),
        }
    }
}

fn env_or(key: &str, default: &str) -> String {
    std::env::var(key).unwrap_or_else(|_| default.to_string())
}

fn env_or_parse<T: std::str::FromStr>(key: &str, default: T) -> T {
    std::env::var(key)
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(default)
}
