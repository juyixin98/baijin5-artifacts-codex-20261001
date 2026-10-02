#include "rlmf/factorizer_session.hpp"

namespace rlmf {

Error FactorizerSession::set_config(const FactorizeConfig& cfg) {
    if (phase_ != Phase::Configured)
        return Error{ErrorKind::StateConflict, "session.config_locked",
                     "configuration cannot change after input was set; call "
                     "reset() first"};
    cfg_ = cfg;
    return Error{};
}

Error FactorizerSession::set_input(Matrix a) {
    if (phase_ != Phase::Configured && phase_ != Phase::InputReady)
        return Error{ErrorKind::StateConflict, "session.input_locked",
                     "input cannot be replaced after compute(); call reset()"};
    input_ = std::move(a);
    phase_ = Phase::InputReady;
    return Error{};
}

Result<Factorization> FactorizerSession::compute() {
    if (phase_ == Phase::Configured)
        return Result<Factorization>{
            Error{ErrorKind::StateConflict, "session.no_input",
                  "compute() called before set_input()"}};
    if (phase_ == Phase::Computed)
        return Result<Factorization>{
            Error{ErrorKind::StateConflict, "session.already_computed",
                  "compute() called twice; take() the result or reset()"}};
    if (phase_ == Phase::Consumed)
        return Result<Factorization>{
            Error{ErrorKind::StateConflict, "session.consumed",
                  "session already consumed; call reset()"}};
    auto r = randomized_factorize(input_, cfg_);
    if (!r.ok())
        return r;
    result_ = std::move(r.value());
    phase_ = Phase::Computed;
    return Result<Factorization>{result_};
}

Result<Factorization> FactorizerSession::take() {
    if (phase_ != Phase::Computed)
        return Result<Factorization>{
            Error{ErrorKind::StateConflict, "session.not_computed",
                  "take() requires a successful compute() first"}};
    phase_ = Phase::Consumed;
    return Result<Factorization>{std::move(result_)};
}

void FactorizerSession::reset(FactorizeConfig cfg) {
    cfg_ = cfg;
    input_ = Matrix{};
    result_ = Factorization{};
    phase_ = Phase::Configured;
}

} // namespace rlmf
