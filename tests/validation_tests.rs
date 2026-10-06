//! Validation and diagnostics tests: concrete rejection categories,
//! evidence-based acceptance, undecidability, and redaction.

use dmc::config::Config;
use dmc::diag::Category;
use dmc::pipeline::run_request;
use dmc::proof::DeterminismEvidence;
use dmc::request::Request;
use dmc::syntax::{Circuit, Node};
use dmc::validate::{validate, Rejection};

fn lit(id: u32, var: u32, phase: bool) -> Node {
    Node::Lit { id, var, phase }
}

fn request(circuit: Circuit, declared: Option<Vec<u32>>) -> Request {
    Request {
        request_id: "t-req".to_string(),
        label: None,
        declared_vars: declared,
        circuit,
    }
}

#[test]
fn overlapping_and_is_rejected_with_var_and_children() {
    // AND(x1, x1 OR x2): variable 1 occurs in both AND children.
    let c = Circuit {
        root: 0,
        nodes: vec![
            Node::And {
                id: 0,
                children: vec![1, 2],
            },
            lit(1, 1, true),
            Node::Or {
                id: 2,
                children: vec![3, 4],
            },
            lit(3, 1, true),
            lit(4, 2, true),
        ],
    };
    let err = validate(&c, &Config::default()).expect_err("must reject overlapping AND");
    assert_eq!(
        err,
        Rejection::AndNotDecomposable {
            node: 0,
            var: 1,
            children: (1, 2)
        }
    );
    let res = run_request(&request(c, None), &Config::default());
    assert_eq!(res.diagnostic.category, Category::AndNotDecomposable);
    assert_eq!(res.diagnostic.node, Some(0));
    assert!(res.diagnostic.detail.contains("variable 1"));
    assert!(res.count.is_none());
}

#[test]
fn non_deterministic_or_is_rejected_with_witness() {
    // OR(x1, x1 AND x2): both children true under x1=x2=true.
    let c = Circuit {
        root: 0,
        nodes: vec![
            Node::Or {
                id: 0,
                children: vec![1, 2],
            },
            lit(1, 1, true),
            Node::And {
                id: 2,
                children: vec![3, 4],
            },
            lit(3, 1, true),
            lit(4, 2, true),
        ],
    };
    let err = validate(&c, &Config::default()).expect_err("must reject non-deterministic OR");
    match err {
        Rejection::OrNotDeterministic {
            node,
            pair,
            witness,
        } => {
            assert_eq!(node, 0);
            assert_eq!(pair, (1, 2));
            // Witness must actually satisfy both children.
            let get = |v: u32| {
                witness
                    .iter()
                    .find(|(x, _)| *x == v)
                    .map(|(_, p)| *p)
                    .unwrap_or(false)
            };
            assert!(get(1) && get(2), "witness must set x1 and x2 true");
        }
        other => panic!("expected OrNotDeterministic, got {other:?}"),
    }
    let res = run_request(&request(c, None), &Config::default());
    assert_eq!(res.diagnostic.category, Category::OrNotDeterministic);
}

#[test]
fn or_without_evidence_beyond_threshold_is_undecidable() {
    // OR(x1, x2): no forced-literal evidence; with enumeration
    // threshold 0 the checker refuses to guess.
    let c = Circuit {
        root: 0,
        nodes: vec![
            Node::Or {
                id: 0,
                children: vec![1, 2],
            },
            lit(1, 1, true),
            lit(2, 2, true),
        ],
    };
    let strict = Config {
        determinism_enumeration_threshold: 0,
        ..Config::default()
    };
    let err = validate(&c, &strict).expect_err("must be undecidable");
    assert_eq!(
        err,
        Rejection::Undecidable {
            node: 0,
            pair: (1, 2),
            scope_size: 2
        }
    );
    let res = run_request(&request(c, None), &strict);
    assert_eq!(res.diagnostic.category, Category::Undecidable);
    assert!(res.diagnostic.detail.contains("threshold 0"));
}

#[test]
fn forced_literal_evidence_accepts_complementary_split() {
    // OR(x1 AND x2, x1 AND NOT x2): children disagree on forced x2.
    let c = Circuit {
        root: 0,
        nodes: vec![
            Node::Or {
                id: 0,
                children: vec![1, 2],
            },
            Node::And {
                id: 1,
                children: vec![3, 4],
            },
            Node::And {
                id: 2,
                children: vec![5, 6],
            },
            lit(3, 1, true),
            lit(4, 2, true),
            lit(5, 1, true),
            lit(6, 2, false),
        ],
    };
    let proof = validate(&c, &Config::default()).expect("must accept");
    assert_eq!(proof.or_checks.len(), 1);
    assert_eq!(
        proof.or_checks[0].evidence,
        DeterminismEvidence::ForcedLiteral { var: 2 }
    );
}

#[test]
fn enumeration_evidence_for_evidence_free_deterministic_or() {
    // OR(XNOR, XOR) over {1,2}: deterministic, but neither child forces
    // any literal, so only enumeration can prove it.
    let cube = |id: u32, l: (u32, bool), r: (u32, bool), base: u32| {
        vec![
            Node::And {
                id,
                children: vec![base, base + 1],
            },
            lit(base, l.0, l.1),
            lit(base + 1, r.0, r.1),
        ]
    };
    // xnor = (x1&x2) | (~x1&~x2); xor = (x1&~x2) | (~x1&x2)
    let mut nodes = vec![
        Node::Or {
            id: 0,
            children: vec![1, 2],
        },
        Node::Or {
            id: 1,
            children: vec![10, 20],
        },
        Node::Or {
            id: 2,
            children: vec![30, 40],
        },
    ];
    nodes.extend(cube(10, (1, true), (2, true), 11));
    nodes.extend(cube(20, (1, false), (2, false), 21));
    nodes.extend(cube(30, (1, true), (2, false), 31));
    nodes.extend(cube(40, (1, false), (2, true), 41));
    let c = Circuit { root: 0, nodes };
    let proof = validate(&c, &Config::default()).expect("must accept via enumeration");
    assert!(
        proof
            .or_checks
            .iter()
            .any(|r| matches!(r.evidence, DeterminismEvidence::Enumeration { .. })),
        "expected enumeration evidence, got {:?}",
        proof.or_checks
    );
}

#[test]
fn unsat_child_evidence_for_or_with_false_branch() {
    // OR(x1, FALSE): the false child is unsatisfiable.
    let c = Circuit {
        root: 0,
        nodes: vec![
            Node::Or {
                id: 0,
                children: vec![1, 2],
            },
            lit(1, 1, true),
            Node::False { id: 2 },
        ],
    };
    let proof = validate(&c, &Config::default()).expect("must accept");
    assert_eq!(
        proof.or_checks[0].evidence,
        DeterminismEvidence::UnsatChild { child: 2 }
    );
}

#[test]
fn syntax_errors_are_reported_as_invalid_syntax() {
    let cases: Vec<Circuit> = vec![
        // duplicate node id
        Circuit {
            root: 0,
            nodes: vec![lit(0, 1, true), lit(0, 2, true)],
        },
        // unknown child
        Circuit {
            root: 0,
            nodes: vec![Node::And {
                id: 0,
                children: vec![9],
            }],
        },
        // empty AND
        Circuit {
            root: 0,
            nodes: vec![Node::And {
                id: 0,
                children: vec![],
            }],
        },
        // cycle: 0 -> 1 -> 0
        Circuit {
            root: 0,
            nodes: vec![
                Node::Or {
                    id: 0,
                    children: vec![1],
                },
                Node::And {
                    id: 1,
                    children: vec![0],
                },
            ],
        },
    ];
    for c in cases {
        let res = run_request(&request(c, None), &Config::default());
        assert_eq!(
            res.diagnostic.category,
            Category::InvalidSyntax,
            "case detail: {}",
            res.diagnostic.detail
        );
    }
}

#[test]
fn duplicate_declared_vars_are_bad_universe() {
    let c = Circuit {
        root: 0,
        nodes: vec![lit(0, 1, true)],
    };
    let res = run_request(&request(c, Some(vec![1, 1, 2])), &Config::default());
    assert_eq!(res.diagnostic.category, Category::BadUniverse);
}

#[test]
fn declared_universe_must_cover_circuit_variables() {
    // Circuit mentions var 5 but the universe declares only {1,2}.
    let c = Circuit {
        root: 0,
        nodes: vec![lit(0, 5, true)],
    };
    let res = run_request(&request(c, Some(vec![1, 2])), &Config::default());
    assert_eq!(res.diagnostic.category, Category::BadUniverse);
    assert!(res.diagnostic.detail.contains("{5}"));
}

#[test]
fn diagnostics_carry_request_id_and_redacted_label_only() {
    let secret = "acme-corp-payroll-2026";
    let c = Circuit {
        root: 0,
        nodes: vec![lit(0, 1, true)],
    };
    let mut req = request(c, None);
    req.request_id = "req-42".to_string();
    req.label = Some(secret.to_string());
    let res = run_request(&req, &Config::default());
    let json = res.diagnostic.to_json();
    assert!(json.contains("req-42"));
    assert!(!json.contains(secret), "label must not leak: {json}");
    assert!(json.contains("redacted:"), "expected fingerprint: {json}");
}
