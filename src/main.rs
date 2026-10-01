//! Binary entry point. All logic lives in the `iejoin` library crate
//! so the integration tests and the CLI share one contract.

use clap::Parser;

fn main() -> std::process::ExitCode {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("warn")),
        )
        .with_writer(std::io::stderr)
        .init();

    let cli = iejoin::cli::Cli::parse();
    iejoin::cli::run(cli)
}
