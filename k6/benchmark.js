/**
 * k6 benchmark scenario for virtual-threads-assessment
 *
 * Two independent constant-arrival-rate scenarios run simultaneously:
 *   • /api/process  → 50 req/s  (heavy I/O: 80 ms sleep)
 *   • /api/status   → 80 req/s  (light I/O: 30 ms sleep)
 *
 * Duration: 90 s  (30 s warm-up + 60 s measurement)
 *
 * Run:
 *   k6 run --out json=results/k6-platform.json k6/benchmark.js
 *   k6 run --out json=results/k6-virtual.json  k6/benchmark.js
 *
 * Install k6 (macOS):
 *   brew install k6
 */

import http from 'k6/http';
import { check } from 'k6';
import { Trend, Counter } from 'k6/metrics';

const processLatency = new Trend('process_latency', true);
const statusLatency = new Trend('status_latency', true);
const processErrors = new Counter('process_errors');
const statusErrors = new Counter('status_errors');

// ─── Load design ─────────────────────────────────────────────────────────────
// Tomcat platform-thread pool = 200 (server.tomcat.threads.max).
// To expose thread exhaustion we pin MORE concurrent VUs than the pool size:
//   • process_endpoint: 250 VUs × 80 ms/req  → ~250 in-flight (pool limit: 200)
//   • status_endpoint:  120 VUs × 30 ms/req  → ~120 in-flight
//
// Platform threads: 200 busy + 50 queuing → latency climbs under backpressure.
// Virtual threads:  all 250 served immediately → latency stays near sleep time.
// ─────────────────────────────────────────────────────────────────────────────
export const options = {
    scenarios: {
        process_endpoint: {
            executor: 'constant-vus',
            vus: 250,          // 250 concurrent > 200 platform-thread pool
            duration: '90s',
            exec: 'testProcess',
        },
        status_endpoint: {
            executor: 'constant-vus',
            vus: 120,          // well within pool but adds background pressure
            duration: '90s',
            exec: 'testStatus',
        },
    },
    thresholds: {
        'process_latency': ['p(99)<5000'],
        'status_latency': ['p(99)<1000'],
        'http_req_failed': ['rate<0.01'],
    },
};

const BASE = __ENV.BASE_URL || 'http://localhost:8080';

export function testProcess() {
    const res = http.get(`${BASE}/api/process`, { tags: { endpoint: 'process' } });
    processLatency.add(res.timings.duration);
    const ok = check(res, { 'process 200': (r) => r.status === 200 });
    if (!ok) processErrors.add(1);
}

export function testStatus() {
    const res = http.get(`${BASE}/api/status`, { tags: { endpoint: 'status' } });
    statusLatency.add(res.timings.duration);
    const ok = check(res, { 'status 200': (r) => r.status === 200 });
    if (!ok) statusErrors.add(1);
}
