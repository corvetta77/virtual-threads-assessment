#!/usr/bin/env bash
# run-virtual-threads.sh
# Starts the app with virtual threads ENABLED (Loom virtual-thread-per-request).
set -euo pipefail

JAR=$(ls build/libs/virtual-threads-assessment-*.jar 2>/dev/null | head -1)
if [[ -z "$JAR" ]]; then
    echo "No JAR found — building first..."
    ./gradlew bootJar -q
    JAR=$(ls build/libs/virtual-threads-assessment-*.jar | head -1)
fi

echo "▶  Starting with VIRTUAL THREADS  (spring.threads.virtual.enabled=true)"
echo "   JAR: $JAR"

exec java \
    -Xmx512m \
    -Dspring.threads.virtual.enabled=true \
    -jar "$JAR"
