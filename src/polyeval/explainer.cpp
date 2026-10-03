#include "polyeval/explainer.hpp"

namespace polyeval::explainer {

std::size_t bit_length(const contract::Big& v) {
  contract::Big a = v < 0 ? -v : v;
  return static_cast<std::size_t>(boost::multiprecision::msb(a)) + 1;
}

PointExplanation explain_point(const contract::PointResult& pr, contract::Mode mode,
                               std::uint64_t prime) {
  PointExplanation e;
  e.index = pr.index;
  e.point = pr.point.str();
  e.value = pr.value.str();
  if (mode == contract::Mode::Field) {
    e.status = "FIELD_RESIDUE";
    e.uncertain = false;
    e.interpretation =
        "exact residue P(x) mod " + std::to_string(prime) +
        " in [0," + std::to_string(prime) + "); the integer evaluation may have wrapped";
  } else {
    if (pr.uncertain) {
      e.status = "UNCERTAIN";
      e.uncertain = true;
      e.interpretation =
          "exact tree result computed, but the independent Horner cross-check was skipped "
          "(bit length exceeds configured cap); " + pr.note;
    } else {
      e.status = "EXACT";
      e.uncertain = false;
      e.interpretation =
          "exact integer value; independent Horner cross-check " +
          std::string(pr.exact_crosschecked ? "agreed" : "not run") + "; no rounding";
    }
  }
  return e;
}

}  // namespace polyeval::explainer
