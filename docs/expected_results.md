# Hand-computed expected results

These reference answers were derived by hand before the test suite was
written; the tests assert against these constants, not against values
produced by the implementation. Encoding: `ref = 0`, `alt = 1`; a
disagreement costs the call's phred quality, a match costs 0.

## basic_clean (3 sites, 6 reads, all qual 30)

Reads support only the patterns `000` and `111`. For the candidate
`h1 = 000` (h2 = 111) every read matches one haplotype exactly, so
`MEC = 0`. Any other candidate (e.g. `001`) makes at least one read
disagree with both haplotypes, so `MEC >= 30`. The optimum is unique.

Expected: one block, `h1 = A-C-G`, `h2 = G-T-A`, `MEC = 0`, not
ambiguous, 6 supporting reads, 0 conflicting.

## error_reads (basic_clean minus r5/r6, plus r_err)

`r_err` calls `s1=alt(25), s2=ref(30), s3=ref(30)`. Against the true
phase `000/111`:

- cost to `000` = 25 (only s1 disagrees)
- cost to `111` = 30 + 30 = 60 (s2, s3 disagree)
- contribution = min(25, 60) = 25

All other reads contribute 0, so `MEC = 25`. The next-best candidate
(`001`, `010`, `011`, …) forces at least one clean read to pay 30, so
the optimum is unique. `r_err` is the single conflicting read, assigned
to haplotype 1 with one correction at `s1` costing 25.

## ambiguous (2 sites, 4 reads, all qual 30)

Reads: `00`, `11`, `01`, `10` — one of each.

- Candidate `00/11`: reads `01` and `10` each pay min(30, 30) = 30 → `MEC = 60`
- Candidate `01/10`: reads `00` and `11` each pay 30 → `MEC = 60`

Two optima → the block is ambiguous; both `A-C/G-T` and `A-T/G-C` are
reported, and the uncertainty list names the tie.

## disconnected (5 sites, 3 components)

Reads only ever co-cover `s1,s2` or `s3,s4`; `s5` is covered by a
single-site read. Expected: three blocks — `[s1,s2]` phased `A-C/G-T`,
`[s3,s4]` phased `G-T/A-C` (canonical orientation pins the first site to
its ref allele), `[s5]` singleton. `MEC = 0` everywhere. No statement
about phase *between* blocks is made; the singleton site is listed as an
uncertainty.
