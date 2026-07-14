#!/usr/bin/env bash
# run-all-benchmarks.sh
# ─────────────────────────────────────────────────────────────────────────────
# Runs all resource profile combinations × N series, then generates the chart.
#
# Profiles:
#   500m  –  500m CPU  / 500Mi pod  (heap=300m, procs=1, tomcat-threads=50)
#   1cpu  –  1 CPU     / 1Gi pod    (heap=700m, procs=1, tomcat-threads=100)
#
# Usage:
#   bash scripts/run-all-benchmarks.sh          # 3 series (default)
#   SERIES=1 bash scripts/run-all-benchmarks.sh # quick single-series run
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail
cd "$(dirname "$0")/.."

SERIES="${SERIES:-3}"
RESULTS_ROOT="results"

# ── Profile definitions: "name:heap_mb:active_procs:threads_max"
declare -a PROFILES=(
    "500m:300:1:50"
    "1cpu:700:1:100"
)

echo "╔══════════════════════════════════════════════════════════════════╗"
echo "║            Virtual Threads Full Benchmark Suite                  ║"
echo "╠══════════════════════════════════════════════════════════════════╣"
echo "║  Profiles : 500m (500m CPU/500Mi pod)  ·  1cpu (1CPU/1Gi pod)   ║"
echo "║  Series   : $SERIES × each profile (platform + virtual)            ║"
echo "╚══════════════════════════════════════════════════════════════════╝"
echo ""

# ── Build once up front ───────────────────────────────────────────────────────
echo "🔨  Building JAR (once)..."
./gradlew bootJar -q
echo "✅  Build complete."
echo ""

TOTAL_RUNS=$(( ${#PROFILES[@]} * SERIES ))
RUN_NUM=0

for profile_def in "${PROFILES[@]}"; do
    IFS=: read -r profile heap procs threads <<< "$profile_def"

    for series in $(seq 1 "$SERIES"); do
        RUN_NUM=$(( RUN_NUM + 1 ))
        echo ""
        echo "▶▶▶  Run $RUN_NUM / $TOTAL_RUNS  ─  profile=$profile  series=$series"
        echo ""

        PROFILE="$profile"     \
        HEAP_MB="$heap"        \
        ACTIVE_PROCS="$procs"  \
        THREADS_MAX="$threads" \
        SERIES_NUM="$series"   \
        SKIP_BUILD="true"      \
        bash scripts/compare.sh

        echo ""
        echo "──── Cooling down 15s before next run ────"
        sleep 15
    done
done

echo ""
echo "╔══════════════════════════════════════════════════════════════════╗"
echo "║              All runs complete — generating chart                ║"
echo "╚══════════════════════════════════════════════════════════════════╝"
echo ""

# ── Aggregate results into JSON for chart generation ─────────────────────────
python3 scripts/aggregate-results.py

# ── Generate the HTML chart ────────────────────────────────────────────────────
echo "📊  Chart generated: results/benchmark-chart.html"
echo "    Open it in a browser and screenshot for sharing."
