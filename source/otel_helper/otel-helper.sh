#!/bin/bash
# ABOUTME: Lightweight shell wrapper for otel-helper that checks file cache first
# ABOUTME: Avoids PyInstaller binary startup (~6s) on cache hit, falls back to full binary on miss
PROFILE="${AWS_PROFILE:-ClaudeCode}"
CACHE_DIR="$HOME/.claude-code-session"
CACHE_FILE="$CACHE_DIR/${PROFILE}-otel-headers.json"
RAW_FILE="$CACHE_DIR/${PROFILE}-otel-headers.raw"

if [ -f "$CACHE_FILE" ] && [ -f "$RAW_FILE" ]; then
    # Extract token_exp from metadata cache (date +%s is GNU/BSD, works on macOS and Linux)
    TOKEN_EXP=$(grep -o '"token_exp": *[0-9]*' "$CACHE_FILE" | grep -o '[0-9]*')
    NOW=$(date +%s)
    if [ -n "$TOKEN_EXP" ] && [ "$((TOKEN_EXP - NOW))" -gt 600 ]; then
        # Serve raw headers directly — no JSON parsing needed
        cat "$RAW_FILE"
        exit 0
    fi
fi

# Cache miss - fall back to full PyInstaller binary (which writes the cache)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$SCRIPT_DIR/otel-helper-bin" "$@"
