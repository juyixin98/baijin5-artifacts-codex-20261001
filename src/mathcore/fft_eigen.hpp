#pragma once
// Eigen-native facade over the FFT contract. The numeric backend stays in
// std::complex<double>; this layer adapts Eigen dense vectors so downstream
// Eigen code can call the arbitrary-length FFT without manual conversion.
#include "fft.hpp"
#include "common/status.hpp"
#include <Eigen/Dense>
#include <complex>

namespace fft::mathcore {

struct EigenFftResult {
  common::FftError error;
  Eigen::VectorXcd out;
  std::size_t convolutionLength = 0;
  std::size_t peakWorkspaceBytes = 0;
  const char* kernelPath = "";
};

inline EigenFftResult fftEigen(const Eigen::VectorXcd& x,
                               const common::FftOptions& options = {}) {
  std::vector<Cmplx> tmp(x.size());
  for (Eigen::Index i = 0; i < x.size(); ++i)
    tmp[static_cast<std::size_t>(i)] = Cmplx(x[i].real(), x[i].imag());
  auto r = fft(tmp, options);
  EigenFftResult e;
  e.error = r.error;
  e.convolutionLength = r.convolutionLength;
  e.peakWorkspaceBytes = r.peakWorkspaceBytes;
  e.kernelPath = r.kernelPath;
  e.out.resize(x.size());
  for (Eigen::Index i = 0; i < x.size(); ++i)
    e.out[i] = {r.out[i].real(), r.out[i].imag()};
  return e;
}

inline EigenFftResult ifftEigen(const Eigen::VectorXcd& x,
                                const common::FftOptions& options = {}) {
  std::vector<Cmplx> tmp(x.size());
  for (Eigen::Index i = 0; i < x.size(); ++i)
    tmp[static_cast<std::size_t>(i)] = Cmplx(x[i].real(), x[i].imag());
  auto r = ifft(tmp, options);
  EigenFftResult e;
  e.error = r.error;
  e.convolutionLength = r.convolutionLength;
  e.peakWorkspaceBytes = r.peakWorkspaceBytes;
  e.kernelPath = r.kernelPath;
  e.out.resize(x.size());
  for (Eigen::Index i = 0; i < x.size(); ++i)
    e.out[i] = {r.out[i].real(), r.out[i].imag()};
  return e;
}

} // namespace fft::mathcore
