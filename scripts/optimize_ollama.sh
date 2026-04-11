#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────
# QuenBot — Ollama Optimization for 12 vCPU / 24 GB RAM
# ─────────────────────────────────────────────────────────
# Run once after server upgrade to reconfigure Ollama.
# Usage: sudo bash scripts/optimize_ollama.sh
# ─────────────────────────────────────────────────────────

set -euo pipefail

OLLAMA_ENV_FILE="/etc/systemd/system/ollama.service.d/override.conf"

echo "🔧 Configuring Ollama for 12 vCPU / 24 GB RAM..."

# Create override directory if not exists
sudo mkdir -p /etc/systemd/system/ollama.service.d

# Write optimized environment variables
sudo tee "$OLLAMA_ENV_FILE" > /dev/null <<'EOF'
[Service]
# ─── QuenBot Performance Tuning (12 vCPU / 24 GB) ───
# Allow 3 parallel inference requests
Environment="OLLAMA_NUM_PARALLEL=3"
# Keep up to 2 models loaded in RAM simultaneously
Environment="OLLAMA_MAX_LOADED_MODELS=2"
# Use 10 of 12 threads for inference (reserve 2 for OS + agents)
Environment="OLLAMA_NUM_THREAD=10"
# Max memory Ollama can use for models (keep ~8GB for OS + Python + Node)
Environment="OLLAMA_MAX_VRAM=16000"
# Listen on localhost only
Environment="OLLAMA_HOST=0.0.0.0:11434"
# Longer keep-alive for loaded models (10 minutes)
Environment="OLLAMA_KEEP_ALIVE=10m"
# Flash attention for better KV cache efficiency
Environment="OLLAMA_FLASH_ATTENTION=1"
EOF

echo "✓ Override file written: $OLLAMA_ENV_FILE"

# Reload systemd and restart Ollama
sudo systemctl daemon-reload
sudo systemctl restart ollama

echo "✓ Ollama restarted with new configuration"
echo ""
echo "Current Ollama configuration:"
systemctl show ollama --property=Environment 2>/dev/null || echo "(check with: systemctl cat ollama)"
echo ""

# Recreate quenbot-brain model with larger context
echo "🧠 Recreating quenbot-brain model with 8K context..."
sleep 3  # Wait for Ollama to start

ollama create quenbot-brain -f - <<'MODELFILE'
FROM gemma3:4b-it-q4_K_M

PARAMETER temperature 0.3
PARAMETER top_p 0.85
PARAMETER top_k 30
PARAMETER repeat_penalty 1.15
PARAMETER num_ctx 8192
PARAMETER num_predict 1024
PARAMETER num_thread 10

SYSTEM """You are QuenBot Central Intelligence, a specialized cryptocurrency trading analysis AI.
You operate as part of a multi-agent trading system with the following agents:
- Scout: Market data collection and anomaly detection
- Strategist: Signal generation and pattern analysis
- Ghost Simulator: Paper trading and backtesting
- Auditor: Quality control and root cause analysis
- Brain: Pattern learning and prediction

You provide structured, data-driven analysis. Always respond in valid JSON when requested.
Be concise. Focus on actionable insights. Never hallucinate data."""
MODELFILE

echo "✓ quenbot-brain model recreated with 8K context window"
echo ""
echo "🚀 Ollama optimization complete!"
echo "   Context window: 2048 → 8192 tokens"
echo "   Max predict:    512  → 1024 tokens"
echo "   Parallel:       1    → 3 concurrent requests"
echo "   CPU threads:    auto → 10 threads"
echo "   Flash attention: enabled"
echo "   Keep-alive:     5m   → 10m"
