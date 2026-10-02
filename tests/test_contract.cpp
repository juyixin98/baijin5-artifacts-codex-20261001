// Contract tests: input validation, residual oracle, fixture IO.
#include <cmath>
#include <filesystem>
#include <fstream>
#include <vector>

#include "pade/io.hpp"
#include "pade/solver.hpp"
#include "test_framework.hpp"

using pade::Options;
using pade::StatusCode;

TEST_CASE("invalid and insufficient inputs return explicit failures") {
    Options o; o.numerator_order = 2; o.denominator_order = 2;
    auto r1 = pade::padeApproximate({}, o);
    CHECK(r1.status == StatusCode::kInvalidArgument);
    CHECK(!r1.message.empty());

    std::vector<pade::Real> shortc = {1.0, 1.0};
    auto r2 = pade::padeApproximate(shortc, o);
    CHECK(r2.status == StatusCode::kInsufficientCoeffs);
    CHECK(!r2.message.empty());

    Options neg; neg.numerator_order = -1; neg.denominator_order = 1;
    auto r3 = pade::padeApproximate({1.0, 1.0}, neg);
    CHECK(r3.status == StatusCode::kInvalidArgument);

    std::vector<pade::Real> nanv = {1.0, std::nan(""), 0.5};
    auto r4 = pade::padeApproximate(nanv, o);
    CHECK(r4.status == StatusCode::kInvalidArgument);
}

TEST_CASE("residual oracle flags wrong coefficients") {
    // exp [1/1] coeffs; corrupt the numerator -> mismatch must be detected.
    std::vector<pade::Real> c = {1, 1, 0.5, 1.0 / 6};
    auto good = pade::verifyResiduals(c, {1.0, 0.5}, {1.0, -0.5}, 1, 1);
    CHECK(good.matches_to_order);
    CHECK_CLOSE(good.max_abs_residual, 0.0, 1e-13);
    auto bad = pade::verifyResiduals(c, {1.0, 0.6}, {1.0, -0.5}, 1, 1);
    CHECK(!bad.matches_to_order);
    CHECK(std::abs(bad.residual[1]) > 1e-3);
}

TEST_CASE("series fixture file round trip and parse errors") {
    namespace fs = std::filesystem;
    fs::path tmp = fs::temp_directory_path() / "pade_fixture_series.txt";
    {
        std::ofstream out(tmp);
        out << "# synthetic fixture\nname = exp-5\n"
            << "coefficients = 1, 1, 0.5, 0.16666666666666666, 0.041666666666666664\n";
    }
    pade::io::SeriesFile sf;
    CHECK(pade::io::loadSeriesFile(tmp.string(), sf) == StatusCode::kOk);
    CHECK(sf.name == "exp-5");
    CHECK(sf.coeffs.size() == 5);
    CHECK_CLOSE(sf.coeffs[4], 1.0 / 24.0, 1e-15);

    fs::path bad = fs::temp_directory_path() / "pade_fixture_bad.txt";
    { std::ofstream out(bad); out << "garbage line no equals\n"; }
    pade::io::SeriesFile sf2;
    CHECK(pade::io::loadSeriesFile(bad.string(), sf2) ==
          StatusCode::kInvalidArgument);

    pade::io::SeriesFile sf3;
    CHECK(pade::io::loadSeriesFile("/nonexistent/path/xyz", sf3) ==
          StatusCode::kIoError);
}
