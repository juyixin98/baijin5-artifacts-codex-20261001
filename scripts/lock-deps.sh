#!/usr/bin/env bash
# Regenerate DEPENDENCIES.lock: resolved dependency and plugin versions for all
# modules. Commit the result; diffing it detects unexpected version drift.
set -euo pipefail
cd "$(dirname "$0")/.."
strip_ansi() { sed 's/\x1b\[[0-9;]*m//g'; }
# dependency:list resolves inter-module jars from the local repository,
# so install the current build first
mvn -q -B install -DskipTests
ALL_DEPS="$(mvn -B -U dependency:list 2>/dev/null | strip_ansi \
  | grep -oE '[a-z0-9._-]+:[a-z0-9._-]+:jar:[0-9][0-9a-zA-Z.-]*:[a-z]+' | sort -u || true)"
{
  echo "# Resolved dependency lock - regenerate with ./scripts/lock-deps.sh"
  echo "# Pinned in pom.xml <properties>; this file records what Maven actually resolved."
  echo "# Toolchain: $(java -version 2>&1 | head -1)"
  echo "#"
  echo "# compile/runtime scope:"
  printf '%s\n' "$ALL_DEPS" | grep -E ':(compile|runtime)$' | sed 's/^/#   /' || echo "#   (none)"
  echo "# test scope:"
  printf '%s\n' "$ALL_DEPS" | grep -E ':test$' | sed 's/^/#   /' || echo "#   (none)"
  echo "# pinned plugin/dependency versions (from pom.xml properties):"
  grep -oE '<[a-z.]+version>[0-9][^<]*</[a-z.]+version>' pom.xml \
    | sed -E 's/<([a-z.]+version)>([^<]+)<\/[a-z.]+version>/#   \1 = \2/' | sort -u
} > DEPENDENCIES.lock
echo "wrote DEPENDENCIES.lock"
