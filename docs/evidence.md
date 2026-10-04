# Boundary stability evidence

Parameters: min_size=1024, avg_bits=12 (target 4096 B), max_size=16384. Algorithm: gear-cdc v1 (window 64 B, sha256 digests).

## 1. Identical content, different feed splits

Input: 1 MiB deterministic pseudo-random bytes (SplitMix64 seed 0xC0FFEE00).
Chunks (whole-buffer feed): 208

| feed pattern | chunks | boundaries identical |
|---|---|---|
| 1-byte pieces | 208 | true |
| 7-byte pieces | 208 | true |
| 64-byte pieces | 208 | true |
| 4096-byte pieces | 208 | true |
| 65536-byte pieces | 208 | true |

## 2. Local insertion of 100 bytes

Input: same 1 MiB stream; 100 bytes inserted at 25%, 50%, 75% of its length.

| insert at | unchanged prefix boundaries | resync boundary | affected chunk span (bytes) | within 2·max_size+window bound |
|---|---|---|---|---|
| 262144 | 51 | 265283 | 10414 | true |
| 524288 | 111 | 545712 | 9833 | true |
| 786432 | 156 | 789129 | 3724 | true |

Reading: "affected chunk span" is the byte range of chunks whose contents or boundaries changed, from the last unchanged boundary to the resynchronization boundary. When the inserted bytes contain no boundary trigger, the surrounding chunk simply absorbs them and no boundary moves; the span is then exactly one chunk. In all cases the disturbance stays local: it never reaches beyond 2·max_size + 64 bytes past the edit.

## 3. Long repeated bytes (1 MiB of 0xAA)

Chunks: 64, of which exactly max_size: 64. Min interior chunk: 16384 B, max: 16384 B.
Every interior chunk respects min_size <= len <= max_size: true.

## 4. Content digest sanity

- random 1 MiB sha256: `6409846511e8338399f24db6fb49cc135f48d64cbc89593059aefacb5fc7e534`
- repeat 1 MiB sha256: `c4145364a3ba46002fb14242872f795535bae6738b1e47ba21eb405cfdf820a5`

All assertions in this report passed at generation time.
