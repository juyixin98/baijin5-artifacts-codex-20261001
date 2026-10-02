//! Unit tests for the typed batch layer and value model.

#[cfg(test)]
mod tests {
    use crate::batch::{Scalar, TypedBatch};
    use crate::plan::{ColumnDecl, ColumnType, Relation};
    use serde_json::json;

    fn decl(name: &str, ty: ColumnType) -> ColumnDecl {
        ColumnDecl {
            name: name.to_string(),
            data_type: ty,
        }
    }

    #[test]
    fn scalar_parsing_is_type_strict() {
        assert!(matches!(
            Scalar::from_json(&json!(7), ColumnType::Int64),
            Ok(Scalar::Int(7))
        ));
        assert!(Scalar::from_json(&json!(true), ColumnType::Int64).is_err());
        assert!(Scalar::from_json(&json!("x"), ColumnType::Bool).is_err());
        assert!(matches!(
            Scalar::from_json(&json!(null), ColumnType::Int64),
            Ok(Scalar::Null)
        ));
        assert!(Scalar::from_json(&json!([1, "x"]), ColumnType::ListInt64).is_err());
    }

    #[test]
    fn int_list_round_trips() {
        let s = Scalar::from_json(&json!([1, 2, 3]), ColumnType::ListInt64).unwrap();
        assert_eq!(s.to_json(), json!([1, 2, 3]));
    }

    #[test]
    fn batch_validates_every_cell_and_reports_column_and_row() {
        let rel = Relation {
            columns: vec![decl("a", ColumnType::Int64)],
            rows: vec![vec![json!(1)], vec![json!("bad")]],
        };
        let err = TypedBatch::from_relation(&rel).unwrap_err();
        assert_eq!(err.category, crate::error::FailureCategory::InvalidData);
        assert!(err.message.contains("column 'a'"));
        assert!(err.message.contains("row 1"));
    }

    #[test]
    fn row_fingerprint_changes_with_value_and_is_column_scoped() {
        let r1 = vec![Scalar::Int(1), Scalar::Int(2)];
        let r2 = vec![Scalar::Int(1), Scalar::Int(3)];
        let all = [0usize, 1];
        let only_a = [0usize];
        assert_ne!(
            TypedBatch::row_fingerprint(&r1, &all),
            TypedBatch::row_fingerprint(&r2, &all)
        );
        assert_eq!(
            TypedBatch::row_fingerprint(&r1, &only_a),
            TypedBatch::row_fingerprint(&r2, &only_a),
            "rows sharing the scoped columns must hash equal"
        );
    }

    #[test]
    fn bool_and_int_never_collide() {
        let r1 = vec![Scalar::Int(1)];
        let r2 = vec![Scalar::Bool(true)];
        let fp = |r: &[Scalar]| TypedBatch::row_fingerprint(r, &[0]);
        assert_ne!(fp(&r1), fp(&r2), "type tag prevents int/bool collision");
    }
}
