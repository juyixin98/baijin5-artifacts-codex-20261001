#pragma once
#include <cstdint>
#include <fstream>
#include <string>

namespace polyeval::log {

struct Event {
  std::string request_id;
  std::string phase;       // parse | build | descend | crosscheck | response
  std::string location;    // source file/module marker
  std::string detail;      // human-readable key=value summary
  std::string status = "ok";
};

class JsonlLogger {
 public:
  explicit JsonlLogger(const std::string& path);
  bool enabled() const { return out_.is_open(); }
  void emit(const Event& ev);
  void emit(std::string request_id, std::string phase, std::string location,
            std::string detail, std::string status = "ok");
 private:
  std::ofstream out_;
  std::uint64_t seq_ = 0;
};

}  // namespace polyeval::log
