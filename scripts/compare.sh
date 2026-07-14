#!/usr/bin/env bash
# compare.sh
# ─────────────────────────────────────────────────────────────────────────────
# Full automated comparison:
#   1. Build the JAR (skipped if SKIP_BUILD=true)
#   2. Run platform-threads test  (monitor + load)
#   3. Run virtual-threads test   (monitor + load)
#   4. Print side-by-side summary
#
# Resource profile env vars (with defaults simulating 500m CPU / 500Mi pod):
#   PROFILE        – label used in result filenames, e.g. "500m" or "1cpu"
#   HEAP_MB        – -Xmx value in MB          (default: 300)
#   ACTIVE_PROCS   – -XX:ActiveProcessorCount  (default: 1)
#   THREADS_MAX    – Tomcat thread pool size    (default: 50)
#   SERIES_NUM     – series/run number          (default: 1)
#   SKIP_BUILD     – set to "true" to skip JAR build
#
# Requires k6.  If k6 is not installed it falls back to ab.
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail
cd "$(dirname "$0")/.."

# Kill any leftover app from a previous run
pkill -f 'virtual-threads-assessment.*\.jar' 2>/dev/null && sleep 2 || true

# Resource configuration (can be overridden via env)
PROFILE="${PROFILE:-500m}"
HEAP_MB="${HEAP_MB:-300}"
ACTIVE_PROCS="${ACTIVE_PROCS:-1}"
THREADS_MAX="${THREADS_MAX:-50}"
SERIES_NUM="${SERIES_NUM:-1}"
SKIP_BUILD="${SKIP_BUILD:-false}"

RESULTS_DIR="results/${PROFILE}/series${SERIES_NUM}"
mkdir -p "$RESULTS_DIR"
chmod +x scripts/*.sh

# ── 0. Build ──────────────────────────────────────────────────────────────────
if [ "$SKIP_BUILD" = "true" ]; then
    echo "⏭️  Skipping build (SKIP_BUILD=true)"
else
    echo "🔨  Building JAR..."
    ./gradlew bootJar -q
fi

# ── Helper ───────────────────────────────────────────────────────────────────
wait_for_app() {
    echo "⏳  Waiting for app to start..."
    for i in $(seq 1 30); do
        curl -s http://localhost:8080/actuator/health | grep -q '"UP"' && return 0
        sleep 2
    done
    echo "❌  App did not start within 60 s"
    exit 1
}

run_test() {
    local label="$1"
    local vt_flag="$2"

    echo ""
    echo "════════════════════════════════════════════"
    echo "  RUN: $label  [profile=$PROFILE heap=${HEAP_MB}m procs=$ACTIVE_PROCS tomcat-threads=$THREADS_MAX series=$SERIES_NUM]"
    echo "════════════════════════════════════════════"

    # Start app in background
    # Simulate k8s pod via JVM flags passed from env
    java -Xmx${HEAP_MB}m \
         -XX:ActiveProcessorCount=${ACTIVE_PROCS} \
         -Dserver.tomcat.threads.max=${THREADS_MAX} \
         -Dspring.threads.virtual.enabled="$vt_flag" \
         -jar build/libs/virtual-threads-assessment-*.jar \
         > "${RESULTS_DIR}/app_${label}.log" 2>&1 &
    APP_PID=$!

    wait_for_app

    # Start monitor in background
    bash scripts/monitor.sh "${PROFILE}_${label}_s${SERIES_NUM}" > "${RESULTS_DIR}/monitor_${label}.log" 2>&1 &
    MONITOR_PID=$!

    # Run load test (|| true so threshold failures don't abort the suite)
    if command -v k6 &>/dev/null; then
        mkdir -p "${RESULTS_DIR}"
        k6 run --out "json=${RESULTS_DIR}/k6_${label}.json" k6/benchmark.js \
           2>&1 | tee "${RESULTS_DIR}/k6_${label}_summary.txt" || true
    else
        echo "  (k6 not found — using ab fallback)"
        bash scripts/load-test-ab.sh "$label"
    fi

    # Kill app first — monitor detects process death, writes summary, then exits
    kill "$APP_PID"     2>/dev/null || true
    wait "$APP_PID"     2>/dev/null || true
    # Now wait for monitor to finish writing its summary (it exits naturally)
    wait "$MONITOR_PID" 2>/dev/null || true

    echo "  Stopped app (PID $APP_PID)"
    sleep 3
}

run_test "platform" "false"
run_test "virtual"  "true"

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════════════════════"
echo "  COMPARISON SUMMARY  [profile=$PROFILE  series=$SERIES_NUM]"
echo "  Config: heap=${HEAP_MB}m  active-procs=$ACTIVE_PROCS  tomcat-threads=$THREADS_MAX"
echo "════════════════════════════════════════════════════════════════════"
printf "%-30s %-18s %-18s\n" "Metric" "Platform Threads" "Virtual Threads"
echo "──────────────────────────────────────────────────────────────────"

print_metric() {
    local label="$1"
    local p_val="$2"
    local v_val="$3"
    printf "%-30s %-18s %-18s\n" "$label" "$p_val" "$v_val"
}

extract_monitor() {
    local label="$1" metric="$2"
    # Extract first number (integer or decimal) from the matching line
    grep "$metric" "${RESULTS_DIR}/monitor_${label}.log" 2>/dev/null \
        | tail -1 \
        | grep -oE '[0-9]+\.?[0-9]*' \
        | head -1 \
        || echo "N/A"
}

print_metric "Max CPU"         "$(extract_monitor platform 'Max CPU')"         "$(extract_monitor virtual 'Max CPU')"
print_metric "Avg CPU"         "$(extract_monitor platform 'Avg CPU')"         "$(extract_monitor virtual 'Avg CPU')"
print_metric "Max RSS Memory"  "$(extract_monitor platform 'Max RSS')"         "$(extract_monitor virtual 'Max RSS')"
print_metric "Max Heap Used"   "$(extract_monitor platform 'Max Heap Used')"   "$(extract_monitor virtual 'Max Heap Used')"
print_metric "Max Live Threads" "$(extract_monitor platform 'Max Live Threads')" "$(extract_monitor virtual 'Max Live Threads')"

echo ""
echo "Detailed results in: $RESULTS_DIR/"
echo "  k6 summaries : ${RESULTS_DIR}/k6_*_summary.txt"
echo "  Monitor CSV  : results/metrics_*.csv"
echo "  App logs     : ${RESULTS_DIR}/app_*.log"
