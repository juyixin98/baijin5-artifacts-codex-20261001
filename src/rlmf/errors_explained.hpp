#pragma once

#include "rlmf/error.hpp"
#include "rlmf/factorizer.hpp"

#include <string>
#include <vector>

namespace rlmf {

// One line of "evidence" behind an error/quality judgement.
struct Evidence {
    std::string name;
    std::string observed;
    std::string expected;
    bool passed = false;
};

// Translates a factorization outcome into human- and machine-readable
// explanation, keeping ESTIMATED error separate from the EXACT residual and
// from the SVD-optimal tail. Nothing here claims exact optimality.
struct ErrorExplanation {
    bool ok = false;
    std::string headline;
    std::vector<Evidence> evidence;
};

ErrorExplanation explain(const Factorization& fac) noexcept;
ErrorExplanation explain(const Error& err) noexcept;

// Convenience: relative comparison helpers reused by tests/CLI.
bool within(double observed, double expected, double rel_tol,
            double abs_tol = 1e-12) noexcept;

} // namespace rlmf
