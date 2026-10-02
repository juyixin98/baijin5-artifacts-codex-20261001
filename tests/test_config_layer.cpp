// Configuration layer and synthetic series IO: errors must be explicit.
#include "test_framework.hpp"
#include "pade/config.hpp"
#include "pade/seriesio.hpp"
#include <cmath>
using namespace pade_test;
using namespace pade;

TEST_CASE(config_parses_known_values_and_comments) {
    cfg::Config c = cfg::parse(
        "# a comment\nrank_tol_factor = 1e-9\nresidual_extra_terms = 4\n"
        "remove_common_factor = false\n");
    check(c.parse_errors.empty(), "no parse errors",
          c.parse_errors.empty() ? "" : c.parse_errors.front());
    check(approx(c.solve.rank_tol_factor, 1e-9L, 1e-20L), "rank tol parsed", "");
    check(c.solve.residual_extra_terms == 4, "extra terms parsed", "");
    check(c.solve.remove_common_factor == false, "bool parsed", "");
    check(c.unknown_keys.empty(), "no unknown keys", "");
}

TEST_CASE(config_reports_bad_values_and_unknown_keys) {
    cfg::Config c = cfg::parse(
        "rank_tol_factor = notanumber\nbogus_key = 3\nresidual_extra_terms=7\n");
    check(!c.parse_errors.empty(), "bad real is an error, not a default", "");
    check(c.unknown_keys.size() == 1, "unknown key surfaced",
          "count=" + std::to_string(c.unknown_keys.size()));
    // Known good values still applied despite other errors.
    check(c.solve.residual_extra_terms == 7, "good value applied", "");
}

TEST_CASE(config_malformed_line_is_an_error) {
    cfg::Config c = cfg::parse("this has no equals sign\nrank_tol_factor=1\n");
    check(c.parse_errors.size() == 1, "single malformed line error",
          "count=" + std::to_string(c.parse_errors.size()));
}

TEST_CASE(synthetic_series_generators_match_known_coefficients) {
    auto ex = series::generate(series::Kind::Exp, 5);
    check(approx(ex[0], 1) && approx(ex[1], 1) && approx(ex[2], Real(1)/2)
          && approx(ex[3], Real(1)/6) && approx(ex[4], Real(1)/24),
          "exp coefficients", "");

    auto si = series::generate(series::Kind::Sin, 6);
    check(approx(si[0], 0) && approx(si[1], 1) && approx(si[2], 0)
          && approx(si[3], -Real(1)/6) && approx(si[5], Real(1)/120),
          "sin coefficients", "");

    auto geo = series::generate(series::Kind::Geometric, 4, {Real(2)});
    check(approx(geo[0], 1) && approx(geo[1], 2) && approx(geo[2], 4)
          && approx(geo[3], 8), "geometric r=2 coefficients", "");
}

TEST_CASE(coefficient_roundtrip_preserves_values) {
    std::vector<Real> c = {1, -Real(1)/3, Real(1)/7, Real(1e-12L)};
    std::string err;
    check(series::saveCoefficients("/tmp/pade_coeff_rt.txt", c, err),
          "save ok", err);
    auto loaded = series::loadCoefficients("/tmp/pade_coeff_rt.txt", err);
    check(err.empty(), "load without error", err);
    check(loaded.size() == c.size(), "same count",
          std::to_string(loaded.size()));
    for (size_t i = 0; i < c.size(); ++i)
        check(approx(loaded[i], c[i], 1e-18L),
              "coeff " + std::to_string(i) + " survives hexfloat roundtrip", "");
}

TEST_CASE(coefficient_load_rejects_bad_line) {
    std::string err;
    auto c = series::loadCoefficients("/nonexistent/path/does-not-exist.txt", err);
    check(c.empty() && !err.empty(), "missing file is an explicit error", err);
}
