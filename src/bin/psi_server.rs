//! psi-server: local PSI relay service. Sees only blinded points.

use psi_dh::config::ServerConfig;

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "psi_dh=info,tower_http=info".into()),
        )
        .init();

    let config = ServerConfig::from_env();
    if let Err(e) = psi_dh::server::run(config).await {
        eprintln!("psi-server failed: {e}");
        std::process::exit(1);
    }
}
