# 真实运行结果留存

运行环境：Python 3.12.3，Linux 6.8（x86_64），依赖版本见 `requirements.txt`。
复现命令见 `docs/REPRODUCE.md`。

## 1. 测试套件（`.venv/bin/python -m pytest -v`）

**结果：53 passed, 1 warning（starlette 弃用告警，与业务无关），约 12s。**

```
tests/test_api.py::test_full_flow_over_http PASSED
tests/test_api.py::test_request_id_echoed_and_audited PASSED
tests/test_api.py::test_unknown_message_is_404 PASSED
tests/test_api.py::test_incomplete_stream_error_category PASSED
tests/test_api.py::test_nonce_reuse_conflict_over_http PASSED
tests/test_api.py::test_audit_endpoint_contains_no_plaintext PASSED
tests/test_crash_recovery.py::test_crash_before_release_commit PASSED
tests/test_crash_recovery.py::test_crash_between_chunk_submissions PASSED
tests/test_idempotency.py::test_identical_retry_returns_stored_ciphertext PASSED
tests/test_idempotency.py::test_conflicting_retry_is_nonce_reuse_conflict PASSED
tests/test_idempotency.py::test_same_content_with_different_final_flag_conflicts PASSED
tests/test_idempotency.py::test_conflict_is_audited_with_request_ids PASSED
tests/test_protocol.py::test_aad_golden_encoding PASSED
tests/test_protocol.py::test_aad_final_flag_bit PASSED
tests/test_protocol.py::test_aad_decode_roundtrip PASSED
tests/test_protocol.py::test_aad_rejects_bad_inputs PASSED
tests/test_protocol.py::test_nonce_golden_derivation PASSED
tests/test_protocol.py::test_nonce_distinct_per_seq PASSED
tests/test_protocol.py::test_hkdf_rfc5869_case1 PASSED
tests/test_protocol.py::test_message_key_binds_message_id PASSED
tests/test_protocol.py::test_split_ciphertext PASSED
tests/test_roundtrip.py::test_chunking_does_not_change_plaintext[1] PASSED
tests/test_roundtrip.py::test_chunking_does_not_change_plaintext[3] PASSED
tests/test_roundtrip.py::test_chunking_does_not_change_plaintext[7] PASSED
tests/test_roundtrip.py::test_chunking_does_not_change_plaintext[1024] PASSED
tests/test_roundtrip.py::test_chunking_does_not_change_plaintext[65536] PASSED
tests/test_roundtrip.py::test_single_chunk_message PASSED
tests/test_roundtrip.py::test_empty_message PASSED
tests/test_roundtrip.py::test_out_of_order_submission PASSED
tests/test_roundtrip.py::test_same_plaintext_different_chunkings_both_release PASSED
tests/test_roundtrip.py::test_different_messages_get_different_ciphertext PASSED
tests/test_tamper.py::test_missing_middle_chunk_is_incomplete PASSED
tests/test_tamper.py::test_missing_terminator_is_incomplete PASSED
tests/test_tamper.py::test_truncated_tail_is_incomplete PASSED
tests/test_tamper.py::test_final_flag_on_non_last_chunk PASSED
tests/test_tamper.py::test_two_final_flags_rejected PASSED
tests/test_tamper.py::test_flipped_ciphertext_byte_is_tag_mismatch PASSED
tests/test_tamper.py::test_reordered_staged_chunks_are_detected PASSED
tests/test_tamper.py::test_empty_stream_is_incomplete PASSED
tests/test_tamper.py::test_failed_message_cannot_be_resubmitted PASSED
tests/test_vectors.py::test_core_matches_reference_ciphertext[msg0..4] PASSED (x5)
tests/test_vectors.py::test_independent_verifier_accepts_reference[msg0..4] PASSED (x5)
tests/test_vectors.py::test_key_derivation_matches_fixture PASSED
tests/test_vectors.py::test_both_backends_reject_bit_flipped_tag PASSED
tests/test_vectors.py::test_chunk_bound_to_seq_and_final_flag PASSED

======================== 53 passed, 1 warning in 12.36s ========================
```

## 2. 端到端服务调用（真实 uvicorn 进程 + HTTP 客户端）

命令：`SAE_PORT=8391 python -m sae` + `SAE_URL=http://127.0.0.1:8391 python examples/client_demo.py`

实际输出（message_id 与哈希为当次运行的真实随机值）：

```
[demo] message_id = 2f5bd555367d1cb92a08a64a9849e19c
[demo] staged 2 chunks (out of order)
[demo] premature plaintext read -> 409 message_state_conflict
[demo] finalize -> {'message_id': '2f5bd555367d1cb92a08a64a9849e19c', 'chunks': 2, 'size': 8000, 'plaintext_sha256': '6550d027510864393e84f9990f18ea1c5420365c41f5cf8b2be8d86643a1c3e5'}
[demo] released 8000 bytes, sha256=6550d027510864393e84f9990f18ea1c5420365c41f5cf8b2be8d86643a1c3e5
[demo] plaintext byte-identical to original: OK
[demo] truncated stream finalize -> 409 incomplete_stream
[audit] req=demo-01a79071-create   message_created    accept
[audit] req=demo-01a79071-chunk-1  chunk_accepted     accept
[audit] req=demo-01a79071-chunk-0  chunk_accepted     accept
[audit] req=48818c86efa6447a9421809e2da71efb plaintext_denied   reject
[audit] req=demo-01a79071-fin      finalize_accepted  accept
```

要点：

- 乱序（先 seq1 后 seq0）提交被接受，finalize 后发布字节与原文一致。
- finalize 前读明文被拒（`message_state_conflict`），截尾流被拒
  （`incomplete_stream`），均未输出任何明文。
- 审计记录带请求标识与 accept/reject 决策，只含哈希/长度等脱敏元数据。
