#!/usr/bin/env bash
# run-platform-threads.sh
# Starts the app with virtual threads DISABLED (classic Tomcat thread pool).
set -euo pipefail

JAR=$(ls build/libs/virtual-threads-assessment-*.jar 2>/dev/null | head -1)
if [[ -z "$JAR" ]]; then
    echo "No JAR found — building first..."
    ./gradlew bootJar -q
    JAR=$(ls build/libs/virtual-threads-assessment-*.jar | head -1)
fi

echo "▶  Starting with PLATFORM THREADS  (spring.threads.virtual.enabled=false)"
echo "   JAR: $JAR"

exec java \
    -Xmx512m \
    -Dspring.threads.virtual.enabled=false \
    -jar "$JAR"
