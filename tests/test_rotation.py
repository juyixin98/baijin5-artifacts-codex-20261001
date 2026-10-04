"""索引密钥轮换测试：双版本查询不漏记录、中断安全。"""
import pytest

from blindex.errors import BlindIndexError, Category

from reference import FIXTURES, ref_expected_matches

REQ = "t"


def _assert_all_queries_hit(service, id_map):
    """对夹具全部非空字段值做查询，逐一比对明文参考。"""
    for fx_id, row in FIXTURES.items():
        for field, value in row.items():
            if value is None:
                continue
            result = service.query(field, value, REQ)
            expected = sorted(
                id_map[fx] for fx in ref_expected_matches(FIXTURES, field, value)
            )
            assert result["matched_record_ids"] == expected, (
                f"轮换期间漏记录: {field}={value!r}"
            )
            assert result["uncertain"] == []


class TestRotation:
    def test_dual_version_query_never_misses(self, loaded_service, storage):
        service, id_map = loaded_service
        assert storage.get_query_versions() == [1]

        rot = service.start_index_rotation(REQ)
        assert rot["from_version"] == 1 and rot["to_version"] == 2
        assert rot["query_versions"] == [1, 2]

        # 轮换开始、尚未重建任何索引：旧版本索引仍在，查询必须全中
        _assert_all_queries_hit(service, id_map)

        # 分批重建，每批之后（模拟中断点）都验证不漏记录
        seen_idle = False
        for _ in range(20):
            out = service.reindex_batch(1, REQ)
            _assert_all_queries_hit(service, id_map)
            if out["state"] == "idle":
                seen_idle = True
                break
        assert seen_idle, "重建未在预期批数内完成"

        # 完成后：查询集合收敛、旧版本索引物理删除、结果仍全中
        assert storage.get_query_versions() == [2]
        assert storage.index_versions_present() == {2}
        _assert_all_queries_hit(service, id_map)

    def test_interrupted_rotation_resumes_safely(self, loaded_service, storage):
        """轮换中断（只重建一部分后停手）时，双版本查询仍覆盖全部记录。"""
        service, id_map = loaded_service
        service.start_index_rotation(REQ)
        service.reindex_batch(2, REQ)  # 只重建 2 条就“中断”

        status = service.rotation_status()
        assert status["state"] == "in_progress"
        assert status["query_versions"] == [1, 2]
        assert status["index_versions_present"] == [1, 2]  # 两版本索引并存

        # 中断状态下：新写入走新版本索引，老记录新旧混合，查询均不漏
        new_id = service.create_record({"email": "new@example.test"}, REQ)
        _assert_all_queries_hit(service, id_map)
        result = service.query("email", " NEW@example.TEST ", REQ)
        assert result["matched_record_ids"] == [new_id]
        assert result["searched_index_versions"] == [1, 2]

    def test_rotation_state_machine_rejects_illegal_transitions(
        self, loaded_service, service
    ):
        service, _ = loaded_service
        with pytest.raises(BlindIndexError) as ei:
            service.reindex_batch(1, REQ)  # 未轮换就重建
        assert ei.value.category is Category.ROTATION_STATE_ERROR

        service.start_index_rotation(REQ)
        with pytest.raises(BlindIndexError) as ei:
            service.start_index_rotation(REQ)  # 轮换中再次发起
        assert ei.value.category is Category.ROTATION_STATE_ERROR

    def test_restarted_service_recovers_query_versions(
        self, loaded_service, storage, keyring
    ):
        """进程在轮换中途崩溃重启：查询版本集从索引表实有版本自愈。"""
        from blindex.audit import AuditLog
        from blindex.crypto_adapter import CryptoAdapter
        from blindex.service import BlindIndexService

        service, id_map = loaded_service
        service.start_index_rotation(REQ)
        service.reindex_batch(1, REQ)
        storage.set_meta("index_query_versions", "[2]")  # 模拟崩溃丢状态

        revived = BlindIndexService(storage, CryptoAdapter(keyring), AuditLog(storage))
        assert revived.rotation_status()["query_versions"] == [1, 2]
        _assert_all_queries_hit(revived, id_map)
