//! k-way merge of sorted sources (one resident `Vec` plus zero or more spilled
//! run files). The merge heap holds exactly one head entry per source, so merge
//! memory scales with the number of runs, not the data volume.

use std::cmp::Ordering;
use std::collections::BinaryHeap;
use std::path::Path;

use crate::error::Result;
use crate::exec::codec::{Entry, RunReader};
use crate::resources::CancellationToken;

/// A sorted sequence of entries.
pub trait SortedSource {
    fn next_entry(&mut self) -> Result<Option<Entry>>;
}

/// Resident sorted vector.
pub struct VecSource {
    iter: std::vec::IntoIter<Entry>,
}

impl VecSource {
    pub fn new(sorted: Vec<Entry>) -> Self {
        Self {
            iter: sorted.into_iter(),
        }
    }
}

impl SortedSource for VecSource {
    fn next_entry(&mut self) -> Result<Option<Entry>> {
        Ok(self.iter.next())
    }
}

/// Spilled run file; readers are opened lazily by [`Merger`].
pub struct FileSource {
    reader: RunReader<std::fs::File>,
}

impl FileSource {
    pub fn open(path: &Path) -> Result<Self> {
        Ok(Self {
            reader: RunReader::open(path)?,
        })
    }
}

impl SortedSource for FileSource {
    fn next_entry(&mut self) -> Result<Option<Entry>> {
        self.reader.next_entry()
    }
}

struct HeapEntry {
    entry: Entry,
    src: usize,
    ascending: bool,
}

impl PartialEq for HeapEntry {
    fn eq(&self, other: &Self) -> bool {
        self.cmp(other) == Ordering::Equal
    }
}
impl Eq for HeapEntry {}

impl PartialOrd for HeapEntry {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

impl Ord for HeapEntry {
    fn cmp(&self, other: &Self) -> Ordering {
        // BinaryHeap is a max-heap; reverse so the smallest sorted element is
        // popped first. Tie on (ordinal, source) makes the heap order total.
        let key_ord = self.entry.compare(&other.entry, self.ascending).reverse();
        key_ord.then_with(|| other.src.cmp(&self.src))
    }
}

/// Pull-based k-way merger.
pub struct Merger {
    sources: Vec<Box<dyn SortedSource>>,
    heap: BinaryHeap<HeapEntry>,
    ascending: bool,
}

impl Merger {
    /// Build a merger. Every source must already be sorted ascending by key
    /// with stable ordinals; `ascending=false` reverses only the key direction.
    pub fn new(
        mut sources: Vec<Box<dyn SortedSource>>,
        ascending: bool,
        cancel: &CancellationToken,
    ) -> Result<Self> {
        let mut heap = BinaryHeap::with_capacity(sources.len());
        for (src, source) in sources.iter_mut().enumerate() {
            cancel.check()?;
            if let Some(entry) = source.next_entry()? {
                heap.push(HeapEntry {
                    entry,
                    src,
                    ascending,
                });
            }
        }
        Ok(Self {
            sources,
            heap,
            ascending,
        })
    }

    /// Pop the smallest entry together with its source index. Source 0 is the
    /// resident vector by construction of the executor.
    pub fn next(&mut self, cancel: &CancellationToken) -> Result<Option<(usize, Entry)>> {
        let head = match self.heap.pop() {
            Some(h) => h,
            None => return Ok(None),
        };
        cancel.check()?;
        if let Some(next) = self.sources[head.src].next_entry()? {
            self.heap.push(HeapEntry {
                entry: next,
                src: head.src,
                ascending: self.ascending,
            });
        }
        Ok(Some((head.src, head.entry)))
    }
}
