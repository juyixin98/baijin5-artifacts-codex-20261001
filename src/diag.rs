//! Diagnostics: human-readable, explainable rendering of a run.
//!
//! The same summary is emitted to the log at run completion and exposed via
//! the API, so what an operator reads in the logs matches what a client sees.

use crate::model::MergeRun;

/// Render a multi-line plain-text summary of a run.
pub fn summarize(run: &MergeRun) -> String {
    let mut out = String::new();
    out.push_str(&format!(
        "run={} request={} tool_version={} status={:?}\n",
        run.run_id, run.request_id, run.tool_version, run.status
    ));
    out.push_str(&format!(
        "window={} .. {} output={}\n",
        run.started_at, run.finished_at, run.output_dir
    ));
    for layer in &run.layers {
        out.push_str(&format!("  layer[{}] {}\n", layer.index, layer.path));
    }
    out.push_str("steps:\n");
    for s in &run.steps {
        out.push_str(&format!("  #{} {}: {}\n", s.seq, s.name, s.detail));
    }
    out.push_str(&format!("entries: {}\n", run.entries.len()));
    for e in &run.entries {
        let extra = match (&e.sha256, &e.link_target) {
            (Some(h), _) => format!(" sha256={} size={}", h, e.size.unwrap_or(0)),
            (_, Some(t)) => format!(" -> {t}"),
            _ => String::new(),
        };
        out.push_str(&format!(
            "  {:?} {} (layer {}){}\n",
            e.kind, e.path, e.source.index, extra
        ));
    }
    out.push_str(&format!("failures: {}\n", run.failures.len()));
    for f in &run.failures {
        out.push_str(&format!(
            "  [{}] path={:?} layer={:?} {}\n",
            f.category,
            f.path,
            f.layer.as_ref().map(|l| l.index),
            f.message
        ));
    }
    out.push_str(&format!("uncertainties: {}\n", run.uncertainties.len()));
    for u in &run.uncertainties {
        out.push_str(&format!(
            "  [{:?}] {} (layer {}) {}\n",
            u.reason, u.path, u.layer.index, u.message
        ));
    }
    out
}
