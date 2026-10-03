#pragma once
// Local binary wire format for complex vectors:
//   magic "CFT1" (4 bytes) | uint64_t LE length | N * (float64 LE re, im)
// Little endian doubles are encoded/decoded explicitly for portability.
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <complex>
#include <string>
#include <vector>

namespace fft::service {

inline void putU64LE(std::uint64_t v, std::FILE* f) {
  unsigned char b[8];
  for (int i = 0; i < 8; ++i) b[i] = static_cast<unsigned char>(v >> (8 * i));
  std::fwrite(b, 1, 8, f);
}
inline std::uint64_t getU64LE(const unsigned char* b) {
  std::uint64_t v = 0;
  for (int i = 0; i < 8; ++i) v |= std::uint64_t(b[i]) << (8 * i);
  return v;
}
inline void putF64LE(double d, std::FILE* f) {
  std::uint64_t v;
  std::memcpy(&v, &d, 8);
  putU64LE(v, f);
}
inline double getF64LE(const unsigned char* b) {
  std::uint64_t v = getU64LE(b);
  double d;
  std::memcpy(&d, &v, 8);
  return d;
}

template <class C>
inline bool writeVector(const std::vector<C>& v, const std::string& path,
                        std::string& err) {
  std::FILE* f = std::fopen(path.c_str(), "wb");
  if (!f) { err = "cannot open output file: " + path; return false; }
  const char magic[4] = {'C', 'F', 'T', '1'};
  std::fwrite(magic, 1, 4, f);
  putU64LE(static_cast<std::uint64_t>(v.size()), f);
  for (const auto& z : v) {
    putF64LE(static_cast<double>(z.real()), f);
    putF64LE(static_cast<double>(z.imag()), f);
  }
  bool ok = std::ferror(f) == 0;
  std::fclose(f);
  if (!ok) err = "write error on " + path;
  return ok;
}

inline bool readVector(std::vector<std::complex<double>>& v,
                       const std::string& path, std::string& err) {
  std::FILE* f = std::fopen(path.c_str(), "rb");
  if (!f) { err = "cannot open input file: " + path; return false; }
  char magic[4];
  if (std::fread(magic, 1, 4, f) != 4 ||
      std::memcmp(magic, "CFT1", 4) != 0) {
    err = "bad magic (expected CFT1): " + path;
    std::fclose(f);
    return false;
  }
  unsigned char lb[8];
  if (std::fread(lb, 1, 8, f) != 8) {
    err = "truncated length field";
    std::fclose(f);
    return false;
  }
  std::uint64_t n = getU64LE(lb);
  if (n > (1ULL << 40)) {
    err = "length implausibly large";
    std::fclose(f);
    return false;
  }
  v.resize(static_cast<std::size_t>(n));
  for (std::uint64_t i = 0; i < n; ++i) {
    unsigned char buf[16];
    if (std::fread(buf, 1, 16, f) != 16) {
      err = "truncated sample data";
      std::fclose(f);
      return false;
    }
    v[i] = {getF64LE(buf), getF64LE(buf + 8)};
  }
  std::fclose(f);
  return true;
}

} // namespace fft::service
