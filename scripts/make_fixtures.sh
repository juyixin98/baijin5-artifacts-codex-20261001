#!/usr/bin/env bash
# Regenerates every fixture under fixtures/ deterministically.
#
# IMPORTANT: fixtures/expected/final-tree is the HAND-AUTHORED reference
# answer. It is written here as explicit, literal content — it is never
# produced by running the merge engine — so tests compare the engine against
# an independent oracle.
set -euo pipefail
cd "$(dirname "$0")/.."

rm -rf fixtures
mkdir -p fixtures/layers/layer1 fixtures/layers/layer2 fixtures/layers/layer3
mkdir -p fixtures/expected/final-tree fixtures/malicious fixtures/external

# ---------------------------------------------------------------------------
# Good layer stack (bottom -> top): layer1, layer2, layer3
# Exercises: delete-then-recreate, opaque dir, file<->dir same-name swap,
# cross-layer override, in-root symlink.
# ---------------------------------------------------------------------------

# layer1 (bottom)
mkdir -p fixtures/layers/layer1/etc fixtures/layers/layer1/data fixtures/layers/layer1/keep
printf 'app-version=1\n'      > fixtures/layers/layer1/etc/app.conf
printf 'A from layer1\n'      > fixtures/layers/layer1/data/a.txt
printf 'B from layer1\n'      > fixtures/layers/layer1/data/b.txt
printf 'swap was a file in layer1\n' > fixtures/layers/layer1/swap
printf 'keep me\n'            > fixtures/layers/layer1/keep/keep.txt

# layer2 (middle): whiteouts + opaque marker + file->dir swap
mkdir -p fixtures/layers/layer2/etc fixtures/layers/layer2/data fixtures/layers/layer2/swap
: > fixtures/layers/layer2/etc/.wh.app.conf        # delete etc/app.conf from layer1
: > fixtures/layers/layer2/data/.wh..wh..opq       # hide layer1's data/*
printf 'C from layer2\n'      > fixtures/layers/layer2/data/c.txt
: > fixtures/layers/layer2/.wh.swap                # delete file "swap" from layer1
printf 'swap is now a dir (layer2)\n' > fixtures/layers/layer2/swap/inner.txt

# layer3 (top): recreate after delete + in-root symlink
mkdir -p fixtures/layers/layer3/etc
printf 'app-version=3\n'      > fixtures/layers/layer3/etc/app.conf
ln -s app.conf fixtures/layers/layer3/etc/current  # relative, stays in root

# ---------------------------------------------------------------------------
# Hand-authored expected final tree (the reference oracle).
# ---------------------------------------------------------------------------
mkdir -p fixtures/expected/final-tree/etc fixtures/expected/final-tree/data
mkdir -p fixtures/expected/final-tree/swap fixtures/expected/final-tree/keep
printf 'app-version=3\n'      > fixtures/expected/final-tree/etc/app.conf
ln -s app.conf fixtures/expected/final-tree/etc/current
printf 'C from layer2\n'      > fixtures/expected/final-tree/data/c.txt
printf 'swap is now a dir (layer2)\n' > fixtures/expected/final-tree/swap/inner.txt
printf 'keep me\n'            > fixtures/expected/final-tree/keep/keep.txt

# ---------------------------------------------------------------------------
# Malicious / out-of-scope fixtures.
# ---------------------------------------------------------------------------

# A symlink whose target escapes the merge root.
mkdir -p fixtures/malicious/layer-escape/bad
ln -s ../../external/sentinel.txt fixtures/malicious/layer-escape/bad/evil

# An absolute symlink (outside the supported link scope).
mkdir -p fixtures/malicious/layer-abslink
ln -s /etc/hostname fixtures/malicious/layer-abslink/abs

# A hardlink pair (outside the supported link scope). Entries are scanned in
# sorted name order, so first.txt is kept and second.txt (the hardlink) is
# reported as the unsupported entry.
mkdir -p fixtures/malicious/layer-hardlink/x
printf 'shared inode\n' > fixtures/malicious/layer-hardlink/x/first.txt
ln fixtures/malicious/layer-hardlink/x/first.txt fixtures/malicious/layer-hardlink/x/second.txt

# A malformed whiteout marker (empty target name).
mkdir -p fixtures/malicious/layer-badwhiteout
: > 'fixtures/malicious/layer-badwhiteout/.wh.'

# A directory a merge must never touch; the escape fixture points here.
printf 'do not touch\n' > fixtures/external/sentinel.txt

echo "fixtures regenerated under fixtures/"
