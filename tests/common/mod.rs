//! 测试公共夹具。
//!
//! `Oracle` 是一个**独立参考实现**：它不调用被测代码的任何遍历/翻译逻辑，
//! 只依据测试声明的“(虚拟起始, 大小, 物理帧号)”地面真值做区间查找，
//! 独立复算期望物理地址，用于核验 [`mmu_teach`] 输出是否正确。
//! 这样“参考答案”不是由被测核心实现自身生成的。

#![allow(dead_code)]

use mmu_teach::config::PAGE_SIZE;
use mmu_teach::pte::Permissions;

/// 一条地面真值映射（与如何建立映射无关，纯区间声明）。
#[derive(Debug, Clone, Copy)]
pub struct OracleMapping {
    pub va: u64,
    pub size: u64,
    pub ppn: u64,
    pub perms: Permissions,
}

/// 独立预言机：线性区间查找，返回期望 (物理地址, 权限)。
pub struct Oracle<'a> {
    maps: &'a [OracleMapping],
}

impl<'a> Oracle<'a> {
    pub fn new(maps: &'a [OracleMapping]) -> Self {
        Oracle { maps }
    }

    pub fn translate(&self, vaddr: u64) -> Option<(u64, Permissions)> {
        for m in self.maps {
            if vaddr >= m.va && vaddr < m.va + m.size {
                let paddr = m.ppn * PAGE_SIZE + (vaddr - m.va);
                return Some((paddr, m.perms));
            }
        }
        None
    }
}

/// 断言两个权限集合逐位一致。
pub fn assert_perms_eq(actual: &Permissions, expected: &Permissions, ctx: &str) {
    assert_eq!(actual.read, expected.read, "{ctx}: read 位不一致");
    assert_eq!(actual.write, expected.write, "{ctx}: write 位不一致");
    assert_eq!(actual.execute, expected.execute, "{ctx}: execute 位不一致");
    assert_eq!(actual.user, expected.user, "{ctx}: user 位不一致");
    assert_eq!(actual.global, expected.global, "{ctx}: global 位不一致");
}
