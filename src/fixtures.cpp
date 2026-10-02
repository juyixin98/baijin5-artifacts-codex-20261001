#include "chebcore/fixtures.hpp"

#include <array>
#include <cstdint>
#include <iomanip>

namespace chebcore::fixtures {

namespace {

// Minimal FIPS 180-4 SHA-256.
class Sha256 {
 public:
  Sha256() { reset(); }

  void update(const std::uint8_t* data, std::size_t len) {
    for (std::size_t i = 0; i < len; ++i) {
      buffer_[buffer_len_++] = data[i];
      if (buffer_len_ == 64) {
        process(buffer_.data());
        bit_len_ += 512;
        buffer_len_ = 0;
      }
    }
  }

  std::string final_hex() {
    std::uint64_t bits = bit_len_ + static_cast<std::uint64_t>(buffer_len_) * 8;
    std::uint8_t pad = 0x80;
    update(&pad, 1);
    std::uint8_t zero = 0;
    while (buffer_len_ != 56) update(&zero, 1);
    std::uint8_t lenbytes[8];
    for (int i = 7; i >= 0; --i)
      lenbytes[i] = static_cast<std::uint8_t>(bits >> (8 * (7 - i)));
    update(lenbytes, 8);
    std::ostringstream os;
    os << std::hex << std::setfill('0');
    for (std::uint32_t h : state_) os << std::setw(8) << h;
    return os.str();
  }

 private:
  static std::uint32_t rotr(std::uint32_t x, std::uint32_t n) {
    return (x >> n) | (x << (32 - n));
  }
  void reset() {
    state_ = {0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
              0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19};
    buffer_len_ = 0;
    bit_len_ = 0;
  }
  void process(const std::uint8_t* p) {
    static const std::uint32_t k[64] = {
        0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,
        0x923f82a4,0xab1c5ed5,0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,
        0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,0xe49b69c1,0xefbe4786,
        0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
        0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,
        0x06ca6351,0x14292967,0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,
        0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,0xa2bfe8a1,0xa81a664b,
        0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
        0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,
        0x5b9cca4f,0x682e6ff3,0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,
        0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2};
    std::array<std::uint32_t, 64> w{};
    for (int i = 0; i < 16; ++i)
      w[i] = (static_cast<std::uint32_t>(p[4*i]) << 24) |
             (static_cast<std::uint32_t>(p[4*i+1]) << 16) |
             (static_cast<std::uint32_t>(p[4*i+2]) << 8) |
             static_cast<std::uint32_t>(p[4*i+3]);
    for (int i = 16; i < 64; ++i) {
      std::uint32_t s0 = rotr(w[i-15],7) ^ rotr(w[i-15],18) ^ (w[i-15] >> 3);
      std::uint32_t s1 = rotr(w[i-2],17) ^ rotr(w[i-2],19) ^ (w[i-2] >> 10);
      w[i] = w[i-16] + s0 + w[i-7] + s1;
    }
    std::uint32_t a=state_[0],b=state_[1],c=state_[2],d=state_[3];
    std::uint32_t e=state_[4],f=state_[5],g=state_[6],h=state_[7];
    for (int i = 0; i < 64; ++i) {
      std::uint32_t S1 = rotr(e,6) ^ rotr(e,11) ^ rotr(e,25);
      std::uint32_t ch = (e & f) ^ ((~e) & g);
      std::uint32_t t1 = h + S1 + ch + k[i] + w[i];
      std::uint32_t S0 = rotr(a,2) ^ rotr(a,13) ^ rotr(a,22);
      std::uint32_t mj = (a & b) ^ (a & c) ^ (b & c);
      std::uint32_t t2 = S0 + mj;
      h=g; g=f; f=e; e=d+t1; d=c; c=b; b=a; a=t1+t2;
    }
    state_[0]+=a; state_[1]+=b; state_[2]+=c; state_[3]+=d;
    state_[4]+=e; state_[5]+=f; state_[6]+=g; state_[7]+=h;
  }
  std::array<std::uint32_t, 8> state_{};
  std::array<std::uint8_t, 64> buffer_{};
  std::size_t buffer_len_{0};
  std::uint64_t bit_len_{0};
};

}  // namespace

Columns load_csv(const std::string& path, char delimiter) {
  std::ifstream in(path);
  if (!in) throw std::runtime_error("fixtures: cannot open " + path);
  Columns cols;
  std::string line;
  // Skip leading comment lines, then treat the next line as the header.
  while (std::getline(in, line))
    if (!line.empty() && line[0] != '#') break;
  {
    std::stringstream hs(line);
    std::string tok;
    while (std::getline(hs, tok, delimiter)) cols.header.push_back(tok);
  }
  cols.data.assign(cols.header.size(), {});
  cols.trailing_labels.assign(1, std::string{});  // index 0 = header marker
  const std::size_t ncol = cols.header.size();
  while (std::getline(in, line)) {
    if (line.empty() || line[0] == '#') continue;
    std::vector<std::string> fields;
    {
      // Minimal RFC4180-ish split: a quoted field may contain the delimiter.
      std::string field;
      bool in_quotes = false;
      for (std::size_t i = 0; i < line.size(); ++i) {
        char ch = line[i];
        if (ch == '"') {
          in_quotes = !in_quotes;
        } else if (ch == delimiter && !in_quotes) {
          fields.push_back(field);
          field.clear();
        } else {
          field.push_back(ch);
        }
      }
      fields.push_back(field);
    }
    if (fields.size() != ncol)
      throw std::runtime_error("fixtures: ragged row in " + path);
    std::string label;
    for (std::size_t i = 0; i < ncol; ++i) {
      const std::string& tok = fields[i];
      std::size_t used = 0;
      double v = 0.0;
      bool numeric = true;
      try {
        v = std::stod(tok, &used);
        while (used < tok.size() &&
               std::isspace(static_cast<unsigned char>(tok[used])))
          ++used;
        if (used != tok.size()) numeric = false;
      } catch (const std::exception&) {
        numeric = false;
      }
      if (numeric) {
        cols.data[i].push_back(v);
      } else {
        // Only a trailing label column is allowed; embedded non-numeric
        // fields are a hard error (keeps numeric column alignment honest).
        if (i + 1 != ncol)
          throw std::runtime_error("fixtures: non-numeric value '" + tok +
                                   "' in non-trailing column of " + path);
        label = tok;
      }
    }
    cols.trailing_labels.push_back(label);
  }
  return cols;
}

std::string sha256_file(const std::string& path) {
  std::ifstream in(path, std::ios::binary);
  if (!in) throw std::runtime_error("fixtures: cannot open " + path);
  Sha256 h;
  std::array<char, 4096> buf{};
  while (in) {
    in.read(buf.data(), buf.size());
    h.update(reinterpret_cast<const std::uint8_t*>(buf.data()),
             static_cast<std::size_t>(in.gcount()));
  }
  return h.final_hex();
}

Manifest load_manifest(const std::string& manifest_path) {
  std::ifstream in(manifest_path);
  if (!in) throw std::runtime_error("fixtures: cannot open manifest " + manifest_path);
  Manifest m;
  std::string line;
  while (std::getline(in, line)) {
    if (line.empty() || line[0] == '#') continue;
    std::size_t sp = line.find("  ");
    if (sp == std::string::npos)
      throw std::runtime_error("fixtures: bad manifest line: " + line);
    m[line.substr(sp + 2)] = line.substr(0, sp);
  }
  return m;
}

void verify_manifest(const std::string& base_dir, const Manifest& m) {
  for (const auto& [rel, want] : m) {
    const std::string full = base_dir + "/" + rel;
    const std::string got = sha256_file(full);
    if (got != want)
      throw std::runtime_error(
          "fixtures: SHA-256 mismatch for " + rel + " (expected " + want +
          ", got " + got + ")");
  }
}

}  // namespace chebcore::fixtures
