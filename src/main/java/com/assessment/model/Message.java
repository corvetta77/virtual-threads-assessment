package com.assessment.model;

import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;

public record Message(
        String id,
        String payload,
        Instant createdAt,
        String source,
        int priority,
        List<String> tags,
        byte[] rawData) { // simulate a blob payload (e.g. serialized event body)

    private static final int BLOB_SIZE = 4096; // 4 KB per message

    public static Message create(String payload) {
        int seq = Integer.parseInt(payload.replaceAll("\\D+", "0"));
        // Allocate blob and fill with pseudo-content to force real heap pressure
        byte[] blob = new byte[BLOB_SIZE];
        for (int i = 0; i < BLOB_SIZE; i++)
            blob[i] = (byte) (seq ^ i);

        List<String> tags = new ArrayList<>();
        for (int i = 0; i < 8; i++)
            tags.add("tag-" + i + ":" + UUID.randomUUID());

        return new Message(
                UUID.randomUUID().toString(),
                payload + "::" + UUID.randomUUID() + "::" + UUID.randomUUID(),
                Instant.now(),
                "producer-" + (seq % 5),
                seq % 10,
                tags,
                blob);
    }
}
