//! Required scenario 1: two mappings (one shared, one private) plus a
//! shared sub-range mapping, interleaving writes across pages.
//!
//! Verifies: COW separation (private writes invisible to shared mappings
//! and never persisted), shared visibility between mappings, the dirty
//! page set, and the persistent image after sync.
//!
//! Oracles are plain `Vec<u8>` computations in this file — independent of
//! the model under test.

mod common;

use common::{oracle_fill, page_of, test_vm, TestRun, PAGE};
use mmap_model::model::MapKind;

const LEN: u64 = 4 * PAGE; // 4 pages

/// Initial file image: page i filled with 0xA0+i.
fn initial_image() -> Vec<u8> {
    let mut v = Vec::new();
    for i in 0..4u8 {
        v.extend_from_slice(&page_of(0xA0 + i));
    }
    v
}

#[test]
fn interleaved_shared_and_private_writes() {
    let mut t = TestRun::new("interleaved_writes");
    let mut vm = test_vm();

    // -- setup: file with known initial content, three mappings ----------
    t.step(
        "setup",
        "file f: 4 pages, page i = 0xA0+i; A=shared[0,4p) B=private[0,4p) C=shared[1p,2p)",
    );
    vm.create_file("f", LEN).unwrap();
    let init = initial_image();
    vm.write_file_direct("f", 0, &init).unwrap();
    let a = vm.map("f", 0, LEN, MapKind::Shared).unwrap();
    let b = vm.map("f", 0, LEN, MapKind::Private).unwrap();
    let c = vm.map("f", PAGE, 2 * PAGE, MapKind::Shared).unwrap();

    // Oracles, computed independently of the model.
    let mut shared_view = init.clone(); // what A and C must see
    let mut b_view = init.clone(); // what B must see

    // -- interleaved write schedule --------------------------------------
    t.step("write", "A: page0 <- 0x11 (shared)");
    vm.write(a, 0, &page_of(0x11)).unwrap();
    oracle_fill(&mut shared_view, 0, 0x11, PAGE as usize);
    oracle_fill(&mut b_view, 0, 0x11, PAGE as usize); // B has no COW on page0

    t.step("write", "B: page1 <- 0x22 (private, triggers COW)");
    vm.write(b, PAGE, &page_of(0x22)).unwrap();
    oracle_fill(&mut b_view, PAGE as usize, 0x22, PAGE as usize);
    // shared_view untouched: private write must not leak

    t.step("write", "A: page1 <- 0x33 (shared; B must NOT see this)");
    vm.write(a, PAGE, &page_of(0x33)).unwrap();
    oracle_fill(&mut shared_view, PAGE as usize, 0x33, PAGE as usize);
    // b_view untouched: B holds a COW copy of page1

    t.step("write", "B: page2 <- 0x44 (private, COW)");
    vm.write(b, 2 * PAGE, &page_of(0x44)).unwrap();
    oracle_fill(&mut b_view, 2 * PAGE as usize, 0x44, PAGE as usize);

    t.step(
        "write",
        "C: relative 0..128 (file page1 head) <- 0x55 (shared sub-range)",
    );
    vm.write(c, 0, &[0x55; 128]).unwrap();
    oracle_fill(&mut shared_view, PAGE as usize, 0x55, 128);

    t.step("write", "A: page3 <- 0x66 (shared)");
    vm.write(a, 3 * PAGE, &page_of(0x66)).unwrap();
    oracle_fill(&mut shared_view, 3 * PAGE as usize, 0x66, PAGE as usize);
    oracle_fill(&mut b_view, 3 * PAGE as usize, 0x66, PAGE as usize);

    // -- COW separation and shared visibility -----------------------------
    t.step("verify", "A and C see the shared view; B sees its COW view");
    t.check(
        "A full read",
        &shared_view,
        &vm.read(a, 0, LEN as usize).unwrap(),
    );
    t.check(
        "B full read",
        &b_view,
        &vm.read(b, 0, LEN as usize).unwrap(),
    );
    t.check(
        "C sub-range read",
        &shared_view[PAGE as usize..3 * PAGE as usize],
        &vm.read(c, 0, (2 * PAGE) as usize).unwrap(),
    );

    // -- dirty page set -----------------------------------------------------
    t.step(
        "verify",
        "dirty set = shared-written pages {0,1,3}; B COW pages {1,2}",
    );
    let fs = vm.file_state("f").unwrap();
    t.check("dirty pages", &vec![0, 1, 3], &fs.dirty_pages);
    let ms = vm.mapping_state(b).unwrap();
    t.check("B private pages", &vec![1, 2], &ms.private_pages);
    t.check("cow fault count", &2, &vm.stats().cow_faults);

    // -- private sync is a no-op for the file -------------------------------
    t.step(
        "sync",
        "sync(B): private pages reported, nothing written back",
    );
    let out = vm.sync(b).unwrap();
    t.check("B sync persisted", &Vec::<u64>::new(), &out.persisted);
    t.check("B sync skipped_private", &vec![1, 2], &out.skipped_private);
    t.check(
        "store image unchanged by private sync",
        &init,
        &vm.store_image("f", 0, LEN as usize).unwrap(),
    );

    // -- shared sync persists exactly the shared view -----------------------
    t.step("sync", "sync(A): dirty shared pages written back");
    let out = vm.sync(a).unwrap();
    t.check("A sync persisted", &vec![0, 1, 3], &out.persisted);
    t.check(
        "persistent image == shared oracle",
        &shared_view,
        &vm.store_image("f", 0, LEN as usize).unwrap(),
    );
    t.check(
        "dirty set empty after sync",
        &Vec::<u64>::new(),
        &vm.file_state("f").unwrap().dirty_pages,
    );

    // Persistent image must contain none of B's private bytes.
    let image = vm.store_image("f", 0, LEN as usize).unwrap();
    t.check("no 0x22 leaked to disk", &false, &image.contains(&0x22));
    t.check("no 0x44 leaked to disk", &false, &image.contains(&0x44));
    t.check("sync failures", &0, &vm.stats().sync_failures);
    t.step(
        "done",
        "COW separation, dirty set and persistent image verified",
    );
}
