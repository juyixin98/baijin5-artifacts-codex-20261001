//! Binary entry point: `serve` runs the Axum API, `verify` runs the
//! independent differential validation suites.
//!
//! Usage (intentionally dependency-free argument parsing):
//!   tvindex [--config FILE] serve
//!   tvindex [--config FILE] verify [--root DIR]

use std::path::PathBuf;
use std::sync::Arc;

use tvindex::api::limited_router;
use tvindex::config::Config;
use tvindex::state::{AppState, TableStore};
use tvindex::verify;

struct Cli {
    config: PathBuf,
    command: Command,
}

enum Command {
    Serve,
    Verify { root: PathBuf },
}

const HELP: &str = "\
tvindex - column-value bitmap indexes with SQL three-valued-logic filtering

USAGE:
    tvindex [--config FILE] <COMMAND>

COMMANDS:
    serve    Start the HTTP query API
    verify   Run the independent scalar-oracle differential suites

OPTIONS:
    --config FILE   TOML config path (default: config/tvindex.toml)
    --root DIR      verify only: project root with fixtures/ (default: .)
    -h, --help      Show this help
";

fn parse_args(argv: &[String]) -> Result<Cli, String> {
    let mut config = PathBuf::from("config/tvindex.toml");
    let mut iter = argv.iter();
    let mut command: Option<&str> = None;
    let mut root = PathBuf::from(".");
    while let Some(arg) = iter.next() {
        match arg.as_str() {
            "-h" | "--help" => {
                print!("{HELP}");
                std::process::exit(0);
            }
            "--config" => {
                config = PathBuf::from(iter.next().ok_or("--config needs a FILE argument")?);
            }
            "--root" => {
                root = PathBuf::from(iter.next().ok_or("--root needs a DIR argument")?);
            }
            "serve" if command.is_none() => command = Some("serve"),
            "verify" if command.is_none() => command = Some("verify"),
            other => return Err(format!("unexpected argument {other:?}\n\n{HELP}")),
        }
    }
    let command = match command {
        Some("serve") => Command::Serve,
        Some("verify") => Command::Verify { root },
        Some(other) => unreachable!("{other}"),
        None => return Err(format!("missing COMMAND\n\n{HELP}")),
    };
    Ok(Cli { config, command })
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    let cli = match parse_args(&argv) {
        Ok(cli) => cli,
        Err(e) => {
            eprintln!("{e}");
            std::process::exit(2);
        }
    };
    let config = Arc::new(Config::load(Some(&cli.config))?);
    init_tracing(&config.log.level);

    let rt = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()?;

    match cli.command {
        Command::Serve => rt.block_on(serve(config))?,
        Command::Verify { root } => {
            let suites = verify::run_builtin(&root);
            let mut total_cases = 0usize;
            let mut failing_cases = 0usize;
            for (name, report) in &suites {
                let cases = report.cases.len();
                let case_fails = report.cases.iter().filter(|c| !c.passed()).count();
                total_cases += cases;
                failing_cases += case_fails;
                println!("suite {name}: {cases} cases, {case_fails} failing");
                if case_fails > 0 {
                    println!("{}", report.render_failures());
                }
            }
            println!("verify summary: {total_cases} cases, {failing_cases} failing");
            if failing_cases != 0 {
                std::process::exit(1);
            }
        }
    }
    Ok(())
}

async fn serve(config: Arc<Config>) -> Result<(), Box<dyn std::error::Error>> {
    let store = TableStore::load(&config)?;
    tracing::info!(
        table = %store.manifest.table,
        rows = store.table.len(),
        live = store.versions.live_bitmap().count_ones(),
        content_version = store.versions.index_version(),
        head_version = store.versions.head_version(),
        addr = %config.bind_addr(),
        "loaded fixture and built all column bitmap indexes"
    );
    let state = AppState {
        store,
        config: config.clone(),
    };
    let app = limited_router(state, config.server.max_body_bytes);
    let listener = tokio::net::TcpListener::bind(config.bind_addr()).await?;
    tracing::info!("tvindex listening on {}", listener.local_addr()?);
    axum::serve(listener, app).await?;
    Ok(())
}

fn init_tracing(level: &str) {
    use tracing_subscriber::filter::LevelFilter;
    // LevelFilter (not EnvFilter) so logging never depends on the regex
    // crate's optional unicode-case feature in minimal offline builds.
    let filter: LevelFilter = match level.to_ascii_lowercase().as_str() {
        "trace" => LevelFilter::TRACE,
        "debug" => LevelFilter::DEBUG,
        "info" => LevelFilter::INFO,
        "warn" => LevelFilter::WARN,
        "error" | "off" => LevelFilter::ERROR,
        other => {
            eprintln!("unknown log level {other:?}, defaulting to info");
            LevelFilter::INFO
        }
    };
    tracing_subscriber::fmt()
        .with_max_level(filter)
        .with_target(false)
        .compact()
        .init();
}
