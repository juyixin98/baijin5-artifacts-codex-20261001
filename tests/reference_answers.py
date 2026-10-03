"""Hand-computed reference answers (test oracle).

Every value below was derived BY HAND from the fixture definitions, not by
running the implementation under test. Derivations are shown in comments so
a reviewer can re-verify each number independently.

Fixture recap (0-based half-open):
- Contig chrSyn1, length 120, base(i) = "ACGT"[i % 4].
- txA (+): exons [10,20) [30,45) [60,70)  -> tx_len = 10+15+10 = 35
    tx spans: E1 t[0,10)  E2 t[10,25)  E3 t[25,35)
- txB (-): exons [15,25) [40,50) [80,100) -> tx_len = 10+10+20 = 40
    transcript order is REVERSED genomically:
    E1=[80,100) t[0,20)  E2=[40,50) t[20,30)  E3=[15,25) t[30,40)
- txC (+): exons [12,18) [65,75) -> tx_len = 6+10 = 16
"""

# ---------------------------------------------------------------------------
# txA (+) point mapping, genomic -> transcript.
# Plus strand: t = cum_before_exon + (g - exon.start).
#   g=10 -> E1 offset 0  -> t=0
#   g=19 -> E1 offset 9  -> t=9
#   g=30 -> E2 offset 0  -> t=10  (cum before E2 = 10)
#   g=44 -> E2 offset 14 -> t=24
#   g=60 -> E3 offset 0  -> t=25  (cum before E3 = 25)
#   g=69 -> E3 offset 9  -> t=34
TXA_G2T_OK = {10: 0, 19: 9, 30: 10, 44: 24, 60: 25, 69: 34}

# txA introns are [20,30) and [45,60): any g inside is INTRONIC.
TXA_G2T_INTRONIC = [20, 29, 45, 59]

# Outside the exon span [10,70) but inside the contig: OUT_OF_TRANSCRIPT.
TXA_G2T_OUT_OF_TRANSCRIPT = [0, 5, 9, 70, 100, 119]

# Beyond contig length 120: OUT_OF_CONTIG.
TXA_G2T_OUT_OF_CONTIG = [120, 500]

# txA transcript -> genomic (inverse of the above).
TXA_T2G_OK = {0: 10, 9: 19, 10: 30, 24: 44, 25: 60, 34: 69}
TXA_T2G_OUT_OF_TRANSCRIPT = [35, 100]

# ---------------------------------------------------------------------------
# txB (-) point mapping.
# Minus strand: t = cum_before_exon + (exon.end - 1 - g).
#   g=99 -> E1 (t[0,20))  offset 100-1-99=0  -> t=0
#   g=80 -> E1 offset 19 -> t=19
#   g=49 -> E2 (t[20,30)) offset 50-1-49=0 -> t=20
#   g=40 -> E2 offset 9  -> t=29
#   g=24 -> E3 (t[30,40)) offset 25-1-24=0 -> t=30
#   g=15 -> E3 offset 9  -> t=39
TXB_G2T_OK = {99: 0, 80: 19, 49: 20, 40: 29, 24: 30, 15: 39}

# txB introns are [25,40) and [50,80).
TXB_G2T_INTRONIC = [25, 39, 50, 79]

# Outside span [15,100): OUT_OF_TRANSCRIPT.
TXB_G2T_OUT_OF_TRANSCRIPT = [0, 14, 100, 119]

# txB transcript -> genomic: t maps to exon.end - 1 - offset.
#   t=0  -> E1 100-1-0=99     t=19 -> E1 100-1-19=80
#   t=20 -> E2 50-1-0=49      t=29 -> E2 50-1-9=40
#   t=30 -> E3 25-1-0=24      t=39 -> E3 25-1-9=15
TXB_T2G_OK = {0: 99, 19: 80, 20: 49, 29: 40, 30: 24, 39: 15}
TXB_T2G_OUT_OF_TRANSCRIPT = [40, 1000]

# ---------------------------------------------------------------------------
# Transcript identity isolation: the SAME genomic position maps differently
# per transcript, and a position exonic in one transcript can be intronic in
# another.
#   g=14: txA -> t=4 (E1 offset 4); txC -> t=2 (E1 offset 2)
#   g=18: exonic in txA (t=8) but INTRONIC in txC (txC intron [18,65))
#   g=64: INTRONIC in txC, exonic in txA (E3 offset 4 -> t=29)
ISOLATION_CASES = [
    # (tx_id, gpos, expected_status, expected_reason, expected_tpos)
    ("txA", 14, "OK", "OK", 4),
    ("txC", 14, "OK", "OK", 2),
    ("txA", 18, "OK", "OK", 8),
    ("txC", 18, "REJECTED", "INTRONIC", None),
    ("txC", 64, "REJECTED", "INTRONIC", None),
    ("txA", 64, "OK", "OK", 29),
]

UNKNOWN_TRANSCRIPT_ID = "txZZZ"

# ---------------------------------------------------------------------------
# Interval mapping (hand-derived).
#
# Case A1: txA genomic [18,32). Exonic bases: 18,19 (E1) and 30,31 (E2).
#   F1: g[18,20) <-> t[8,10)   F2: g[30,32) <-> t[10,12)
#   Gap g[20,30) INTRONIC. Status PARTIAL. mapped_length = 4.
TXA_G_INTERVAL_18_32 = {
    "status": "PARTIAL",
    "fragments": [
        {"g_start": 18, "g_end": 20, "t_start": 8, "t_end": 10},
        {"g_start": 30, "g_end": 32, "t_start": 10, "t_end": 12},
    ],
    "gaps": [{"g_start": 20, "g_end": 30, "reason": "INTRONIC"}],
    "mapped_length": 4,
}

# Case A2 (round-trip partner of A1): txA transcript [8,12).
#   t8,t9 -> g18,g19 (E1); t10,t11 -> g30,g31 (E2). Fully exonic: OK.
TXA_T_INTERVAL_8_12 = {
    "status": "OK",
    "fragments": [
        {"g_start": 18, "g_end": 20, "t_start": 8, "t_end": 10},
        {"g_start": 30, "g_end": 32, "t_start": 10, "t_end": 12},
    ],
    "gaps": [],
    "mapped_length": 4,
}

# Case B1: txB transcript [18,22) (minus strand).
#   t18 -> g81, t19 -> g80  => merged block g[80,82) <-> t[18,20)
#   t20 -> g49, t21 -> g48  => block g[48,50) <-> t[20,22)
# Fragments in transcript order; mapped_length 4 == input length 4.
TXB_T_INTERVAL_18_22 = {
    "status": "OK",
    "fragments": [
        {"g_start": 80, "g_end": 82, "t_start": 18, "t_end": 20},
        {"g_start": 48, "g_end": 50, "t_start": 20, "t_end": 22},
    ],
    "gaps": [],
    "mapped_length": 4,
}

# Case B2: txB genomic [48,82). Exonic: E2 [48,50) and E1 [80,82).
# Fragments must come back in TRANSCRIPT order (E1 first on minus strand),
# i.e. genomically DESCENDING — this pins fragment ordering to the
# transcript, not the genome.
#   F1: g[80,82) <-> t[18,20)   F2: g[48,50) <-> t[20,22)
#   Gap g[50,80) INTRONIC. Status PARTIAL.
TXB_G_INTERVAL_48_82 = {
    "status": "PARTIAL",
    "fragments": [
        {"g_start": 80, "g_end": 82, "t_start": 18, "t_end": 20},
        {"g_start": 48, "g_end": 50, "t_start": 20, "t_end": 22},
    ],
    "gaps": [{"g_start": 50, "g_end": 80, "reason": "INTRONIC"}],
    "mapped_length": 4,
}

# Case A3: txA genomic [5,12). Only [10,12) is exonic.
#   F1: g[10,12) <-> t[0,2); gap g[5,10) OUT_OF_TRANSCRIPT. PARTIAL.
TXA_G_INTERVAL_5_12 = {
    "status": "PARTIAL",
    "fragments": [{"g_start": 10, "g_end": 12, "t_start": 0, "t_end": 2}],
    "gaps": [{"g_start": 5, "g_end": 10, "reason": "OUT_OF_TRANSCRIPT"}],
    "mapped_length": 2,
}

# Case A4: txA genomic [20,30) lies entirely in intron 1 -> REJECTED/INTRONIC.
TXA_G_INTERVAL_20_30 = {"status": "REJECTED", "reason": "INTRONIC"}

# Case A5: txA genomic [70,75) entirely past the last exon.
TXA_G_INTERVAL_70_75 = {"status": "REJECTED", "reason": "OUT_OF_TRANSCRIPT"}

# Case B3: txB full-length transcript [0,40) -> three fragments, tx order.
TXB_T_INTERVAL_FULL = {
    "status": "OK",
    "fragments": [
        {"g_start": 80, "g_end": 100, "t_start": 0, "t_end": 20},
        {"g_start": 40, "g_end": 50, "t_start": 20, "t_end": 30},
        {"g_start": 15, "g_end": 25, "t_start": 30, "t_end": 40},
    ],
    "mapped_length": 40,
}

# ---------------------------------------------------------------------------
# Base direction (hand-derived from base(i) = "ACGT"[i % 4]).
#
# txA spliced = seq[10:20] + seq[30:45] + seq[60:70]
#   seq[10:20]: 10%4=2 -> "GTACGTACGT"
#   seq[30:45]: 30%4=2 -> "GTACGTACGTACGTA"
#   seq[60:70]: 60%4=0 -> "ACGTACGTAC"
TXA_SPLICED = "GTACGTACGT" + "GTACGTACGTACGTA" + "ACGTACGTAC"

# txB spliced = revcomp(seq[15:25] + seq[40:50] + seq[80:100])
#             = revcomp(seq[80:100]) + revcomp(seq[40:50]) + revcomp(seq[15:25])
#   revcomp("ACGTACGTACGTACGTACGT") = "ACGTACGTACGTACGTACGT"  (self-revcomp)
#   revcomp("ACGTACGTAC")           = "GTACGTACGT"
#   revcomp("TACGTACGTA")           = "TACGTACGTA"            (self-revcomp)
TXB_SPLICED = (
    "ACGTACGTACGTACGTACGT" + "GTACGTACGT" + "TACGTACGTA"
)

# txC spliced = seq[12:18] + seq[65:75]; 12%4=0, 65%4=1.
TXC_SPLICED = "ACGTAC" + "CGTACGTACG"

# Spot base checks: (tx_id, tpos, expected base).
#   txB t=0  <-> g=99: base 'T', complement 'A'
#   txB t=19 <-> g=80: base 'A', complement 'T'
#   txB t=20 <-> g=49: base 'C', complement 'G'
#   txB t=39 <-> g=15: base 'T', complement 'A'
BASE_CHECKS = [
    ("txA", 0, "G"),
    ("txA", 10, "G"),
    ("txB", 0, "A"),
    ("txB", 19, "T"),
    ("txB", 20, "G"),
    ("txB", 39, "A"),
    ("txC", 0, "A"),
    ("txC", 6, "C"),
]
