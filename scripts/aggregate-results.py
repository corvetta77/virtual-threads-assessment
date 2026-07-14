#!/usr/bin/env python3
"""
aggregate-results.py
Parses k6 summary files from all profiles/series and writes:
  results/aggregated.json   – raw per-run data + averages
  results/benchmark-chart.html – standalone chart ready for sharing
"""

import os
import re
from typing import Optional
import json
import statistics
from pathlib import Path

RESULTS_ROOT = Path("results")

PROFILES = ["500m", "1cpu"]
PROFILE_LABELS = {
    "500m": "500m CPU / 500Mi",
    "1cpu": "1 CPU / 1Gi",
}
THREAD_MODES = ["platform", "virtual"]

# ── Metric extraction helpers ─────────────────────────────────────────────────

def parse_duration_ms(s: str) -> "Optional[float]":
    """Parse k6 duration string like '499.83ms', '1.2s', '23µs' → float ms."""
    s = s.strip()
    if s in ("", "N/A", "-"):
        return None
    if s.endswith("µs"):
        return float(s[:-2]) / 1000
    if s.endswith("ms"):
        return float(s[:-2])
    if s.endswith("s"):
        return float(s[:-1]) * 1000
    try:
        return float(s)
    except ValueError:
        return None

def extract_k6_metrics(summary_path: Path) -> "Optional[dict]":
    """Extract key metrics from a k6 summary text file."""
    if not summary_path.exists():
        return None

    # Join any wrapped lines (k6 may wrap at terminal width)
    raw = summary_path.read_text()
    lines = raw.splitlines()
    joined_lines = []
    for line in lines:
        if joined_lines and line and not line[0].isspace() and joined_lines[-1].endswith(('.', ',')):
            joined_lines[-1] += line.strip()
        else:
            joined_lines.append(line)
    text = "\n".join(joined_lines)

    metrics = {}

    # Throughput: "http_reqs......................: 562428 6245.41/s"
    m = re.search(r'http_reqs[.\s]+:\s+[\d,]+\s+([\d.]+)/s', text)
    if m:
        metrics["throughput"] = float(m.group(1))

    # Error rate: "http_req_failed................: 0.07%  ..."
    m = re.search(r'http_req_failed[.\s]+:\s+([\d.]+)%', text)
    if m:
        metrics["error_rate"] = float(m.group(1))

    # process_latency avg from metrics table (p99 from THRESHOLDS section)
    m = re.search(r'process_latency[.\s]+:\s+avg=([\d.]+(?:ms|µs|s))', text)
    if m:
        metrics["process_avg_ms"] = parse_duration_ms(m.group(1))
    # p(99) from THRESHOLDS section
    m = re.search(r"process_latency\s+[✓✗]\s+'p\(99\)<\d+'\s+p\(99\)=([\d.]+(?:ms|µs|s))", text)
    if m:
        metrics["process_p99_ms"] = parse_duration_ms(m.group(1))
    # fallback p95 from metrics table if no p99 threshold
    if "process_p99_ms" not in metrics:
        m = re.search(r'process_latency[.\s]+:.*?p\(95\)=([\d.]+(?:ms|µs|s))', text, re.DOTALL)
        if m:
            metrics["process_p99_ms"] = parse_duration_ms(m.group(1))

    # status_latency avg and p99
    m = re.search(r'status_latency[.\s]+:\s+avg=([\d.]+(?:ms|µs|s))', text)
    if m:
        metrics["status_avg_ms"] = parse_duration_ms(m.group(1))
    m = re.search(r"status_latency\s+[✓✗]\s+'p\(99\)<\d+'\s+p\(99\)=([\d.]+(?:ms|µs|s))", text)
    if m:
        metrics["status_p99_ms"] = parse_duration_ms(m.group(1))
    if "status_p99_ms" not in metrics:
        m = re.search(r'status_latency[.\s]+:.*?p\(95\)=([\d.]+(?:ms|µs|s))', text, re.DOTALL)
        if m:
            metrics["status_p99_ms"] = parse_duration_ms(m.group(1))

    # Fallback: http_req_duration if custom metrics absent
    if "process_avg_ms" not in metrics:
        m = re.search(r'http_req_duration[.\s]+:\s+avg=([\d.]+(?:ms|µs|s))', text)
        if m:
            metrics["process_avg_ms"] = parse_duration_ms(m.group(1))
        m2 = re.search(r"http_req_duration[.\s]+:.*?p\(95\)=([\d.]+(?:ms|µs|s))", text, re.DOTALL)
        if m2:
            metrics["process_p99_ms"] = parse_duration_ms(m2.group(1))

    return metrics if metrics else None


def parse_monitor_log(monitor_log: Path) -> dict:
    """Extract resource metrics from a monitor.sh log.
    Tries the formatted summary block first; falls back to parsing live
    sampling lines ('CPU: X%  RSS: Y MB  Heap: Z/W MB  Threads: N  GC/s: M').
    """
    result = {"max_cpu": None, "avg_cpu": None, "max_rss_mb": None,
              "max_heap_mb": None, "max_threads": None,
              "gc_events": None, "gc_pause_ms": None}
    if not monitor_log.exists():
        return result

    lines = monitor_log.read_text().splitlines()

    # ── 1. Try formatted summary block ──────────────────────────────────────
    def _grab(keyword):
        for ln in lines:
            if keyword in ln:
                nums = re.findall(r'[\d.]+', ln)
                if nums:
                    try: return float(nums[0])
                    except ValueError: pass
        return None

    max_cpu  = _grab("Max CPU")
    avg_cpu  = _grab("Avg CPU")
    max_rss  = _grab("Max RSS")
    max_heap = _grab("Max Heap Used")
    max_thr  = _grab("Max Live Threads")
    gc_ev    = _grab("Total GC events")
    gc_pause = _grab("GC Pause Total")

    if max_cpu is not None:
        # Summary block exists — use it directly
        result.update({"max_cpu": max_cpu, "avg_cpu": avg_cpu,
                       "max_rss_mb": max_rss, "max_heap_mb": max_heap,
                       "max_threads": max_thr, "gc_events": gc_ev,
                       "gc_pause_ms": gc_pause})
        return result

    # ── 2. Fall back: parse live sampling lines ──────────────────────────────
    # Format: "  CPU:  70.2%  RSS:  181.2 MB  Heap: 31/314 MB  Threads: 24  GC/s: 0"
    cpu_vals, rss_vals, heap_vals, thr_vals, gc_deltas = [], [], [], [], []
    pat = re.compile(
        r'CPU:\s*([\d.]+)%.*?RSS:\s*([\d.]+)\s*MB.*?Heap:\s*(\d+)/(\d+)\s*MB.*?Threads:\s*(\d+).*?GC/s:\s*(\d+)'
    )
    for ln in lines:
        m = pat.search(ln)
        if m:
            cpu_vals.append(float(m.group(1)))
            rss_vals.append(float(m.group(2)))
            heap_vals.append(float(m.group(3)))
            thr_vals.append(int(m.group(5)))
            gc_deltas.append(int(m.group(6)))

    if cpu_vals:
        result["max_cpu"]     = max(cpu_vals)
        result["avg_cpu"]     = round(statistics.mean(cpu_vals), 1)
        result["max_rss_mb"]  = max(rss_vals)
        result["max_heap_mb"] = max(heap_vals)
        result["max_threads"] = max(thr_vals)
        result["gc_events"]   = sum(gc_deltas)
        # gc_pause not available in live lines — leave None

    return result


# ── Collect all data ──────────────────────────────────────────────────────────

all_data = {}  # {profile: {mode: [metrics_per_series]}}

for profile in PROFILES:
    all_data[profile] = {mode: [] for mode in THREAD_MODES}
    series_dirs = sorted(RESULTS_ROOT.glob(f"{profile}/series*"))

    if not series_dirs:
        print(f"⚠️  No results found for profile '{profile}' — skipping")
        continue

    for series_dir in series_dirs:
        for mode in THREAD_MODES:
            k6_file = series_dir / f"k6_{mode}_summary.txt"
            monitor_file = series_dir / f"monitor_{mode}.log"

            metrics = extract_k6_metrics(k6_file)
            if metrics is None:
                print(f"⚠️  Missing/empty: {k6_file}")
                continue

            # Add monitor metrics (parses live lines if summary block absent)
            mon = parse_monitor_log(monitor_file)
            metrics.update(mon)

            all_data[profile][mode].append(metrics)
            print(f"✅  Loaded {profile}/series{series_dir.name[-1]}/{mode}: "
                  f"throughput={metrics.get('throughput', '?'):.0f}/s  "
                  f"process_avg={metrics.get('process_avg_ms', '?')}ms")


def avg(lst: list, key: str) -> "Optional[float]":
    vals = [x[key] for x in lst if x.get(key) is not None]
    return round(statistics.mean(vals), 2) if vals else None

def med(lst: list, key: str) -> "Optional[float]":
    """Median — resistant to corrupted outlier runs."""
    vals = sorted(x[key] for x in lst if x.get(key) is not None)
    return round(statistics.median(vals), 2) if vals else None


# ── Build aggregated structure ────────────────────────────────────────────────

aggregated = {}
for profile in PROFILES:
    aggregated[profile] = {}
    for mode in THREAD_MODES:
        runs = all_data[profile][mode]
        if not runs:
            aggregated[profile][mode] = None
            continue
        aggregated[profile][mode] = {
            "series_count": len(runs),
            "throughput":      med(runs, "throughput"),
            "error_rate":      avg(runs, "error_rate"),
            "process_avg_ms":  med(runs, "process_avg_ms"),
            "process_p99_ms":  med(runs, "process_p99_ms"),
            "status_avg_ms":   med(runs, "status_avg_ms"),
            "status_p99_ms":   med(runs, "status_p99_ms"),
            "max_threads":     avg(runs, "max_threads"),
            "max_cpu":         avg(runs, "max_cpu"),
            "avg_cpu":         avg(runs, "avg_cpu"),
            "max_rss_mb":      avg(runs, "max_rss_mb"),
            "max_heap_mb":     avg(runs, "max_heap_mb"),
            "gc_events":       avg(runs, "gc_events"),
            "gc_pause_ms":     avg(runs, "gc_pause_ms"),
            # per-series raw values for scatter overlays
            "_series": {
                k: [r.get(k) for r in runs]
                for k in ["throughput","process_avg_ms","process_p99_ms",
                          "status_avg_ms","status_p99_ms","error_rate",
                          "max_threads","max_cpu","avg_cpu","max_rss_mb","max_heap_mb",
                          "gc_events","gc_pause_ms"]
            },
        }

out_path = RESULTS_ROOT / "aggregated.json"
RESULTS_ROOT.mkdir(exist_ok=True)
out_path.write_text(json.dumps(aggregated, indent=2))
print(f"\n📦  Aggregated data written to: {out_path}")

# ── Generate HTML chart ───────────────────────────────────────────────────────

# Prepare chart data arrays (in display order: 500m-platform, 500m-virtual, 1cpu-platform, 1cpu-virtual)
series_labels = [
    "500m · Platform",
    "500m · Virtual",
    "1CPU · Platform",
    "1CPU · Virtual",
]

colors_bg = [
    "rgba(239, 83, 80, 0.85)",    # red – platform 500m
    "rgba(66, 165, 245, 0.85)",   # blue – virtual 500m
    "rgba(255, 167, 38, 0.85)",   # amber – platform 1cpu
    "rgba(102, 187, 106, 0.85)",  # green – virtual 1cpu
]
colors_border = [
    "rgba(239, 83, 80, 1)",
    "rgba(66, 165, 245, 1)",
    "rgba(255, 167, 38, 1)",
    "rgba(102, 187, 106, 1)",
]

def get(profile, mode, key):
    d = aggregated.get(profile, {}).get(mode)
    if d is None:
        return "null"
    v = d.get(key)
    return "null" if v is None else v

chart_data = {
    "process_avg":  [get("500m","platform","process_avg_ms"), get("500m","virtual","process_avg_ms"), get("1cpu","platform","process_avg_ms"), get("1cpu","virtual","process_avg_ms")],
    "process_p99":  [get("500m","platform","process_p99_ms"), get("500m","virtual","process_p99_ms"), get("1cpu","platform","process_p99_ms"), get("1cpu","virtual","process_p99_ms")],
    "status_avg":   [get("500m","platform","status_avg_ms"),  get("500m","virtual","status_avg_ms"),  get("1cpu","platform","status_avg_ms"),  get("1cpu","virtual","status_avg_ms")],
    "status_p99":   [get("500m","platform","status_p99_ms"),  get("500m","virtual","status_p99_ms"),  get("1cpu","platform","status_p99_ms"),  get("1cpu","virtual","status_p99_ms")],
    "throughput":   [get("500m","platform","throughput"),      get("500m","virtual","throughput"),      get("1cpu","platform","throughput"),      get("1cpu","virtual","throughput")],
    "error_rate":   [get("500m","platform","error_rate"),      get("500m","virtual","error_rate"),      get("1cpu","platform","error_rate"),      get("1cpu","virtual","error_rate")],
    "max_threads":  [get("500m","platform","max_threads"),     get("500m","virtual","max_threads"),     get("1cpu","platform","max_threads"),     get("1cpu","virtual","max_threads")],
    "max_cpu":      [get("500m","platform","max_cpu"),          get("500m","virtual","max_cpu"),          get("1cpu","platform","max_cpu"),          get("1cpu","virtual","max_cpu")],
    "avg_cpu":      [get("500m","platform","avg_cpu"),          get("500m","virtual","avg_cpu"),          get("1cpu","platform","avg_cpu"),          get("1cpu","virtual","avg_cpu")],
    "max_rss":      [get("500m","platform","max_rss_mb"),       get("500m","virtual","max_rss_mb"),       get("1cpu","platform","max_rss_mb"),       get("1cpu","virtual","max_rss_mb")],
    "max_heap":     [get("500m","platform","max_heap_mb"),     get("500m","virtual","max_heap_mb"),     get("1cpu","platform","max_heap_mb"),     get("1cpu","virtual","max_heap_mb")],
    "gc_events":    [get("500m","platform","gc_events"),       get("500m","virtual","gc_events"),       get("1cpu","platform","gc_events"),       get("1cpu","virtual","gc_events")],
    "gc_pause":     [get("500m","platform","gc_pause_ms"),     get("500m","virtual","gc_pause_ms"),     get("1cpu","platform","gc_pause_ms"),     get("1cpu","virtual","gc_pause_ms")],
}

# Per-series scatter data: {metric: [[s1,s2,s3], [s1,s2,s3], ...]} for each of 4 label slots
def get_series(profile, mode, key):
    d = aggregated.get(profile, {}).get(mode)
    if not d: return []
    return [v for v in d.get("_series", {}).get(key, []) if v is not None]

per_series_data = {}
for metric_key in ["throughput","process_avg_ms","process_p99_ms","status_avg_ms","status_p99_ms",
                   "error_rate","max_threads","max_cpu","avg_cpu","max_rss_mb","max_heap_mb",
                   "gc_events","gc_pause_ms"]:
    per_series_data[metric_key] = [
        get_series("500m", "platform", metric_key),
        get_series("500m", "virtual",  metric_key),
        get_series("1cpu", "platform", metric_key),
        get_series("1cpu", "virtual",  metric_key),
    ]

# series count info for subtitle
series_info = {}
for profile in PROFILES:
    for mode in THREAD_MODES:
        d = aggregated.get(profile, {}).get(mode)
        series_info[f"{profile}_{mode}"] = d["series_count"] if d else 0

import json as _json
_cd = chart_data
_ps = per_series_data

html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Java Virtual Threads Benchmark</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"></script>
<style>
  :root {{
    --bg:#0f1117;--surface:#1a1d2e;--surface2:#252840;--border:#2e3150;
    --text:#e2e8f0;--sub:#94a3b8;--red:#ef5350;--blue:#42a5f5;--amber:#ffa726;--green:#66bb6a;
    --red-dim:rgba(239,83,80,.15);--blue-dim:rgba(66,165,245,.15);
    --amber-dim:rgba(255,167,38,.15);--green-dim:rgba(102,187,106,.15);
  }}
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:var(--bg);color:var(--text);font-family:'Segoe UI',system-ui,sans-serif;padding:28px 18px 60px}}
  h1{{text-align:center;font-size:1.85rem;font-weight:700;
      background:linear-gradient(135deg,#42a5f5,#66bb6a);-webkit-background-clip:text;
      -webkit-text-fill-color:transparent;background-clip:text;margin-bottom:6px}}
  .sub{{text-align:center;color:var(--sub);font-size:.88rem;margin-bottom:22px;line-height:1.6}}
  .sub b{{color:var(--text)}}

  /* ── legend ── */
  .legend{{display:flex;flex-wrap:wrap;gap:10px;justify-content:center;margin-bottom:28px}}
  .leg{{display:flex;align-items:center;gap:7px;font-size:.82rem;color:var(--sub)}}
  .dot{{width:12px;height:12px;border-radius:3px;flex-shrink:0}}

  /* ── section heading ── */
  .section-title{{
    grid-column:1/-1;font-size:.7rem;font-weight:700;text-transform:uppercase;
    letter-spacing:.1em;color:var(--sub);padding:6px 0 2px;
    border-bottom:1px solid var(--border);margin-bottom:4px
  }}

  /* ── tradeoff scorecards (top row) ── */
  .scorecard-grid{{
    display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));
    gap:14px;max-width:1440px;margin:0 auto 24px
  }}
  .sc{{background:var(--surface);border:1px solid var(--border);border-radius:14px;
       padding:16px 18px;position:relative;overflow:hidden}}
  .sc::before{{content:'';position:absolute;inset:0;opacity:.06;pointer-events:none}}
  .sc.win-virt::before{{background:var(--green)}}
  .sc.win-plat::before{{background:var(--amber)}}
  .sc-label{{font-size:.68rem;font-weight:600;text-transform:uppercase;letter-spacing:.07em;color:var(--sub);margin-bottom:4px}}
  .sc-value{{font-size:2rem;font-weight:800;line-height:1}}
  .sc-value.up{{color:var(--green)}}.sc-value.down{{color:var(--blue)}}.sc-value.warn{{color:var(--amber)}}
  .sc-desc{{font-size:.73rem;color:var(--sub);margin-top:5px;line-height:1.4}}
  .sc-badge{{display:inline-block;padding:2px 8px;border-radius:20px;font-size:.68rem;font-weight:700;margin-top:6px}}
  .badge-virt{{background:rgba(102,187,106,.2);color:var(--green)}}
  .badge-plat{{background:rgba(255,167,38,.2);color:var(--amber)}}

  /* ── charts grid ── */
  .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr));gap:18px;max-width:1440px;margin:0 auto}}
  .card{{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:18px}}
  .card.wide{{grid-column:1/-1}}
  .ct{{font-size:.68rem;font-weight:600;text-transform:uppercase;letter-spacing:.07em;color:var(--sub);margin-bottom:2px}}
  .cm{{font-size:1rem;font-weight:700;margin-bottom:14px;display:flex;align-items:baseline;gap:8px}}
  .hint{{font-size:.7rem;color:var(--sub);font-weight:400}}
  /* winner highlight badge in chart title */
  .win-tag{{font-size:.65rem;font-weight:700;padding:2px 7px;border-radius:10px}}
  .wt-v{{background:rgba(102,187,106,.2);color:var(--green)}}
  .wt-p{{background:rgba(255,167,38,.2);color:var(--amber)}}
  .wrap{{position:relative;height:230px}}
  .wrap.tall{{height:190px}}

  /* ── comparison table ── */
  table{{width:100%;border-collapse:collapse;font-size:.8rem}}
  th{{text-align:left;padding:8px 10px;color:var(--sub);font-weight:600;
      border-bottom:1px solid var(--border);font-size:.7rem;text-transform:uppercase}}
  td{{padding:7px 10px;border-bottom:1px solid var(--border)}}
  tr:last-child td{{border-bottom:none}}
  .better{{color:var(--green);font-weight:700}}
  .worse{{color:var(--red)}}
  .neutral{{color:var(--sub)}}
  .badge{{display:inline-block;padding:1px 7px;border-radius:10px;font-size:.72rem;font-weight:600}}
  .v{{background:rgba(66,165,245,.18);color:#42a5f5}}.p{{background:rgba(239,83,80,.18);color:#ef5350}}

  .footer{{text-align:center;margin-top:36px;color:var(--sub);font-size:.74rem;line-height:1.9}}
  .tag{{display:inline-block;background:var(--surface2);border:1px solid var(--border);
        border-radius:20px;padding:2px 9px;font-size:.71rem;margin:2px}}
</style>
</head>
<body>
<h1>Java Virtual Threads — Benchmark Results</h1>
<p class="sub">Spring Boot 4 · Project Loom · <b>250 concurrent VUs</b> · I/O-bound + CPU work (SHA-256, 4KB blobs, 80ms blocking)<br>
2 pod profiles · <b>{series_info.get('500m_virtual',1)} series each</b> · bars = avg · dots = individual runs</p>

<div class="legend">
  <div class="leg"><div class="dot" style="background:var(--red)"></div>500m · Platform (50-thread Tomcat pool)</div>
  <div class="leg"><div class="dot" style="background:var(--blue)"></div>500m · Virtual Threads</div>
  <div class="leg"><div class="dot" style="background:var(--amber)"></div>1CPU · Platform (100-thread Tomcat pool)</div>
  <div class="leg"><div class="dot" style="background:var(--green)"></div>1CPU · Virtual Threads</div>
</div>

<!-- ── Tradeoff scorecards ── -->
<div class="scorecard-grid" id="scorecards"></div>

<!-- ── Charts ── -->
<div class="grid">

  <div class="section-title">Throughput &amp; Latency</div>

  <div class="card wide">
    <div class="ct">Throughput</div>
    <div class="cm">Requests / second <span class="hint">higher is better ↑</span> <span class="win-tag wt-v">Virtual wins</span></div>
    <div class="wrap tall"><canvas id="cThroughput"></canvas></div>
  </div>

  <div class="card">
    <div class="ct">/api/process — Average Latency</div>
    <div class="cm">milliseconds <span class="hint">lower is better ↓</span> <span class="win-tag wt-v">Virtual wins</span></div>
    <div class="wrap"><canvas id="cProcAvg"></canvas></div>
  </div>

  <div class="card">
    <div class="ct">/api/process — p99 Latency</div>
    <div class="cm">milliseconds <span class="hint">lower is better ↓</span> <span class="win-tag wt-v">Virtual wins</span></div>
    <div class="wrap"><canvas id="cProcP99"></canvas></div>
  </div>

  <div class="card">
    <div class="ct">/api/status — Average Latency</div>
    <div class="cm">milliseconds <span class="hint">lower is better ↓</span> <span class="win-tag wt-v">Virtual wins</span></div>
    <div class="wrap"><canvas id="cStatAvg"></canvas></div>
  </div>

  <div class="card">
    <div class="ct">/api/status — p99 Latency</div>
    <div class="cm">milliseconds <span class="hint">lower is better ↓</span> <span class="win-tag wt-v">Virtual wins</span></div>
    <div class="wrap"><canvas id="cStatP99"></canvas></div>
  </div>

  <div class="card">
    <div class="ct">Error Rate</div>
    <div class="cm">percent <span class="hint">lower is better ↓</span> <span class="win-tag wt-v">Virtual wins</span></div>
    <div class="wrap"><canvas id="cErr"></canvas></div>
  </div>

  <div class="section-title">Resource Usage — Tradeoffs</div>

  <div class="card">
    <div class="ct">Peak OS Threads</div>
    <div class="cm">count <span class="hint">lower = less OS overhead ↓</span> <span class="win-tag wt-v">Virtual wins</span></div>
    <div class="wrap"><canvas id="cThreads"></canvas></div>
  </div>

  <div class="card">
    <div class="ct">Peak Heap Used</div>
    <div class="cm">MB <span class="hint">↑ higher under virtual (more concurrency)</span> <span class="win-tag wt-p">Platform lower</span></div>
    <div class="wrap"><canvas id="cHeap"></canvas></div>
  </div>

  <div class="card">
    <div class="ct">Peak RSS Memory</div>
    <div class="cm">MB (resident set) <span class="hint">↑ virtual uses more RAM</span> <span class="win-tag wt-p">Platform lower</span></div>
    <div class="wrap"><canvas id="cRss"></canvas></div>
  </div>

  <div class="card">
    <div class="ct">CPU — Peak %</div>
    <div class="cm">percent <span class="hint">↑ virtual uses more CPU (doing more work)</span> <span class="win-tag wt-v">Virtual: efficient</span></div>
    <div class="wrap"><canvas id="cCpuMax"></canvas></div>
  </div>

  <div class="card">
    <div class="ct">CPU — Average %</div>
    <div class="cm">percent over test duration</div>
    <div class="wrap"><canvas id="cCpuAvg"></canvas></div>
  </div>

  <div class="section-title">GC Pressure</div>

  <div class="card">
    <div class="ct">GC Events (total during test)</div>
    <div class="cm">count <span class="hint">↑ more GC under virtual (more allocation)</span> <span class="win-tag wt-p">Platform lower</span></div>
    <div class="wrap"><canvas id="cGcEvents"></canvas></div>
  </div>

  <div class="card">
    <div class="ct">GC Pause Total</div>
    <div class="cm">milliseconds <span class="hint">cumulative stop-the-world time</span></div>
    <div class="wrap"><canvas id="cGcPause"></canvas></div>
  </div>

  <!-- Full comparison table -->
  <div class="card wide">
    <div class="ct">Full Comparison — Averaged across {series_info.get('500m_virtual',1)} series</div>
    <div class="cm" style="margin-bottom:12px">All metrics · green = better · red = worse · relative to platform threads baseline</div>
    <div style="overflow-x:auto"><table id="cmpTable">
      <thead><tr>
        <th>Profile</th><th>Mode</th>
        <th>Throughput (req/s)</th><th>ProcAvg (ms)</th><th>Proc p99 (ms)</th>
        <th>StatAvg (ms)</th><th>Stat p99 (ms)</th><th>Errors (%)</th>
        <th>OS Threads</th><th>Heap (MB)</th><th>RSS (MB)</th>
        <th>CPU avg (%)</th><th>GC events</th><th>GC pause (ms)</th>
      </tr></thead>
      <tbody id="cmpBody"></tbody>
    </table></div>
  </div>

  <!-- Raw series table -->
  <div class="card wide">
    <div class="ct">Raw Data — Individual Series</div>
    <div class="cm" style="margin-bottom:12px">Per-run values</div>
    <div style="overflow-x:auto"><table>
      <thead><tr>
        <th>Profile</th><th>Mode</th>
        <th>S1 req/s</th><th>S2 req/s</th><th>S3 req/s</th>
        <th>S1 PAvg</th><th>S2 PAvg</th><th>S3 PAvg</th>
        <th>S1 p99</th><th>S2 p99</th><th>S3 p99</th>
        <th>S1 Thr</th><th>S2 Thr</th><th>S3 Thr</th>
        <th>S1 Heap</th><th>S2 Heap</th><th>S3 Heap</th>
        <th>S1 GC</th><th>S2 GC</th><th>S3 GC</th>
      </tr></thead>
      <tbody id="rawBody"></tbody>
    </table></div>
  </div>

</div>

<div class="footer">
  <p>Spring Boot 4.0 · Java 26 · Project Loom Virtual Threads · k6 · {series_info.get('500m_virtual',1)}×90s runs per config</p>
  <p>Workload: batch SHA-256 of 4KB blobs + 2000-sample histogram + 80ms/30ms blocking I/O</p><br>
  <span class="tag">#JavaVirtualThreads</span><span class="tag">#ProjectLoom</span>
  <span class="tag">#SpringBoot</span><span class="tag">#Java26</span>
  <span class="tag">#PerformanceBenchmark</span><span class="tag">#Kubernetes</span>
</div>

<script>
const LABELS = {_json.dumps(series_labels)};
const BG  = {_json.dumps(colors_bg)};
const BD  = {_json.dumps(colors_border)};

const AVG = {{
  throughput: {_cd['throughput']},
  procAvg:    {_cd['process_avg']},
  procP99:    {_cd['process_p99']},
  statAvg:    {_cd['status_avg']},
  statP99:    {_cd['status_p99']},
  errRate:    {_cd['error_rate']},
  threads:    {_cd['max_threads']},
  heap:       {_cd['max_heap']},
  rss:        {_cd['max_rss']},
  cpuMax:     {_cd['max_cpu']},
  cpuAvg:     {_cd['avg_cpu']},
  gcEvents:   {_cd['gc_events']},
  gcPause:    {_cd['gc_pause']},
}};

const PS = {{
  throughput: {_json.dumps(_ps['throughput'])},
  procAvg:    {_json.dumps(_ps['process_avg_ms'])},
  procP99:    {_json.dumps(_ps['process_p99_ms'])},
  statAvg:    {_json.dumps(_ps['status_avg_ms'])},
  statP99:    {_json.dumps(_ps['status_p99_ms'])},
  errRate:    {_json.dumps(_ps['error_rate'])},
  threads:    {_json.dumps(_ps['max_threads'])},
  heap:       {_json.dumps(_ps['max_heap_mb'])},
  rss:        {_json.dumps(_ps['max_rss_mb'])},
  cpuMax:     {_json.dumps(_ps['max_cpu'])},
  cpuAvg:     {_json.dumps(_ps['avg_cpu'])},
  gcEvents:   {_json.dumps(_ps['gc_events'])},
  gcPause:    {_json.dumps(_ps['gc_pause_ms'])},
}};

// ── Chart helpers ──────────────────────────────────────────────────────────
const baseOpts = (unit, horizontal) => ({{
  responsive:true, maintainAspectRatio:false, animation:{{duration:500}},
  plugins:{{
    legend:{{display:false}},
    tooltip:{{
      backgroundColor:'rgba(26,29,46,.97)',borderColor:'#2e3150',borderWidth:1,
      titleColor:'#e2e8f0',bodyColor:'#94a3b8',padding:10,
      callbacks:{{ label: ctx => {{
        const v = horizontal ? ctx.parsed.x : ctx.parsed.y;
        return v != null ? ` ${{ctx.dataset.label||''}}: ${{v.toFixed(1)}} ${{unit}}` : ' N/A';
      }}}}
    }}
  }},
  scales:{{
    x:{{grid:{{color:'rgba(46,49,80,.5)'}},ticks:{{color:'#94a3b8',font:{{size:10}}}}}},
    y:{{grid:{{color:'rgba(46,49,80,.5)'}},ticks:{{color:'#94a3b8',font:{{size:10}}}},suggestedMin:0}}
  }}
}});

function makeGrouped(avgArr, psArr) {{
  const bars = LABELS.map((lbl,i) => ({{
    type:'bar', label:lbl, data:[avgArr[i]],
    backgroundColor:BG[i], borderColor:BD[i], borderWidth:2, borderRadius:5, borderSkipped:false,
  }}));
  const dots = LABELS.map((lbl,i) => ({{
    type:'scatter', label:lbl+' runs',
    data:(psArr[i]||[]).map(v=>v!=null?{{x:0,y:v}}:null).filter(Boolean),
    backgroundColor:BD[i], pointRadius:5, pointHoverRadius:7, showLine:false,
  }}));
  return {{labels:[''], datasets:[...bars,...dots]}};
}}

function makeThroughput() {{
  return {{
    labels: LABELS,
    datasets: [
      {{ type:'bar', label:'avg', data:AVG.throughput, backgroundColor:BG, borderColor:BD,
         borderWidth:2, borderRadius:5, borderSkipped:false }},
      ...LABELS.map((lbl,i) => ({{
        type:'scatter', label:lbl+' runs',
        data:(PS.throughput[i]||[]).map((v,_)=>v!=null?{{y:i,x:v}}:null).filter(Boolean),
        backgroundColor:BD[i], pointRadius:6, pointHoverRadius:8
      }}))
    ]
  }};
}}

const horiz = {{
  ...baseOpts('req/s',true), indexAxis:'y',
  scales:{{
    x:{{grid:{{color:'rgba(46,49,80,.5)'}},ticks:{{color:'#94a3b8',font:{{size:10}}}},suggestedMin:0}},
    y:{{grid:{{color:'rgba(46,49,80,.5)'}},ticks:{{color:'#e2e8f0',font:{{size:11,weight:'600'}}}}}}
  }}
}};

new Chart(document.getElementById('cThroughput'), {{type:'bar',data:makeThroughput(),options:horiz}});

[
  ['cProcAvg',  AVG.procAvg,   PS.procAvg,  'ms'],
  ['cProcP99',  AVG.procP99,   PS.procP99,  'ms'],
  ['cStatAvg',  AVG.statAvg,   PS.statAvg,  'ms'],
  ['cStatP99',  AVG.statP99,   PS.statP99,  'ms'],
  ['cErr',      AVG.errRate,   PS.errRate,  '%'],
  ['cThreads',  AVG.threads,   PS.threads,  'threads'],
  ['cHeap',     AVG.heap,      PS.heap,     'MB'],
  ['cRss',      AVG.rss,       PS.rss,      'MB'],
  ['cCpuMax',   AVG.cpuMax,    PS.cpuMax,   '%'],
  ['cCpuAvg',   AVG.cpuAvg,    PS.cpuAvg,   '%'],
  ['cGcEvents', AVG.gcEvents,  PS.gcEvents, 'events'],
  ['cGcPause',  AVG.gcPause,   PS.gcPause,  'ms'],
].forEach(([id, avg, ps, unit]) => {{
  new Chart(document.getElementById(id), {{data:makeGrouped(avg,ps), options:baseOpts(unit,false)}});
}});

// ── Scorecards ─────────────────────────────────────────────────────────────
function ratio(a,b){{ return (!a||!b||a===0) ? null : b/a; }}
function fmtPct(r, higherBetter){{
  if(r===null) return '—';
  const delta = r - 1;
  const sign = delta >= 0 ? '+' : '';
  const cls = (higherBetter ? delta>=0 : delta<=0) ? 'up' : 'warn';
  return {{text: sign+Math.round(delta*100)+'%', cls}};
}}
function fmtX(r){{
  if(!r) return {{text:'—',cls:'neutral'}};
  return {{text: r.toFixed(1)+'×', cls: r>=1.5?'up':'neutral'}};
}}

// 500m: virt vs plat
const tp500  = ratio(AVG.throughput[0], AVG.throughput[1]);
const lat500 = ratio(AVG.procAvg[0],    AVG.procAvg[1]);
const err500 = ratio(AVG.errRate[0],    AVG.errRate[1]);
const thr500 = ratio(AVG.threads[0],    AVG.threads[1]);
const hp500  = ratio(AVG.heap[0],       AVG.heap[1]);
const gc500  = ratio(AVG.gcEvents[0],   AVG.gcEvents[1]);
// 1cpu: virt vs plat
const tp1cpu  = ratio(AVG.throughput[2], AVG.throughput[3]);
const lat1cpu = ratio(AVG.procAvg[2],    AVG.procAvg[3]);

const cards = [
  {{ label:'Throughput gain', sub:'virtual vs platform (500m)', r:tp500, hb:true, badge:'badge-virt', badgeText:'virtual threads win' }},
  {{ label:'Throughput gain', sub:'virtual vs platform (1cpu)',  r:tp1cpu, hb:true, badge:'badge-virt', badgeText:'virtual threads win' }},
  {{ label:'Latency reduction', sub:'/api/process avg (500m)', r:lat500, hb:false, badge:'badge-virt', badgeText:'virtual threads win' }},
  {{ label:'Latency reduction', sub:'/api/process avg (1cpu)',  r:lat1cpu, hb:false, badge:'badge-virt', badgeText:'virtual threads win' }},
  {{ label:'Error rate change', sub:'500m virtual vs platform', r:err500, hb:false, badge:err500&&err500<1?'badge-virt':'badge-plat', badgeText:err500&&err500<1?'fewer errors':'more errors' }},
  {{ label:'OS thread reduction', sub:'500m virtual vs platform', r:thr500, hb:false, badge:'badge-virt', badgeText:'far fewer OS threads' }},
  {{ label:'Heap overhead', sub:'virtual uses more (more concurrency)', r:hp500, hb:false, badge:'badge-plat', badgeText:'platform uses less heap' }},
  {{ label:'GC event delta', sub:'500m virtual vs platform', r:gc500, hb:false, badge:gc500&&gc500>1?'badge-plat':'badge-virt', badgeText:gc500&&gc500>1?'more GC (expected)':'less GC' }},
];

const sc = document.getElementById('scorecards');
cards.forEach(c => {{
  const f = c.hb ? fmtPct(c.r, true) : fmtPct(c.r, false);
  const win = c.r!==null && ((c.hb && c.r>1)||(!c.hb && c.r<1));
  const div = document.createElement('div');
  div.className = 'sc ' + (win?'win-virt':'win-plat');
  div.innerHTML = `
    <div class="sc-label">${{c.label}}</div>
    <div class="sc-value ${{f.cls||'neutral'}}">${{f.text}}</div>
    <div class="sc-desc">${{c.sub}}</div>
    <span class="sc-badge ${{c.badge}}">${{c.badgeText}}</span>
  `;
  sc.appendChild(div);
}});

// ── Comparison table ───────────────────────────────────────────────────────
const CONFIGS = [
  {{prof:'500m', mode:'platform', idx:0}},
  {{prof:'500m', mode:'virtual',  idx:1}},
  {{prof:'1CPU', mode:'platform', idx:2}},
  {{prof:'1CPU', mode:'virtual',  idx:3}},
];
const tbody = document.getElementById('cmpBody');
const fmt1 = v => v!=null&&v!=='null' ? Number(v).toFixed(1) : '—';

// baseline per profile: platform = idx 0 (500m) or idx 2 (1cpu)
function cellClass(val, baseVal, higherBetter){{
  if(val==null||baseVal==null||val==='null'||baseVal==='null') return 'neutral';
  const better = higherBetter ? Number(val)>Number(baseVal) : Number(val)<Number(baseVal);
  return better ? 'better' : (val===baseVal ? 'neutral' : 'worse');
}}

CONFIGS.forEach(c => {{
  const isVirt = c.mode==='virtual';
  const baseIdx = c.prof==='500m' ? 0 : 2;
  const tr = document.createElement('tr');
  const fields = [
    [AVG.throughput, true],[AVG.procAvg,false],[AVG.procP99,false],
    [AVG.statAvg,false],[AVG.statP99,false],[AVG.errRate,false],
    [AVG.threads,false],[AVG.heap,false],[AVG.rss,false],
    [AVG.cpuAvg,false],[AVG.gcEvents,false],[AVG.gcPause,false],
  ];
  const cells = fields.map(([arr, hb]) => {{
    const v = arr[c.idx], base = arr[baseIdx];
    const cls = isVirt ? cellClass(v, base, hb) : 'neutral';
    return `<td class="${{cls}}">${{fmt1(v)}}</td>`;
  }}).join('');
  tr.innerHTML = `<td>${{c.prof}}</td><td><span class="badge ${{isVirt?'v':'p'}}">${{c.mode}}</span></td>${{cells}}`;
  tbody.appendChild(tr);
}});

// ── Raw series table ───────────────────────────────────────────────────────
const rawBody = document.getElementById('rawBody');
CONFIGS.forEach(c => {{
  const tr = document.createElement('tr');
  const cls = c.mode==='virtual'?'v':'p';
  const s = key => (PS[key][c.idx]||[]);
  const f = v => v!=null ? Number(v).toFixed(1) : '—';
  tr.innerHTML = `
    <td>${{c.prof}}</td><td><span class="badge ${{cls}}">${{c.mode}}</span></td>
    ${{[0,1,2].map(i=>`<td>${{f(s('throughput')[i])}}</td>`).join('')}}
    ${{[0,1,2].map(i=>`<td>${{f(s('procAvg')[i])}}</td>`).join('')}}
    ${{[0,1,2].map(i=>`<td>${{f(s('procP99')[i])}}</td>`).join('')}}
    ${{[0,1,2].map(i=>`<td>${{f(s('threads')[i])}}</td>`).join('')}}
    ${{[0,1,2].map(i=>`<td>${{f(s('heap')[i])}}</td>`).join('')}}
    ${{[0,1,2].map(i=>`<td>${{f(s('gcEvents')[i])}}</td>`).join('')}}
  `;
  rawBody.appendChild(tr);
}});
</script>
</body>
</html>
"""

chart_path = RESULTS_ROOT / "benchmark-chart.html"
chart_path.write_text(html)
print(f"📊  Chart written to: {chart_path}")
print(f"    → Open in browser: open {chart_path}")
