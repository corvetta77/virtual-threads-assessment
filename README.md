# Virtual Threads Assessment

## Motivation

Virtual threads landed in Java 21 with a lot of hype. I wanted to see if they actually make a difference for a boring, everyday I/O-bound service — not a microbenchmark, but something closer to what runs in production.

So: same app, same pod, platform threads vs virtual threads. Does throughput go up? What does it cost in heap and GC? Is it worth flipping the switch?

---

Benchmarks Java platform threads vs virtual threads (Project Loom) under realistic HTTP workload using Spring Boot 4 + k6.

## What it measures

A Spring Boot app exposes two endpoints:

- `GET /api/process` — heavy workload: batch dequeue of 20 messages (4 KB blobs each), SHA-256 hashing, enrichment, and a simulated 80 ms blocking I/O (DB write)
- `GET /api/status` — light workload: 30 ms simulated I/O

k6 drives 250 concurrent VUs at `/api/process` and 120 at `/api/status` for 90 s. With platform threads, the fixed Tomcat pool (50–100 threads depending on profile) becomes the bottleneck; virtual threads eliminate the ceiling.

## Resource profiles

| Profile | CPU limit | Memory | Heap | Tomcat threads |
|---------|-----------|--------|------|----------------|
| `500m`  | 500m      | 500Mi  | 300m | 50             |
| `1cpu`  | 1 CPU     | 1Gi    | 700m | 100            |

## Prerequisites

- Java 25+ (26 used for development)
- [k6](https://k6.io/) — `brew install k6`
- Python 3 (for chart generation)

## Quick start

```bash
# Build
./gradlew bootJar

# Run a single comparison (platform vs virtual, 500m profile, series 1)
bash scripts/compare.sh

# Run full benchmark suite (2 profiles × 3 series each)
bash scripts/run-all-benchmarks.sh

# Generate chart
python3 scripts/aggregate-results.py
open results/benchmark-chart.html
```

## Toggle thread mode manually

```bash
# Platform threads (default)
java -jar build/libs/*.jar

# Virtual threads
java -jar build/libs/*.jar --spring.profiles.active=virtual
```

`application-virtual.properties` sets `spring.threads.virtual.enabled=true`.

## Results

Results are committed under `results/`. The aggregated interactive chart is published via GitHub Pages:  
**[View live benchmark chart](../../)** *(enabled after first CI run)*

Metrics captured per run: p50/p95/p99 latency, request rate, error rate, and thread count via `monitor.sh`.

## Project structure

```
src/                        Spring Boot application
k6/benchmark.js             k6 load test scenario
scripts/
  compare.sh                single platform-vs-virtual run
  run-all-benchmarks.sh     full suite across all profiles/series
  monitor.sh                CPU/thread sampler (runs alongside the app)
  aggregate-results.py      parses CSV/k6 JSON → aggregated.json + HTML chart
results/                    raw CSVs, k6 JSON summaries, generated chart
.github/workflows/          publishes chart to GitHub Pages on push
```
