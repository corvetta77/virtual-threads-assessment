#!/usr/bin/env bash
# load-test-ab.sh
# ─────────────────────────────────────────────────────────────────────────────
# Fallback load test using Apache Bench (ab), which ships with macOS.
# Runs both endpoints simultaneously:
#   /api/process  ~50 req/s  (duration 60 s)
#   /api/status   ~80 req/s  (duration 60 s)
#
# Note: ab does not natively support "rate/s" mode; we approximate it by
# setting concurrency = desired_rps × avg_latency_s
#   process: 50 req/s × 0.08 s = ~4 concurrent
#   status:  80 req/s × 0.03 s = ~3 concurrent
#
# For precise rates install k6:  brew install k6
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

LABEL="${1:-run}"
DURATION=60   # seconds
RESULTS_DIR="results"
mkdir -p "$RESULTS_DIR"

TOTAL_PROCESS=$(( 50 * DURATION ))
TOTAL_STATUS=$(( 80 * DURATION ))

echo "════════════════════════════════════════════"
echo "  ab load test [$LABEL]"
echo "  /api/process  → $TOTAL_PROCESS requests, concurrency 4"
echo "  /api/status   → $TOTAL_STATUS requests, concurrency 3"
echo "════════════════════════════════════════════"

ab -n "$TOTAL_PROCESS" -c 4 -g "${RESULTS_DIR}/ab_process_${LABEL}.tsv" \
   http://localhost:8080/api/process > "${RESULTS_DIR}/ab_process_${LABEL}.txt" 2>&1 &
PID1=$!

ab -n "$TOTAL_STATUS"  -c 3 -g "${RESULTS_DIR}/ab_status_${LABEL}.tsv" \
   http://localhost:8080/api/status  > "${RESULTS_DIR}/ab_status_${LABEL}.txt"  2>&1 &
PID2=$!

wait "$PID1" "$PID2"

echo ""
echo "─── /api/process results ───"
grep -E "Requests per second|Time per request|Failed requests|Percentage of" \
     "${RESULTS_DIR}/ab_process_${LABEL}.txt"

echo ""
echo "─── /api/status results ───"
grep -E "Requests per second|Time per request|Failed requests|Percentage of" \
     "${RESULTS_DIR}/ab_status_${LABEL}.txt"

echo ""
echo "Full reports in $RESULTS_DIR/"
