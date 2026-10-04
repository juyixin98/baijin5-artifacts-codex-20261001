use dnnf_mc::engine::count;
use dnnf_mc::syntax::Node;
use num_bigint::BigUint;

#[test]
fn smoke_or_of_two_literals() {
    // Deterministic OR: x1 OR (!x1 AND x2).
    // Assignments: x1=1 (x2 free) -> 2; x1=0,x2=1 -> 1. Total 3.
    let n = Node::Or {
        children: vec![
            Node::Lit { var: 1, polarity: true },
            Node::And {
                children: vec![
                    Node::Lit { var: 1, polarity: false },
                    Node::Lit { var: 2, polarity: true },
                ],
            },
        ],
    };
    assert_eq!(count(&n), BigUint::from(3u32));
}
