//! Independent verification helpers. These are deliberately simple and do
//! NOT reuse the protocol/crypto code paths, so tests and the demo compare
//! the cryptographic output against an independent reference.

use std::collections::BTreeSet;

/// Reference intersection computed directly on plaintext sets.
/// Returns a sorted, duplicate-free set.
pub fn plaintext_intersection(a: &[Vec<u8>], b: &[Vec<u8>]) -> Vec<Vec<u8>> {
    let set_a: BTreeSet<&[u8]> = a.iter().map(Vec::as_slice).collect();
    let set_b: BTreeSet<&[u8]> = b.iter().map(Vec::as_slice).collect();
    set_a
        .intersection(&set_b)
        .map(|s| s.to_vec())
        .collect()
}

/// Scan a serialized transcript for raw element bytes. Returns every element
/// that appears verbatim — an empty result means the transcript carries only
/// blinded/encoded material.
pub fn find_leaked_elements(transcript: &[u8], elements: &[Vec<u8>]) -> Vec<Vec<u8>> {
    elements
        .iter()
        .filter(|e| !e.is_empty() && contains_subslice(transcript, e))
        .cloned()
        .collect()
}

fn contains_subslice(haystack: &[u8], needle: &[u8]) -> bool {
    if needle.len() > haystack.len() {
        return false;
    }
    haystack.windows(needle.len()).any(|w| w == needle)
}
