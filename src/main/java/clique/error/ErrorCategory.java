package clique.error;

/**
 * 可区分的失败类别：
 * INPUT_ERROR        输入契约违规（自环、越界顶点、非对称邻接、非法配置等）
 * STATE_CONFLICT     续扫状态与当前图/运行不一致，或状态本身越界
 * RESOURCE_EXHAUSTED 资源预算（递归步数）耗尽，携带可续扫的部分结果
 * COMPUTATION_FAILED 内部不变式被破坏（重复输出、非极大输出、证书不一致等）
 */
public enum ErrorCategory {
    INPUT_ERROR,
    STATE_CONFLICT,
    RESOURCE_EXHAUSTED,
    COMPUTATION_FAILED
}
