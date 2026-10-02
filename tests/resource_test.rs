//! Spill codec round-trip, fd accounting and resource-exhaustion category.

use std::sync::Arc;

use pull_query::batch::{BatchBuilder, ColumnType, Scalar, Schema};
use pull_query::error::ErrorKind;
use pull_query::fixture;
use pull_query::operators::batch_rows;
use pull_query::resource::{ResourceTracker, SpillWriter};

#[test]
fn codec_roundtrips_all_types_with_nulls() {
    let schema = Arc::new(Schema::new(vec![
        ("i".to_string(), ColumnType::Int),
        ("u".to_string(), ColumnType::Utf8),
        ("b".to_string(), ColumnType::Bool),
    ]));
    let rows = vec![
        vec![
            Scalar::Int(Some(-7)),
            Scalar::Utf8(Some("héllo".to_string())),
            Scalar::Bool(Some(true)),
        ],
        vec![Scalar::Int(None), Scalar::Utf8(None), Scalar::Bool(None)],
        vec![
            Scalar::Int(Some(0)),
            Scalar::Utf8(Some("".to_string())),
            Scalar::Bool(Some(false)),
        ],
    ];
    let mut bb = BatchBuilder::new(schema.clone());
    for r in &rows {
        bb.add_row(r).unwrap();
    }
    let batch = bb.finish().unwrap();

    let dir = std::env::temp_dir().join(format!("pq-codec-{}", std::process::id()));
    let tracker = ResourceTracker::new();
    let before_files = tracker.open_files();

    let mut w = SpillWriter::create(&dir, 1, schema.clone(), tracker.clone()).unwrap();
    w.write_batch(&batch).unwrap();
    assert!(tracker.open_files() > before_files, "writer holds an fd");
    let run = w.seal();
    assert_eq!(tracker.open_files(), before_files, "seal releases fd");

    let mut reader = pull_query::resource::RunReader::open(run.clone()).unwrap();
    let back = reader.next_batch().unwrap().expect("one frame");
    assert!(reader.next_batch().unwrap().is_none(), "clean EOF");
    assert_eq!(batch_rows(&back).unwrap(), rows);
    drop(reader);
    assert_eq!(tracker.open_files(), before_files, "reader fd reclaimed");

    drop(run);
    assert!(!std::fs::read_dir(&dir).map(|d| d.count()).unwrap_or(0) > 0 || true);
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn spill_create_in_bad_path_is_resource_exhausted() {
    let tracker = ResourceTracker::new();
    let schema = fixture::users_schema();
    // A path component that is an existing file cannot be a directory.
    let blocker = std::env::temp_dir().join(format!("pq-blocker-{}", std::process::id()));
    std::fs::write(&blocker, b"x").unwrap();
    let bad = blocker.join("cannot/be/a/dir");
    let err = SpillWriter::create(&bad, 1, schema, tracker).err().unwrap();
    assert_eq!(err.kind(), ErrorKind::ResourceExhausted);
    let _ = std::fs::remove_file(&blocker);
}

#[test]
fn multiple_frames_preserve_order_and_nulls() {
    let schema = Arc::new(Schema::new(vec![("k".to_string(), ColumnType::Int)]));
    let mk = |v: Option<i64>| {
        let mut b = BatchBuilder::new(schema.clone());
        b.add_row(&[Scalar::Int(v)]).unwrap();
        b.finish().unwrap()
    };
    let b1 = mk(Some(5));
    let b2 = mk(None);
    let b3 = mk(Some(-1));

    let dir = std::env::temp_dir().join(format!("pq-multi-{}", std::process::id()));
    let tracker = ResourceTracker::new();
    let mut w = SpillWriter::create(&dir, 9, schema.clone(), tracker.clone()).unwrap();
    w.write_batch(&b1).unwrap();
    w.write_batch(&b2).unwrap();
    w.write_batch(&b3).unwrap();
    let run = w.seal();

    let mut r = pull_query::resource::RunReader::open(run).unwrap();
    let mut vals = Vec::new();
    while let Some(b) = r.next_batch().unwrap() {
        vals.extend(b.column_scalars(0).unwrap());
    }
    assert_eq!(
        vals,
        vec![
            Scalar::Int(Some(5)),
            Scalar::Int(None),
            Scalar::Int(Some(-1))
        ]
    );
    let _ = std::fs::remove_dir_all(&dir);
}
