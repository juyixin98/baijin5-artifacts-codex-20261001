#pragma once

#include "rlmf/error.hpp"
#include "rlmf/factorizer.hpp"

namespace rlmf {

// Stateful façade enforcing a fixed lifecycle:
//   Configured -> InputReady -> Computed -> (Consumed / Reset)
// Calling a method in the wrong phase returns ErrorKind::StateConflict.
class FactorizerSession {
public:
    enum class Phase { Configured, InputReady, Computed, Consumed };

    FactorizerSession() = default;
    explicit FactorizerSession(FactorizeConfig cfg) : cfg_(cfg) {}

    Phase phase() const noexcept { return phase_; }
    const FactorizeConfig& config() const noexcept { return cfg_; }

    Error set_config(const FactorizeConfig& cfg);
    Error set_input(Matrix a);
    Result<Factorization> compute();
    // Taking the result is only legal once; afterwards phase is Consumed.
    Result<Factorization> take();
    void reset(FactorizeConfig cfg);

private:
    FactorizeConfig cfg_{};
    Matrix input_{};
    Factorization result_{};
    Phase phase_ = Phase::Configured;
};

} // namespace rlmf
