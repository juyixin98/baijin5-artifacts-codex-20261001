// SPDX-License-Identifier: MIT
// 数值错误契约：所有可失败的内核操作均返回 Result<T>，
// 错误码归属于四个可区分的类别（输入错误 / 状态冲突 / 资源耗尽 / 计算失败）。
#pragma once

#include <cstddef>
#include <string>
#include <type_traits>
#include <utility>

namespace gaussleg {

// 稳定错误码：日志、测试、CLI 退出码都依赖这些符号，不要随意改值。
enum class ErrorCode : int {
    kOk = 0,

    // ---- 输入错误 (invalid input) ----
    kOrderNotPositive = 101,   // n <= 0
    kOrderTooLarge = 102,      // n > 硬上限（资源保护）
    kInvalidInterval = 103,    // a/b 非有限或 a >= b
    kInvalidOption = 104,      // 迭代次数/容差等选项非法

    // ---- 高阶精度上限 (precision ceiling，计算失败的细分) ----
    kAbovePrecisionCeiling = 201, // n > 精度上限且未显式允许

    // ---- 状态冲突 (builder state conflict) ----
    kBuilderNotConfigured = 301, // 未 setOrder 即 build
    kBuilderFinalized = 302,     // Built 后再 set*
    kRuleConsumed = 303,         // 已取走结果后再次取走

    // ---- 资源耗尽 (resource exhaustion) ----
    kAllocationFailed = 401,  // Eigen/标准库分配失败
    kResourceLimit = 402,     // 超过受保护的规模/迭代资源上限

    // ---- 计算失败 (numerical failure) ----
    kRootNotConverged = 501, // Newton 迭代未收敛
    kResidualTooLarge = 502, // 残差/权重校验不过：根不可信
    kSanityCheckFailed = 503 // 对称性/正性/权重和完整性检查失败
};

enum class ErrorCategory {
    kNone,
    kInvalidInput,
    kStateConflict,
    kResourceExhaustion,
    kComputationFailure
};

ErrorCategory categoryOf(ErrorCode code) noexcept;
const char* name(ErrorCode code) noexcept;
const char* name(ErrorCategory cat) noexcept;

// 附带现场上下文的错误：出错节点索引、阶数、迭代次数、残差等。
struct Error {
    ErrorCode code{ErrorCode::kOk};
    std::string message;
    int order{-1};
    int rootIndex{-1};
    int iterations{-1};
    double residual{0.0};
    double tolerance{0.0};

    Error() = default;
    Error(ErrorCode c, std::string msg) : code(c), message(std::move(msg)) {}
};

// 值类型结果
template <typename T>
class Result {
public:
    Result() : value_{}, ok_(false) {}
    Result(const T& value) : value_(value), ok_(true) {}
    Result(T&& value) : value_(std::move(value)), ok_(true) {}
    Result(const Error& err) : error_(err), ok_(false) {}
    Result(Error&& err) : error_(std::move(err)), ok_(false) {}

    bool ok() const noexcept { return ok_; }
    explicit operator bool() const noexcept { return ok_; }

    T& value() & { return value_; }
    const T& value() const& { return value_; }
    T&& value() && { return std::move(value_); }

    const Error& error() const& { return error_; }

private:
    T value_{};
    Error error_{};
    bool ok_{false};
};

// 左值引用结果（如 Result<const Rule&>）：不持有对象
template <typename T>
class Result<T&> {
public:
    Result(T& value) : ptr_(&value), ok_(true) {}
    Result(const Error& err) : error_(err), ok_(false) {}
    Result(Error&& err) : error_(std::move(err)), ok_(false) {}

    bool ok() const noexcept { return ok_; }
    explicit operator bool() const noexcept { return ok_; }

    T& value() const { return *ptr_; }
    const Error& error() const& { return error_; }

private:
    T* ptr_{nullptr};
    Error error_{};
    bool ok_{false};
};

// void 结果
template <>
class Result<void> {
public:
    Result() : ok_(true) {}
    Result(const Error& err) : error_(err), ok_(false) {}
    Result(Error&& err) : error_(std::move(err)), ok_(false) {}

    bool ok() const noexcept { return ok_; }
    explicit operator bool() const noexcept { return ok_; }
    const Error& error() const& { return error_; }

private:
    Error error_{};
    bool ok_{false};
};

} // namespace gaussleg
