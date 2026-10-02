//! 文件系统持久化：把 [`LabSnapshot`] 以 JSON 原子写入本地文件。
//!
//! 采用“临时文件 + fsync + rename”的原子替换：写入中途崩溃不会留下半截快照。
//! 不涉及任何真实内核页表——读写的只是本教学服务自己的合成状态文件。

use crate::error::{DomainError, DomainResult};
use crate::store::{Lab, LabSnapshot};
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};

/// 持久化结果。
#[derive(Debug, Clone, serde::Serialize)]
pub struct PersistInfo {
    pub run_id: String,
    pub path: String,
    pub bytes: u64,
    pub spaces: usize,
    pub mappings: usize,
}

impl Lab {
    /// 原子保存快照到 `path`。
    pub fn save_to_file(&mut self, path: &str) -> DomainResult<PersistInfo> {
        let snap = self.snapshot();
        let json = serde_json::to_string_pretty(&snap)
            .map_err(|e| DomainError::Computation(format!("快照序列化失败：{e}")))?;

        let target = PathBuf::from(path);
        if let Some(parent) = target.parent() {
            if !parent.as_os_str().is_empty() {
                fs::create_dir_all(parent)
                    .map_err(|e| DomainError::Input(format!("无法创建快照目录 {parent:?}：{e}")))?;
            }
        }
        let tmp = tmp_path(&target);
        {
            let mut f = fs::File::create(&tmp)
                .map_err(|e| DomainError::Input(format!("无法创建临时文件 {tmp:?}：{e}")))?;
            f.write_all(json.as_bytes())
                .map_err(|e| DomainError::Computation(format!("写快照失败：{e}")))?;
            f.sync_all()
                .map_err(|e| DomainError::Computation(format!("fsync 快照失败：{e}")))?;
        }
        fs::rename(&tmp, &target)
            .map_err(|e| DomainError::Computation(format!("原子替换快照失败：{e}")))?;

        let run_id = self.events_mut().record(
            crate::events::EventKind::Persist,
            format!("快照已写入 {path}（{} 字节）", json.len()),
            vec!["临时文件 + fsync + rename 原子替换".into()],
            serde_json::json!({
                "path": path, "bytes": json.len(),
                "spaces": snap.spaces.len(), "mappings": snap.mappings.len(),
            }),
            None,
        );
        Ok(PersistInfo {
            run_id,
            path: path.into(),
            bytes: json.len() as u64,
            spaces: snap.spaces.len(),
            mappings: snap.mappings.len(),
        })
    }

    /// 从快照文件恢复。
    pub fn load_from_file(path: &str) -> DomainResult<Lab> {
        let json = fs::read_to_string(path)
            .map_err(|e| DomainError::NotFound(format!("读取快照 {path} 失败：{e}")))?;
        let snap: LabSnapshot = serde_json::from_str(&json)
            .map_err(|e| DomainError::Input(format!("快照 {path} 解析失败：{e}")))?;
        Lab::restore(snap)
    }
}

fn tmp_path(target: &Path) -> PathBuf {
    let mut name = target
        .file_name()
        .map(|s| s.to_os_string())
        .unwrap_or_default();
    name.push(".tmp");
    target.with_file_name(name)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::Config;
    use crate::types::{AccessKind, MapRequest, PageSize, Permissions, TranslateRequest};

    fn temp_path(tag: &str) -> String {
        let pid = std::process::id();
        format!("/tmp/mmu-lab-test-{pid}-{tag}.json")
    }

    #[test]
    fn snapshot_roundtrip_preserves_translation() {
        let mut lab = Lab::new(Config::default());
        let (a1, _) = lab.create_asid(Some("p1".into())).unwrap();
        lab.map(&MapRequest {
            asid: a1,
            va: 0x1000,
            pa: None,
            page: PageSize::Small,
            permissions: Permissions::rwx(),
            global: false,
            invalidate: true,
        })
        .unwrap();

        let path = temp_path("roundtrip");
        lab.save_to_file(&path).unwrap();
        let mut restored = Lab::load_from_file(&path).unwrap();

        let out = restored
            .translate(&TranslateRequest {
                asid: a1,
                va: 0x1234,
                access: AccessKind::Read,
                len: 1,
                fetch_pte: false,
            })
            .unwrap();
        match out {
            crate::store::translate::TranslateOutcome::Ok(r) => {
                assert_eq!(r.pa, 0x1234, "恢复后翻译物理地址应保持一致");
            }
            other => panic!("期望恢复后翻译成功，实际 {other:?}"),
        }
        let _ = fs::remove_file(&path);
    }

    #[test]
    fn rejects_bad_version_or_garbage() {
        let path = temp_path("garbage");
        fs::write(&path, b"{not json").unwrap();
        assert!(matches!(
            Lab::load_from_file(&path),
            Err(DomainError::Input(_))
        ));
        let _ = fs::remove_file(&path);
    }
}
