//! Logical query plan: grouped aggregation definitions.
//!
//! The plan is deliberately independent of the wire types: the HTTP layer
//! builds it from JSON ([`Plan::from_json`]), while tests and the example
//! generator can construct it directly.

use std::collections::hash_map::DefaultHasher;
use std::hash::{Hash, Hasher};

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::batch::DataType;
use crate::error::{Error, ErrorKind, Result};

/// Sort direction for ordered aggregations.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum SortOrder {
    Asc,
    Desc,
}

/// The exact aggregation operator requested for one output column.
#[derive(Debug, Clone, PartialEq)]
pub enum AggOp {
    /// SQL `PERCENTILE_CONT`: linear interpolation between the two closest
    /// values. Only defined on numeric inputs; always yields float64 even when
    /// the input is int64.
    PercentileCont { q: f64 },
    /// SQL `PERCENTILE_DISC`: the discrete percentile — the first value whose
    /// cumulative distribution is `>= q`. Preserves the input value type.
    PercentileDisc { q: f64 },
    /// SQL `MODE() WITHIN GROUP (ORDER BY ...)`: most frequent non-null value.
    /// Ties are resolved by the sort order (smallest value wins), matching
    /// PostgreSQL's documented behaviour.
    Mode,
    /// SQL `LISTAGG` / `STRING_AGG`: non-null strings joined with a delimiter,
    /// ordered ascending or descending. Equal strings keep insertion order
    /// (stable sort).
    StringAgg { delimiter: String, order: SortOrder },
}

impl AggOp {
    pub fn name(&self) -> &'static str {
        match self {
            AggOp::PercentileCont { .. } => "percentile_cont",
            AggOp::PercentileDisc { .. } => "percentile_disc",
            AggOp::Mode => "mode",
            AggOp::StringAgg { .. } => "string_agg",
        }
    }

    /// Input physical type accepted by this operator.
    pub fn accepts(&self, input: DataType) -> bool {
        match self {
            AggOp::PercentileCont { .. } | AggOp::PercentileDisc { .. } => {
                matches!(input, DataType::Int64 | DataType::Float64)
            }
            AggOp::Mode => true,
            AggOp::StringAgg { .. } => matches!(input, DataType::Utf8),
        }
    }
}

/// One aggregation: `op(input) AS alias`.
#[derive(Debug, Clone, PartialEq)]
pub struct AggDef {
    pub alias: String,
    pub input: String,
    pub op: AggOp,
}

/// A grouped aggregation plan.
#[derive(Debug, Clone, PartialEq)]
pub struct Plan {
    pub group_by: Vec<String>,
    pub aggregations: Vec<AggDef>,
}

impl Plan {
    /// Build a plan from the JSON `plan` object of a request:
    /// ```json
    /// {
    ///   "group_by": ["g"],
    ///   "aggregations": [
    ///     {"alias":"p50","column":"v","op":"percentile_cont","quantile":0.5},
    ///     {"alias":"m","column":"c","op":"mode"},
    ///     {"alias":"s","column":"t","op":"string_agg",
    ///      "delimiter":"|","order":"desc"}
    ///   ]
    /// }
    /// ```
    pub fn from_json(value: &Value) -> Result<Self> {
        let obj = value
            .as_object()
            .ok_or_else(|| Error::invalid_request("plan must be a JSON object"))?;

        let group_by = match obj.get("group_by") {
            Some(Value::Array(a)) => a
                .iter()
                .map(|v| {
                    v.as_str()
                        .map(str::to_string)
                        .ok_or_else(|| Error::invalid_request("group_by entries must be strings"))
                })
                .collect::<Result<Vec<_>>>()?,
            Some(Value::Null) | None => Vec::new(),
            Some(_) => return Err(Error::invalid_request("group_by must be an array")),
        };

        let aggs_json = obj
            .get("aggregations")
            .and_then(Value::as_array)
            .ok_or_else(|| Error::invalid_request("plan.aggregations must be a non-empty array"))?;
        if aggs_json.is_empty() {
            return Err(Error::invalid_request(
                "plan.aggregations must not be empty",
            ));
        }

        let mut aggregations = Vec::with_capacity(aggs_json.len());
        for a in aggs_json {
            aggregations.push(parse_agg(a)?);
        }

        let plan = Plan {
            group_by,
            aggregations,
        };
        plan.validate_quantiles()?;
        Ok(plan)
    }

    /// Quantile range checks, isolated so they can run *before* the engine
    /// touches any data. `NaN` and infinities are rejected as well.
    pub fn validate_quantiles(&self) -> Result<()> {
        for agg in &self.aggregations {
            let q = match agg.op {
                AggOp::PercentileCont { q } | AggOp::PercentileDisc { q } => Some(q),
                _ => None,
            };
            if let Some(q) = q {
                if !q.is_finite() || !(0.0..=1.0).contains(&q) {
                    return Err(Error::invalid_quantile(q));
                }
            }
        }
        Ok(())
    }

    /// Stable hash over the plan contents, used to bind resume tokens to the
    /// exact plan they were created for.
    pub fn plan_hash(&self) -> String {
        #[derive(Serialize)]
        struct Canonical<'a> {
            group_by: &'a [String],
            aggregations: Vec<CanonicalAgg<'a>>,
        }
        #[derive(Serialize)]
        struct CanonicalAgg<'a> {
            alias: &'a str,
            column: &'a str,
            op: &'static str,
            quantile: Option<f64>,
            delimiter: Option<&'a str>,
            order: Option<SortOrder>,
        }

        let canonical = Canonical {
            group_by: &self.group_by,
            aggregations: self
                .aggregations
                .iter()
                .map(|a| CanonicalAgg {
                    alias: &a.alias,
                    column: &a.input,
                    op: a.op.name(),
                    quantile: match a.op {
                        AggOp::PercentileCont { q } | AggOp::PercentileDisc { q } => Some(q),
                        _ => None,
                    },
                    delimiter: match &a.op {
                        AggOp::StringAgg { delimiter, .. } => Some(delimiter.as_str()),
                        _ => None,
                    },
                    order: match &a.op {
                        AggOp::StringAgg { order, .. } => Some(*order),
                        _ => None,
                    },
                })
                .collect(),
        };
        let json = serde_json::to_string(&canonical).expect("canonical plan serialization");
        let mut hasher = DefaultHasher::new();
        json.hash(&mut hasher);
        format!("{:016x}", hasher.finish())
    }
}

fn parse_agg(value: &Value) -> Result<AggDef> {
    let obj = value
        .as_object()
        .ok_or_else(|| Error::invalid_request("each aggregation must be an object"))?;
    let alias = obj
        .get("alias")
        .and_then(Value::as_str)
        .ok_or_else(|| Error::invalid_request("aggregation.alias is required"))?
        .to_string();
    let input = obj
        .get("column")
        .and_then(Value::as_str)
        .ok_or_else(|| Error::invalid_request("aggregation.column is required"))?
        .to_string();
    let op_name = obj
        .get("op")
        .and_then(Value::as_str)
        .ok_or_else(|| Error::invalid_request("aggregation.op is required"))?;

    let op = match op_name {
        "percentile_cont" | "percentile_disc" => {
            let q = obj.get("quantile").and_then(Value::as_f64).ok_or_else(|| {
                Error::invalid_request(format!("{op_name} requires a numeric 'quantile' field"))
            })?;
            // Range is rejected here too, but Plan::validate_quantiles is the
            // single chokepoint the engine calls before execution.
            if !q.is_finite() || !(0.0..=1.0).contains(&q) {
                return Err(Error::invalid_quantile(q));
            }
            if op_name == "percentile_cont" {
                AggOp::PercentileCont { q }
            } else {
                AggOp::PercentileDisc { q }
            }
        }
        "mode" => AggOp::Mode,
        "string_agg" => {
            let delimiter = obj
                .get("delimiter")
                .and_then(Value::as_str)
                .unwrap_or(",")
                .to_string();
            if delimiter.is_empty() {
                return Err(Error::new(
                    ErrorKind::InvalidRequest,
                    "string_agg delimiter must contain at least one character",
                ));
            }
            let order = match obj.get("order") {
                None | Some(Value::Null) => SortOrder::Asc,
                Some(Value::String(s)) => match s.as_str() {
                    "asc" => SortOrder::Asc,
                    "desc" => SortOrder::Desc,
                    other => {
                        return Err(Error::invalid_request(format!(
                            "string_agg order must be 'asc' or 'desc', got {other}"
                        )));
                    }
                },
                Some(_) => {
                    return Err(Error::invalid_request("string_agg order must be a string"));
                }
            };
            AggOp::StringAgg { delimiter, order }
        }
        other => {
            return Err(Error::invalid_request(format!(
                "unknown aggregation op '{other}'"
            )));
        }
    };

    Ok(AggDef { alias, input, op })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn parses_full_plan() {
        let plan = Plan::from_json(&json!({
            "group_by": ["g"],
            "aggregations": [
                {"alias":"p","column":"v","op":"percentile_disc","quantile":0.25},
                {"alias":"s","column":"t","op":"string_agg","delimiter":"|","order":"desc"}
            ]
        }))
        .unwrap();
        assert_eq!(plan.group_by, vec!["g".to_string()]);
        assert_eq!(
            plan.aggregations[1].op,
            AggOp::StringAgg {
                delimiter: "|".into(),
                order: SortOrder::Desc
            }
        );
    }

    #[test]
    fn rejects_quantile_above_one_before_execution() {
        let err = Plan::from_json(&json!({
            "group_by": [],
            "aggregations": [
                {"alias":"p","column":"v","op":"percentile_cont","quantile":1.01}
            ]
        }))
        .unwrap_err();
        assert_eq!(err.kind, ErrorKind::InvalidQuantile);
    }

    #[test]
    fn rejects_nan_quantile() {
        let plan = Plan {
            group_by: vec![],
            aggregations: vec![AggDef {
                alias: "p".into(),
                input: "v".into(),
                op: AggOp::PercentileCont { q: f64::NAN },
            }],
        };
        assert_eq!(
            plan.validate_quantiles().unwrap_err().kind,
            ErrorKind::InvalidQuantile
        );
    }

    #[test]
    fn rejects_negative_quantile_and_unknown_op() {
        let err = Plan::from_json(&json!({
            "aggregations": [
                {"alias":"p","column":"v","op":"percentile_disc","quantile":-0.0001}
            ]
        }))
        .unwrap_err();
        assert_eq!(err.kind, ErrorKind::InvalidQuantile);

        let err = Plan::from_json(&json!({
            "aggregations": [{"alias":"x","column":"v","op":"bogus"}]
        }))
        .unwrap_err();
        assert_eq!(err.kind, ErrorKind::InvalidRequest);
    }

    #[test]
    fn plan_hash_is_stable() {
        let p1 = Plan::from_json(&json!({
            "group_by": ["g"],
            "aggregations": [
                {"alias":"p","column":"v","op":"percentile_cont","quantile":0.5}
            ]
        }))
        .unwrap();
        let p2 = p1.clone();
        assert_eq!(p1.plan_hash(), p2.plan_hash());
        assert_eq!(p1.plan_hash().len(), 16);
    }
}
