//! Unmap semantics: shared dirty pages survive in the cache (a later
//! mapping can still sync them), private COW pages are discarded.

mod common;

use common::{page_of, test_vm, TestRun, PAGE};
use mmap_model::model::MapKind;

const LEN: u64 = 2 * PAGE;

#[test]
fn unmap_shared_keeps_cache_private_drops_cow() {
    let mut t = TestRun::new("unmap_semantics");
    let mut vm = test_vm();

    t.step(
        "setup",
        "file f: 2 pages; shared map A writes page0; private map B writes page1",
    );
    vm.create_file("f", LEN).unwrap();
    let a = vm.map("f", 0, LEN, MapKind::Shared).unwrap();
    vm.write(a, 0, &page_of(0xAA)).unwrap();
    let b = vm.map("f", 0, LEN, MapKind::Private).unwrap();
    vm.write(b, PAGE, &page_of(0xBB)).unwrap();

    t.step(
        "unmap",
        "unmap A: dirty shared page stays resident in the cache",
    );
    let rep = vm.unmap(a).unwrap();
    t.check(
        "A dirty left in cache",
        &vec![0],
        &rep.dirty_pages_left_in_cache,
    );
    t.check(
        "A discarded private",
        &Vec::<u64>::new(),
        &rep.discarded_private_pages,
    );
    t.check(
        "cache still dirty",
        &vec![0],
        &vm.file_state("f").unwrap().dirty_pages,
    );

    t.step(
        "remap",
        "new shared mapping sees the cached page and can sync it",
    );
    let a2 = vm.map("f", 0, LEN, MapKind::Shared).unwrap();
    t.check(
        "page0 from cache",
        &page_of(0xAA),
        &vm.read(a2, 0, PAGE as usize).unwrap(),
    );
    let out = vm.sync(a2).unwrap();
    t.check("synced after remap", &vec![0], &out.persisted);
    t.check(
        "persisted image",
        &page_of(0xAA),
        &vm.store_image("f", 0, PAGE as usize).unwrap(),
    );

    t.step("unmap", "unmap B: private COW page is discarded");
    let rep = vm.unmap(b).unwrap();
    t.check(
        "B discarded private",
        &vec![1],
        &rep.discarded_private_pages,
    );

    t.step(
        "remap",
        "new private mapping sees underlying content, not the old COW",
    );
    let b2 = vm.map("f", 0, LEN, MapKind::Private).unwrap();
    t.check(
        "page1 back to file content",
        &page_of(0x00),
        &vm.read(b2, PAGE, PAGE as usize).unwrap(),
    );
    t.check(
        "no private pages",
        &Vec::<u64>::new(),
        &vm.mapping_state(b2).unwrap().private_pages,
    );
    t.check("unmap counter", &2, &vm.stats().unmaps);
    t.step("done", "unmap semantics verified");
}
