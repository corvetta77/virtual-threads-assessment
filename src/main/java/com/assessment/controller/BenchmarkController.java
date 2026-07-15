package com.assessment.controller;

import com.assessment.model.Message;
import com.assessment.service.MessageQueueService;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;
import java.util.*;
import java.util.stream.Collectors;

@RestController
@RequestMapping("/api")
public class BenchmarkController {

    private final MessageQueueService messageQueueService;

    public BenchmarkController(MessageQueueService messageQueueService) {
        this.messageQueueService = messageQueueService;
    }

    /**
     * Simulates a realistic message-processing pipeline:
     * - batch dequeue up to 20 messages (each carries 4KB blob)
     * - SHA-256 hash of each blob (CPU-intensive)
     * - enrich: map, filter, sort, group
     * - blocking I/O (DB write simulation)
     */
    @GetMapping("/process")
    public ResponseEntity<Map<String, Object>> process() throws InterruptedException {
        // 1. Batch dequeue
        List<Message> batch = createBatch();

        // 2. CPU-heavy: SHA-256 every blob + build enriched records
        List<Map<String, Object>> enriched = createEnriched(batch);

        // 3. Simulate blocking I/O (DB write / downstream HTTP call)
        Thread.sleep(80);

        // 4. Aggregate
        Map<String, Long> bySource = enriched.stream()
                .collect(Collectors.groupingBy(r -> (String) r.get("source"), Collectors.counting()));
        double avgAge = enriched.stream().mapToLong(r -> (long) r.get("ageMs")).average().orElse(0);

        return ResponseEntity.ok(Map.of(
                "status", batch.isEmpty() ? "empty_queue" : "processed",
                "batchSize", batch.size(),
                "avgMessageAgeMs", Math.round(avgAge),
                "bySource", bySource,
                "topMessage", enriched.isEmpty() ? Map.of() : enriched.get(0),
                "queueSize", messageQueueService.size(),
                "virtual", Thread.currentThread().isVirtual()));
    }

    private List<Map<String, Object>> createEnriched(List<Message> batch) {
        MessageDigest sha;
        try {
            sha = MessageDigest.getInstance("SHA-256");
        } catch (NoSuchAlgorithmException e) {
            throw new RuntimeException(e);
        }

        List<Map<String, Object>> enriched = batch.stream()
                .map(m -> {
                    // Hash the raw blob — real CPU work
                    sha.reset();
                    byte[] digest = sha.digest(m.rawData());
                    String hexDigest = HexFormat.of().formatHex(digest);

                    // String-heavy enrichment
                    String tagSummary = m.tags().stream()
                            .filter(t -> !t.startsWith("tag-0"))
                            .sorted()
                            .collect(Collectors.joining("|"));

                    Map<String, Object> r = new LinkedHashMap<>();
                    r.put("id", m.id());
                    r.put("payload", m.payload().toUpperCase());
                    r.put("source", m.source());
                    r.put("priority", m.priority());
                    r.put("ageMs", Instant.now().toEpochMilli() - m.createdAt().toEpochMilli());
                    r.put("blobHash", hexDigest);
                    r.put("tagSummary", tagSummary);
                    return r;
                })
                .filter(r -> (int) r.get("priority") >= 0)
                .sorted(Comparator.comparingInt(r -> -(int) r.get("priority")))
                .collect(Collectors.toList());
        return enriched;
    }

    private List<Message> createBatch() {
        List<Message> batch = new ArrayList<>();
        for (int i = 0; i < 20; i++) {
            Message m = messageQueueService.dequeue();
            if (m == null)
                break;
            batch.add(m);
        }
        return batch;
    }

    /**
     * Simulates a stats/dashboard read with in-memory analytics:
     * - allocate + sort a 2000-sample window (heap pressure)
     * - compute histogram, percentiles
     * - light blocking I/O
     */
    @GetMapping("/status")
    public ResponseEntity<Map<String, Object>> status() throws InterruptedException {
        long enqueued = messageQueueService.getTotalEnqueued();
        long dequeued = messageQueueService.getTotalDequeued();

        // Allocate a large list to create heap/GC pressure
        List<Long> latencies = generateLatencies(enqueued);

        // Build 20-bucket histogram
        Map<String, Long> histogram = buildHistogram(latencies);

        // Simulate blocking I/O (cache lookup)
        Thread.sleep(30);

        return ResponseEntity.ok(Map.of(
                "queueSize", messageQueueService.size(),
                "totalEnqueued", enqueued,
                "totalDequeued", dequeued,
                "lag", enqueued - dequeued,
                "p50LatencyMs", latencies.get(1000),
                "p95LatencyMs", latencies.get(1899),
                "p99LatencyMs", latencies.get(1979),
                "latencyHistogram", histogram,
                "virtual", Thread.currentThread().isVirtual()));
    }

    private Map<String, Long> buildHistogram(List<Long> latencies) {
        long min = latencies.get(0), max = latencies.get(latencies.size() - 1);
        long step = Math.max(1, (max - min) / 20);
        Map<String, Long> histogram = new LinkedHashMap<>();
        for (int b = 0; b < 20; b++) {
            long lo = min + (long) b * step, hi = lo + step;
            final long flo = lo, fhi = hi;
            histogram.put(lo + "-" + hi, latencies.stream().filter(v -> v >= flo && v < fhi).count());
        }
        return histogram;
    }

    private List<Long> generateLatencies(long enqueued) {
        Random rng = new Random(enqueued);
        List<Long> latencies = new ArrayList<>(2000);
        for (int i = 0; i < 2000; i++)
            latencies.add((long) (rng.nextGaussian() * 40 + 80));
        Collections.sort(latencies);
        return latencies;
    }
}
