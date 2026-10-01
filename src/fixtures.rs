//! Deterministic local synthetic fixtures.
//!
//! No real business data is used anywhere; every dataset is generated here
//! from constants.  The fixtures are deliberately small enough to hand-compute
//! while still spanning multiple [`Batch`]es, so integration tests exercise the
//! multi-run external-sort path rather than a single in-memory buffer.

use crate::batch::{Batch, Column, DataType, Field};

/// A fixture: typed schema plus one or more row batches.
pub struct Fixture {
    pub name: &'static str,
    pub schema: Vec<Field>,
    pub batches: Vec<Batch>,
}

impl Fixture {
    pub fn total_rows(&self) -> usize {
        self.batches.iter().map(Batch::row_count).sum()
    }
}

/// Column layout shared by every fixture:
/// `g`     — group key (utf8)
/// `score` — continuous measurement (float64, nullable)
/// `level` — discrete measurement (int64, nullable)
/// `tag`   — categorical value for mode / string aggregation (utf8, nullable)
pub fn standard_schema() -> Vec<Field> {
    vec![
        Field::new("g", DataType::Utf8),
        Field::new("score", DataType::Float64),
        Field::new("level", DataType::Int64),
        Field::new("tag", DataType::Utf8),
    ]
}

/// The hand-computed even sample.
///
/// Group `even` has scores `[10, 20, 30, 40]` (one value per batch, in
/// shuffled ingestion order) and levels `[1, 2, 3, 4]`.
///
/// Reference (`PERCENTILE_CONT` on an even sample):
/// ```sql
/// SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY score) FROM t WHERE g='even';
/// -- rank = 0.5*(4-1) = 1.5 → 20 + 0.5*(30-20) = 25
/// ```
pub fn even_sample() -> Fixture {
    let schema = standard_schema();
    // Deliberately shuffled across batches to prove sorting happens.
    let b1 = batch(
        &["even", "even"],
        &[Some(30.0), Some(10.0)],
        &[Some(3), Some(1)],
        &[Some("c"), Some("a")],
    );
    let b2 = batch(
        &["even", "even"],
        &[Some(40.0), Some(20.0)],
        &[Some(4), Some(2)],
        &[Some("d"), Some("b")],
    );
    Fixture {
        name: "even_sample",
        schema,
        batches: vec![b1, b2],
    }
}

/// A group whose measurement columns are entirely NULL.
pub fn all_nulls() -> Fixture {
    let schema = standard_schema();
    let b1 = batch(
        &["nullish", "nullish"],
        &[None, None],
        &[None, None],
        &[None, None],
    );
    let b2 = batch(&["nullish"], &[None], &[None], &[None]);
    Fixture {
        name: "all_nulls",
        schema,
        batches: vec![b1, b2],
    }
}

/// Mode tie fixture: tags `a,a,b,b` within one group — smallest must win.
/// Also carries scores so the same fixture can be aggregated several ways.
pub fn mode_tie() -> Fixture {
    let schema = standard_schema();
    let b1 = batch(
        &["tie", "tie"],
        &[Some(1.0), Some(2.0)],
        &[Some(5), Some(6)],
        &[Some("b"), Some("a")],
    );
    let b2 = batch(
        &["tie", "tie"],
        &[Some(3.0), Some(4.0)],
        &[Some(7), Some(8)],
        &[Some("a"), Some("b")],
    );
    Fixture {
        name: "mode_tie",
        schema,
        batches: vec![b1, b2],
    }
}

/// Large repeat group: 1_000 rows where tag is always `"z"` plus three
/// `"a"` rows, split over several batches.  Mode must be `"z"` and the
/// ascending string aggregation must contain 1_000 `z` entries.
pub fn large_repeat_group() -> Fixture {
    let schema = standard_schema();
    let mut batches = Vec::new();
    let mut score = 0i64;
    for chunk in 0..10 {
        let n = if chunk < 9 { 100 } else { 3 };
        let groups = vec!["big"; n];
        let scores: Vec<Option<f64>> = (0..n)
            .map(|_| {
                score += 1;
                Some(score as f64)
            })
            .collect();
        let levels: Vec<Option<i64>> = scores.iter().map(|v| v.map(|x| x as i64)).collect();
        let tags: Vec<Option<&str>> = if chunk < 9 {
            vec![Some("z"); n]
        } else {
            vec![Some("a"); n]
        };
        batches.push(batch(&groups, &scores, &levels, &tags));
    }
    Fixture {
        name: "large_repeat_group",
        schema,
        batches,
    }
}

/// Skew fixture: one heavy group (`heavy`) with long strings and a large row
/// count alongside many tiny singleton groups.  Used to prove memory stays
/// bounded and distinct small groups do not accumulate raw values.
pub fn skewed_groups(heavy_rows: usize, small_groups: usize) -> Fixture {
    let schema = standard_schema();
    let long = "long-string-payload-".repeat(4) + "0123456789";
    let mut batches = Vec::new();

    // Heavy group spread over multiple batches.
    let mut produced = 0;
    while produced < heavy_rows {
        let n = 50.min(heavy_rows - produced);
        let groups = vec!["heavy"; n];
        let scores: Vec<Option<f64>> = (0..n).map(|i| Some((produced + i) as f64)).collect();
        let levels: Vec<Option<i64>> = (0..n).map(|i| Some((produced + i) as i64)).collect();
        let tags: Vec<Option<String>> = (0..n).map(|i| Some(format!("{long}-{i:03}"))).collect();
        batches.push(
            Batch::new(
                schema.clone(),
                vec![
                    Column::from_utf8(groups.into_iter().map(str::to_string).map(Some).collect()),
                    Column::from_f64(scores),
                    Column::from_i64(levels),
                    Column::from_utf8(tags),
                ],
            )
            .expect("skew heavy batch is valid"),
        );
        produced += n;
    }

    // Many small groups, one row each, in their own batches.
    for start in (0..small_groups).step_by(25) {
        let end = (start + 25).min(small_groups);
        let groups: Vec<String> = (start..end).map(|i| format!("small-{i:04}")).collect();
        let scores: Vec<Option<f64>> = (start..end).map(|i| Some(i as f64)).collect();
        let levels: Vec<Option<i64>> = (start..end).map(|i| Some(i as i64)).collect();
        let tags: Vec<Option<String>> = (start..end).map(|i| Some(format!("t{i}"))).collect();
        batches.push(
            Batch::new(
                schema.clone(),
                vec![
                    Column::from_utf8(groups.into_iter().map(Some).collect()),
                    Column::from_f64(scores),
                    Column::from_i64(levels),
                    Column::from_utf8(tags),
                ],
            )
            .expect("skew small batch is valid"),
        );
    }

    Fixture {
        name: "skewed_groups",
        schema,
        batches,
    }
}

/// All fixtures in one directory, handy for parameterized integration tests.
pub fn all_standard() -> Vec<Fixture> {
    vec![even_sample(), all_nulls(), mode_tie(), large_repeat_group()]
}

fn batch(
    groups: &[&str],
    scores: &[Option<f64>],
    levels: &[Option<i64>],
    tags: &[Option<&str>],
) -> Batch {
    Batch::new(
        standard_schema(),
        vec![
            Column::from_utf8(groups.iter().map(|s| Some((*s).to_string())).collect()),
            Column::from_f64(scores.to_vec()),
            Column::from_i64(levels.to_vec()),
            Column::from_utf8(tags.iter().map(|t| t.map(|s| s.to_string())).collect()),
        ],
    )
    .expect("fixture batch is internally consistent")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fixtures_have_expected_row_counts_and_schema() {
        assert_eq!(even_sample().total_rows(), 4);
        assert_eq!(all_nulls().total_rows(), 3);
        assert_eq!(mode_tie().total_rows(), 4);
        assert_eq!(large_repeat_group().total_rows(), 903);
        for f in all_standard() {
            assert!(f.batches.len() >= 2, "{} should span batches", f.name);
            assert_eq!(f.schema.len(), 4);
            assert!(f.batches.iter().all(|b| b.fields().len() == 4));
        }
    }

    #[test]
    fn skew_fixture_scales() {
        let f = skewed_groups(130, 40);
        assert_eq!(f.total_rows(), 170);
        assert_eq!(f.batches.len(), 3 + 2);
    }
}
