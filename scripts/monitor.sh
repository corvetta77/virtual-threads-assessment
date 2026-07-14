#!/usr/bin/env bash
# monitor.sh
# ─────────────────────────────────────────────────────────────────────────────
# Samples CPU %, RSS memory, and live JVM thread count once per second for the
# Spring Boot process.  Writes a CSV and prints a summary at the end.
#
# Usage:
#   ./scripts/monitor.sh [label]          # label defaults to "run"
#
# Example:
#   ./scripts/monitor.sh platform &       # start monitoring in background
#   <run your load test>
#   kill %1                               # stop monitoring
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

LABEL="${1:-run}"
RESULTS_DIR="results"
mkdir -p "$RESULTS_DIR"

OUTPUT="${RESULTS_DIR}/metrics_${LABEL}_$(date +%Y%m%d_%H%M%S).csv"

# Wait for the Spring Boot process to appear
echo "⏳  Waiting for Spring Boot process..."
PID=""
for i in $(seq 1 30); do
    PID=$(pgrep -f "virtual-threads-assessment" 2>/dev/null | head -1 || true)
    [[ -n "$PID" ]] && break
    sleep 1
done

if [[ -z "$PID" ]]; then
    echo "❌  Could not find Spring Boot process.  Is the app running?"
    exit 1
fi

echo "✅  Found PID $PID — writing metrics to $OUTPUT"
echo "timestamp_s,cpu_pct,rss_mb,heap_used_mb,heap_max_mb,live_threads,gc_count_delta,gc_pause_ms" > "$OUTPUT"

PREV_GC_COUNT=0

get_jvm_metric() {
    local metric="$1"
    curl -s "http://localhost:8080/actuator/metrics/${metric}" 2>/dev/null \
        | python3 -c "import sys,json; d=json.load(sys.stdin); print(int(d['measurements'][0]['value']))" 2>/dev/null \
        || echo "0"
}

while kill -0 "$PID" 2>/dev/null; do
    TS=$(date +%s)
    # CPU and RSS via ps (RSS is in KB on macOS)
    read -r CPU RSS <<< "$(ps -p "$PID" -o %cpu=,rss= 2>/dev/null || echo '0 0')"
    RSS_MB=$(echo "scale=1; ${RSS:-0} / 1024" | bc)

    HEAP_USED=$(get_jvm_metric "jvm.memory.used?tag=area:heap")
    HEAP_MAX=$(get_jvm_metric  "jvm.memory.max?tag=area:heap")
    HEAP_USED_MB=$(echo "scale=0; ${HEAP_USED:-0} / 1048576" | bc)
    HEAP_MAX_MB=$(echo "scale=0; ${HEAP_MAX:-1} / 1048576" | bc)
    THREADS=$(get_jvm_metric "jvm.threads.live")
    # Try jvm.gc.pause (G1GC / standard), fallback to jvm.gc.concurrent.phase.time (ZGC/Shenandoah)
    GC_RAW=$(curl -s "http://localhost:8080/actuator/metrics/jvm.gc.pause" 2>/dev/null)
    if ! echo "$GC_RAW" | python3 -c "import sys,json; json.load(sys.stdin)" 2>/dev/null; then
        GC_RAW=$(curl -s "http://localhost:8080/actuator/metrics/jvm.gc.concurrent.phase.time" 2>/dev/null)
    fi
    GC_COUNT=$(echo "$GC_RAW" | python3 -c "import sys,json; d=json.load(sys.stdin); m=[x for x in d.get('measurements',[]) if x['statistic']=='COUNT']; print(int(m[0]['value']) if m else 0)" 2>/dev/null || echo "0")
    GC_DELTA=$(( GC_COUNT - PREV_GC_COUNT ))
    PREV_GC_COUNT=$GC_COUNT
    # GC pause total (ms) - cumulative from actuator
    GC_PAUSE_RAW=$(echo "$GC_RAW" | python3 -c "import sys,json; d=json.load(sys.stdin); m=[x for x in d.get('measurements',[]) if x['statistic']=='TOTAL_TIME']; print(int(m[0]['value']*1000) if m else 0)" 2>/dev/null || echo "0")

    echo "${TS},${CPU},${RSS_MB},${HEAP_USED_MB},${HEAP_MAX_MB},${THREADS},${GC_DELTA},${GC_PAUSE_RAW}" >> "$OUTPUT"
    printf "  CPU: %5s%%  RSS: %6s MB  Heap: %s/%s MB  Threads: %s  GC/s: %s\n" \
           "$CPU" "$RSS_MB" "$HEAP_USED_MB" "$HEAP_MAX_MB" "$THREADS" "$GC_DELTA"

    sleep 1
done

echo ""
echo "════════════════════════════════════════════"
echo "  SUMMARY [$LABEL]"
echo "════════════════════════════════════════════"
awk -F',' '
NR > 1 {
    if ($2+0 > max_cpu+0) max_cpu = $2
    if ($3+0 > max_rss+0) max_rss = $3
    if ($4+0 > max_heap+0) max_heap = $4
    if ($6+0 > max_thr+0) max_thr = $6
    sum_cpu += $2; sum_rss += $3; sum_heap += $4; sum_gc += $7; count++
    last_gc_pause = $8
}
END {
    printf "  Max CPU:          %.1f%%\n",  max_cpu
    printf "  Avg CPU:          %.1f%%\n",  sum_cpu/count
    printf "  Max RSS:          %.1f MB\n", max_rss
    printf "  Avg RSS:          %.1f MB\n", sum_rss/count
    printf "  Max Heap Used:    %.0f MB\n", max_heap
    printf "  Avg Heap Used:    %.0f MB\n", sum_heap/count
    printf "  Max Live Threads: %d\n",      max_thr
    printf "  Total GC events:  %.0f\n",    sum_gc
    printf "  GC Pause Total:   %.0f ms\n", last_gc_pause
}' "$OUTPUT"

echo ""
echo "Full data: $OUTPUT"
