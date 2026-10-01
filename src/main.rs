//! set_ops CLI.
//!
//! Subcommands: `serve` starts the Axum validation/execution service; `run`
//! executes one operator over typed CSV fixtures and emits JSON; `fixture`
//! generates synthetic datasets (duplicates, nested nulls, collision-prone
//! strings, skew) into a directory.

use std::net::SocketAddr;
use std::path::PathBuf;
use std::process::ExitCode;

use clap::{Parser, Subcommand, ValueEnum};

use set_ops::batch::json::batch_to_json;
use set_ops::batch::read_typed_csv;
use set_ops::operator::{ExecutionMode, Qualifier, Query, SetOp, execute};
use set_ops::resource::ResourceLimits;
use set_ops::service::AppState;

#[derive(Parser)]
#[command(name = "set_ops", version, about)]
struct Cli {
    #[command(subcommand)]
    command: Cmd,
}

#[derive(Subcommand)]
enum Cmd {
    /// Run the HTTP service.
    Serve {
        #[arg(long, default_value = "127.0.0.1:8080")]
        addr: SocketAddr,
        #[arg(long, default_value = "fixtures")]
        fixture_root: PathBuf,
        #[arg(long, default_value = "target/spill")]
        spill_root: PathBuf,
    },
    /// Execute one set operator locally.
    Run {
        #[arg(long)]
        left: PathBuf,
        #[arg(long)]
        right: PathBuf,
        #[arg(long)]
        op: String,
        #[arg(long, value_enum, default_value_t = QualifierArg::Distinct)]
        qualifier: QualifierArg,
        #[arg(long, value_enum, default_value_t = ModeArg::Auto)]
        mode: ModeArg,
        /// Resident key-byte budget; forces external-memory behaviour when
        /// smaller than the dataset's distinct-key footprint.
        #[arg(long, default_value_t = 64 * 1024 * 1024)]
        memory_bytes: usize,
        #[arg(long, default_value_t = 64)]
        fanout: usize,
        #[arg(long, default_value_t = 8)]
        max_depth: usize,
        /// Reject multiplicities above this value (overflow rule).
        #[arg(long, default_value_t = u64::MAX)]
        max_count: u64,
        #[arg(long)]
        spill_dir: Option<PathBuf>,
        #[arg(long)]
        run_id: Option<String>,
        /// Rows read per Arrow2 input chunk.
        #[arg(long, default_value_t = 37)]
        batch_rows: usize,
    },
    /// Emit synthetic fixture datasets.
    Fixture {
        #[arg(long, default_value = "fixtures/generated")]
        out: PathBuf,
        #[arg(long, default_value_t = 2000)]
        rows: usize,
        /// Key space size; small => heavy duplicates and skew.
        #[arg(long, default_value_t = 50)]
        keyspace: usize,
        #[arg(long, value_enum, default_value_t = FixtureKind::All)]
        kind: FixtureKind,
    },
}

#[derive(Clone, Copy, ValueEnum)]
enum QualifierArg {
    Distinct,
    All,
}

#[derive(Clone, Copy, ValueEnum)]
enum ModeArg {
    InMemory,
    Auto,
    External,
}

#[derive(Clone, Copy, ValueEnum)]
enum FixtureKind {
    /// collision-prone text pairs
    Collisions,
    /// nested nulls in every column
    Nulls,
    /// duplicated/skewed rows
    Duplicates,
    /// combined stress dataset
    All,
}

fn main() -> ExitCode {
    let cli = Cli::parse();
    match cli.command {
        Cmd::Serve {
            addr,
            fixture_root,
            spill_root,
        } => {
            let rt = tokio::runtime::Builder::new_multi_thread()
                .enable_all()
                .build()
                .expect("tokio runtime");
            let state = AppState::new(fixture_root, spill_root);
            rt.block_on(async move {
                eprintln!("set_ops listening on http://{addr}");
                if let Err(e) = set_ops::service::serve(state, addr).await {
                    eprintln!("server error: {e}");
                    std::process::exit(1);
                }
            });
            ExitCode::SUCCESS
        }
        Cmd::Run {
            left,
            right,
            op,
            qualifier,
            mode,
            memory_bytes,
            fanout,
            max_depth,
            max_count,
            spill_dir,
            run_id,
            batch_rows,
        } => {
            let result = run_local(RunArgs {
                left,
                right,
                op,
                qualifier: match qualifier {
                    QualifierArg::Distinct => Qualifier::Distinct,
                    QualifierArg::All => Qualifier::All,
                },
                mode: match mode {
                    ModeArg::InMemory => ExecutionMode::InMemory,
                    ModeArg::Auto => ExecutionMode::Auto,
                    ModeArg::External => ExecutionMode::External,
                },
                memory_bytes,
                fanout,
                max_depth,
                max_count,
                spill_dir,
                run_id,
                batch_rows,
            });
            match result {
                Ok(()) => ExitCode::SUCCESS,
                Err(e) => {
                    let envelope = serde_json::json!({
                        "status": "error",
                        "error": { "kind": e.kind, "message": e.message, "context": e.context }
                    });
                    eprintln!("{}", serde_json::to_string_pretty(&envelope).unwrap());
                    // Distinct exit codes per failure family for scripts.
                    match e.kind {
                        set_ops::ErrorKind::Input(_) => ExitCode::from(2),
                        set_ops::ErrorKind::StateConflict(_) => ExitCode::from(3),
                        set_ops::ErrorKind::ResourceExhausted(_) => ExitCode::from(4),
                        set_ops::ErrorKind::Compute => ExitCode::from(5),
                    }
                }
            }
        }
        Cmd::Fixture {
            out,
            rows,
            keyspace,
            kind,
        } => {
            write_fixtures(&out, rows, keyspace, kind);
            ExitCode::SUCCESS
        }
    }
}

struct RunArgs {
    left: PathBuf,
    right: PathBuf,
    op: String,
    qualifier: Qualifier,
    mode: ExecutionMode,
    memory_bytes: usize,
    fanout: usize,
    max_depth: usize,
    max_count: u64,
    spill_dir: Option<PathBuf>,
    run_id: Option<String>,
    batch_rows: usize,
}

fn run_local(args: RunArgs) -> Result<(), set_ops::SetOpsError> {
    let op = SetOp::parse(&args.op)
        .map_err(|m| set_ops::SetOpsError::input(set_ops::InputCode::InvalidRequest, m))?;
    let (left_schema, mut left_batches) = read_typed_csv(&args.left, args.batch_rows)?;
    let (right_schema, mut right_batches) = read_typed_csv(&args.right, args.batch_rows)?;
    // Schema is carried by the header even for a rowless side.
    left_schema.compatible_with(&right_schema)?;
    if left_batches.is_empty() {
        left_batches.push(set_ops::batch::build_batch(&left_schema, &[])?);
    }
    if right_batches.is_empty() {
        right_batches.push(set_ops::batch::build_batch(&right_schema, &[])?);
    }

    let limits = ResourceLimits {
        memory_bytes: args.memory_bytes,
        partition_fanout: args.fanout,
        max_partition_depth: args.max_depth,
        max_count: args.max_count,
        ..ResourceLimits::default()
    };

    let out = execute(
        Query::new(op, args.qualifier, left_batches, right_batches),
        limits,
        args.mode,
        args.spill_dir.as_deref(),
        args.run_id,
    )?;

    let rows: Vec<_> = out.batches.iter().flat_map(batch_to_json).collect();
    let envelope = serde_json::json!({
        "status": "ok",
        "run_id": out.stats.run_id,
        "stats": out.stats,
        "replay": out.log.replay_summary(),
        "rows": rows,
    });
    println!("{}", serde_json::to_string_pretty(&envelope).unwrap());
    Ok(())
}

fn write_fixtures(dir: &std::path::Path, rows: usize, keyspace: usize, kind: FixtureKind) {
    std::fs::create_dir_all(dir).expect("create fixture dir");
    let header = "id:bigint,grp:bigint,label:text,flag:boolean,score:double\n";

    let write_side = |name: &str, seed: u64| {
        use std::io::Write;
        let path = dir.join(name);
        let mut f = std::fs::File::create(&path).expect("create fixture file");
        f.write_all(header.as_bytes()).unwrap();
        let mut rng = Rng::new(seed);
        for i in 0..rows {
            // Zipf-ish skew: key 0 appears ~25% of the time.
            let roll = rng.next() % 100;
            let grp = if roll < 25 {
                0
            } else {
                (rng.next() as usize) % keyspace.max(1)
            };
            let id = i as i64;
            let label = collision_label(kind, &mut rng, grp);
            // ~12% nulls, including nulls in adjacent columns ("nested" nulls)
            let flag: String = if i % 9 == 0 {
                String::new()
            } else if grp % 2 == 0 {
                "true".into()
            } else {
                "false".into()
            };
            let score = if i % 7 == 0 {
                String::new()
            } else {
                format!("{:.3}", (grp as f64) / 7.0)
            };
            // quote labels containing commas
            let label_field = if label.contains(',') || label.contains('"') {
                format!("\"{}\"", label.replace('"', "\"\""))
            } else {
                label
            };
            writeln!(f, "{id},{grp},{label_field},{flag},{score}").unwrap();
        }
        eprintln!("wrote {path:?} ({rows} rows, keyspace {keyspace})");
    };

    write_side("left.csv", 0x1234_5678);
    write_side("right.csv", 0x9abc_def0);

    // Small handcrafted edge fixtures covering nested NULLs and exact counts.
    std::fs::write(
        dir.join("edge_left.csv"),
        "a:text,b:text\n\
         x,\\N\n\
         \\N,y\n\
         \\N,\\N\n\
         12,3\n\
         1,23\n\
         \"1\",\"23\"\n",
    )
    .expect("edge fixture");
    std::fs::write(
        dir.join("edge_right.csv"),
        "a:text,b:text\n\
         x,\\N\n\
         \\N,y\n\
         \\N,\\N\n\
         \\N,\\N\n\
         12,3\n\
         1,23\n",
    )
    .expect("edge fixture");
}

/// Labels crafted to collide under naive concatenation/typing:
/// digit strings with boundaries, textual numbers, embedded commas/quotes.
fn collision_label(kind: FixtureKind, rng: &mut Rng, grp: usize) -> String {
    match kind {
        FixtureKind::Nulls => {
            if grp.is_multiple_of(3) {
                "\\N".to_string()
            } else {
                format!("n{grp}")
            }
        }
        FixtureKind::Collisions => match grp % 6 {
            0 => "123".into(),
            1 => "1,23".into(),
            2 => "12,3".into(),
            3 => "1".repeat(1 + (rng.next() as usize % 3)),
            4 => "\"quoted\"".into(),
            _ => format!("{grp}"),
        },
        FixtureKind::Duplicates => format!("dup{:03}", grp % 7),
        FixtureKind::All => match grp % 10 {
            0 => String::new(), // NULL
            1 => "\\N".into(),
            2 => "12,3".into(),
            3 => "1,23".into(),
            4 => "123".into(),
            5 => "\"q\"".into(),
            6 => format!("dup{:03}", grp % 5),
            7 => format!("{grp}"),
            8 => "héllo".into(),
            _ => "".to_string(),
        },
    }
}

/// Tiny deterministic xorshift so generated fixtures are replayable.
struct Rng(u64);

impl Rng {
    fn new(seed: u64) -> Self {
        Self(seed | 1)
    }
    fn next(&mut self) -> u64 {
        let mut x = self.0;
        x ^= x << 13;
        x ^= x >> 7;
        x ^= x << 17;
        self.0 = x;
        x
    }
}
