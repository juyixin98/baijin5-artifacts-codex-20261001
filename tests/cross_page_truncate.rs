//! Required scenario 2: truncation across page boundaries.
//!
//! Verifies: pages wholly beyond the new EOF are discarded and any access
//! to them fails with `AccessOutOfRange` (never fabricated zeros); the tail
//! of the new last partial page is zeroed; beyond-EOF tail writes stay
//! cache-resident and become visible after a grow; dirty set and
//! persistent image track every step.

mod common;

use common::{page_of, test_vm, TestRun, PAGE};
use mmap_model::error::ErrorCategory;
use mmap_model::model::{DiscardedPage, MapKind};
use mmap_model::store::BackingStore;

const LEN: u64 = 4 * PAGE;

#[test]
fn cross_page_truncate_rules() {
    let mut t = TestRun::new("cross_page_truncate");
    let mut vm = test_vm();

    t.step(
        "setup",
        "file f: 4 pages; shared map; page i <- 0x30+i (all dirty)",
    );
    vm.create_file("f", LEN).unwrap();
    let m = vm.map("f", 0, LEN, MapKind::Shared).unwrap();
    for i in 0..4u8 {
        vm.write(m, i as u64 * PAGE, &page_of(0x30 + i)).unwrap();
    }
    t.check(
        "dirty after writes",
        &vec![0, 1, 2, 3],
        &vm.file_state("f").unwrap().dirty_pages,
    );

    // -- shrink to 2.5 pages ------------------------------------------------
    t.step("truncate", "shrink 2048 -> 1280 (2.5 pages)");
    let rep = vm.truncate("f", 2 * PAGE + PAGE / 2).unwrap();
    t.check("old_size", &LEN, &rep.old_size);
    t.check(
        "discarded pages",
        &vec![DiscardedPage {
            index: 3,
            was_dirty: true,
        }],
        &rep.discarded_pages,
    );
    t.check("zeroed tail page", &Some(2), &rep.zeroed_tail_page);
    t.check(
        "dirty after shrink",
        &vec![0, 1, 2],
        &vm.file_state("f").unwrap().dirty_pages,
    );

    // -- pages beyond EOF: hard error, not zeros -----------------------------
    t.step(
        "verify",
        "access to page 3 (wholly beyond EOF) must fail, not zero-fill",
    );
    let err = vm.read(m, 3 * PAGE, 1).unwrap_err();
    t.check(
        "error category",
        &ErrorCategory::AccessOutOfRange,
        &err.category,
    );
    let err = vm.write(m, 3 * PAGE, &[0xEE]).unwrap_err();
    t.check(
        "write error category",
        &ErrorCategory::AccessOutOfRange,
        &err.category,
    );
    t.check("sigbus counter", &2, &vm.stats().sigbus_errors);

    // -- partial page: valid head keeps data, tail reads as zero --------------
    t.step(
        "verify",
        "page 2 head intact, beyond-EOF tail zeroed by truncate",
    );
    t.check(
        "page2 head",
        &vec![0x32; 256],
        &vm.read(m, 2 * PAGE, 256).unwrap(),
    );
    t.check(
        "page2 tail zeroed",
        &vec![0x00; 256],
        &vm.read(m, 2 * PAGE + 256, 256).unwrap(),
    );

    // -- beyond-EOF tail write: cache-resident, not persisted ------------------
    t.step(
        "write",
        "0x77 into page2 beyond-EOF tail (allowed, not persisted)",
    );
    vm.write(m, 2 * PAGE + 256, &vec![0x77; 256]).unwrap();
    let out = vm.sync(m).unwrap();
    t.check("persisted pages", &vec![0, 1, 2], &out.persisted);

    let mut expected_image = Vec::new();
    expected_image.extend_from_slice(&page_of(0x30));
    expected_image.extend_from_slice(&page_of(0x31));
    expected_image.extend_from_slice(&vec![0x32; 256]); // only the valid half
    t.check(
        "store size",
        &(2 * PAGE + PAGE / 2),
        &vm.store().size("f").unwrap(),
    );
    t.check(
        "persistent image (1280 bytes)",
        &expected_image,
        &vm.store_image("f", 0, expected_image.len()).unwrap(),
    );
    t.check(
        "nothing past EOF in store",
        &Vec::<u8>::new(),
        &vm.store_image("f", 2 * PAGE + PAGE / 2, 256).unwrap(),
    );

    // -- grow back: tail write becomes visible, new pages are legal zeros ------
    t.step("truncate", "grow 1280 -> 2048");
    let rep = vm.truncate("f", LEN).unwrap();
    t.check(
        "grow discards nothing",
        &Vec::<DiscardedPage>::new(),
        &rep.discarded_pages,
    );
    t.check(
        "0x77 tail now within EOF",
        &vec![0x77; 256],
        &vm.read(m, 2 * PAGE + 256, 256).unwrap(),
    );
    t.check(
        "page 3 reads as zeros (sparse store, legal)",
        &page_of(0x00),
        &vm.read(m, 3 * PAGE, PAGE as usize).unwrap(),
    );

    // -- shrink to an exact page boundary --------------------------------------
    t.step("write", "page3 <- 0x88, then shrink to exact boundary 1024");
    vm.write(m, 3 * PAGE, &page_of(0x88)).unwrap();
    let rep = vm.truncate("f", 2 * PAGE).unwrap();
    t.check("no partial tail at boundary", &None, &rep.zeroed_tail_page);
    t.check(
        "discarded at boundary",
        &vec![
            DiscardedPage {
                index: 2,
                was_dirty: false,
            },
            DiscardedPage {
                index: 3,
                was_dirty: true,
            },
        ],
        &rep.discarded_pages,
    );
    let err = vm.read(m, 2 * PAGE, 1).unwrap_err();
    t.check(
        "page 2 now out of range",
        &ErrorCategory::AccessOutOfRange,
        &err.category,
    );

    // -- shrink to zero ---------------------------------------------------------
    t.step("truncate", "shrink to 0: every page becomes inaccessible");
    vm.truncate("f", 0).unwrap();
    let err = vm.read(m, 0, 1).unwrap_err();
    t.check(
        "offset 0 out of range",
        &ErrorCategory::AccessOutOfRange,
        &err.category,
    );
    t.check("file size", &0, &vm.file_state("f").unwrap().size);
    t.step("done", "truncate boundary semantics verified");
}
