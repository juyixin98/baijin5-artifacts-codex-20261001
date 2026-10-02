#pragma once
#include "pade/types.hpp"

namespace pade {

// Compute the [m/n] Pade approximant of the series C(x) = sum c_k x^k.
//
// Contract:
//  * needs at least m + n + 1 coefficients.
//  * denominator constant is normalized to q0 = 1 whenever that is possible.
//  * when the Toeplitz block is singular, ranks decide the branch:
//        rank([b|A]) > rank(A)  -> DegenerateDenominatorConstant,
//                                  q0 = 1 is impossible, q0 = 0 homogeneous
//                                  representative retained for diagnosis;
//        rank([b|A]) == rank(A) -> RankDeficient, minimum-norm q0 = 1 solve.
//  * p and q always carry the UNREDUCED local definition; any detected common
//    factor is stored separately (gcd_*, *_reduced) so local information is
//    never erased.
SolveReport solvePade(const SeriesRequest& req);

// Evaluate a produced approximant. Uses the reduced fraction when available,
// but reports which form was used. DenominatorNearZero is a hard failure.
EvalReport evaluate(const SolveReport& rep, Real x,
                    Real pole_relative_tol = Real(1e-12));

} // namespace pade
