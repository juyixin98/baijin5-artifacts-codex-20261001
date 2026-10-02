//! Required scenario 3: sync against a failing backing store.
//!
//! Verifies: a failed sync reports the `SyncFailed` category, failed pages
//! keep their dirty marks (successful pages in the same call are cleared),
//! a retry persists the remaining pages, a flush failure is also reported,
//! and private mappings never touch the store on sync.

mod common;

use common::{page_of, TestRun, PAGE};
use mmap_model::config::ModelConfig;
use mmap_model::error::ErrorCategory;
use mmap_model::model::{MapKind, Vm};
use mmap_model::store::{BackingStore, FaultyStore, MemStore};

const LEN: u64 = 2 * PAGE;

fn faulty_vm() -> (Vm<FaultyStore<MemStore>>, FaultyStore<MemStore>) {
    let cfg = ModelConfig {
        page_size: PAGE,
        max_file_size: 1 << 20,
        max_mappings: 64,
    };
    let store = FaultyStore::new(MemStore::new());
    let handle = store.clone(); // arm faults while the model owns a copy
    (Vm::new(cfg, store), handle)
}

#[test]
fn failed_sync_keeps_dirty_marks() {
    let mut t = TestRun::new("sync_failure");
    let (mut vm, faults) = faulty_vm();

    t.step(
        "setup",
        "file f: 2 pages; shared map; page0<-0x11 page1<-0x22",
    );
    vm.create_file("f", LEN).unwrap();
    let m = vm.map("f", 0, LEN, MapKind::Shared).unwrap();
    vm.write(m, 0, &page_of(0x11)).unwrap();
    vm.write(m, PAGE, &page_of(0x22)).unwrap();
    t.check(
        "dirty before sync",
        &vec![0, 1],
        &vm.file_state("f").unwrap().dirty_pages,
    );

    // -- one page write fails ------------------------------------------------
    t.step(
        "sync",
        "arm 1 write failure: page0 writeback fails, page1 succeeds",
    );
    faults.arm_write_failures(1);
    let err = vm.sync(m).unwrap_err();
    t.check("error category", &ErrorCategory::SyncFailed, &err.category);
    t.check("failed pages", &vec![0], &err.failed_pages);
    t.check(
        "failed page keeps dirty mark",
        &vec![0],
        &vm.file_state("f").unwrap().dirty_pages,
    );
    t.check(
        "page1 was persisted despite sibling failure",
        &page_of(0x22),
        &vm.store_image("f", PAGE, PAGE as usize).unwrap(),
    );
    t.check(
        "page0 not yet in store",
        &page_of(0x00),
        &vm.store_image("f", 0, PAGE as usize).unwrap(),
    );

    // -- retry succeeds --------------------------------------------------------
    t.step(
        "sync",
        "retry with healthy store persists the remaining page",
    );
    let out = vm.sync(m).unwrap();
    t.check("retry persisted", &vec![0], &out.persisted);
    t.check(
        "dirty empty",
        &Vec::<u64>::new(),
        &vm.file_state("f").unwrap().dirty_pages,
    );
    t.check(
        "page0 now in store",
        &page_of(0x11),
        &vm.store_image("f", 0, PAGE as usize).unwrap(),
    );

    // -- flush failure is also a sync failure -----------------------------------
    t.step(
        "sync",
        "arm 1 flush failure: pages written but durability barrier fails",
    );
    vm.write(m, 0, &page_of(0x33)).unwrap();
    faults.arm_flush_failures(1);
    let err = vm.sync(m).unwrap_err();
    t.check(
        "flush failure category",
        &ErrorCategory::SyncFailed,
        &err.category,
    );
    t.check("sync failure counter", &2, &vm.stats().sync_failures);
    let out = vm.sync(m).unwrap(); // nothing dirty left; flush now healthy
    t.check(
        "final sync persists nothing",
        &Vec::<u64>::new(),
        &out.persisted,
    );

    // -- private sync never touches the store ------------------------------------
    t.step(
        "sync",
        "private mapping: COW a page, arm failures, sync is a no-op",
    );
    let p = vm.map("f", 0, LEN, MapKind::Private).unwrap();
    vm.write(p, 0, &page_of(0x99)).unwrap(); // COW page0
    faults.arm_write_failures(1);
    faults.arm_flush_failures(1);
    let out = vm.sync(p).unwrap(); // must not fail: no store interaction
    t.check("private sync persisted", &Vec::<u64>::new(), &out.persisted);
    t.check("private sync skipped", &vec![0], &out.skipped_private);
    t.check(
        "private bytes never reached the store",
        &page_of(0x33),
        &vm.store_image("f", 0, PAGE as usize).unwrap(),
    );
    t.check("store size unchanged", &LEN, &vm.store().size("f").unwrap());
    t.step("done", "sync failure semantics verified");
}
