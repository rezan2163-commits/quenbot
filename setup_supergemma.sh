#!/bin/bash
# ══════════════════════════════════════════════════════════════════
# SuperGemma-26B GGUF Model Setup Script
# ══════════════════════════════════════════════════════════════════
# Server: 16 vCPU / 32 GB RAM — CPU-only inference
# Model: gemma-2-27b-it Q4_K_M (GGUF) ~16GB
# Backend: llama-cpp-python (pip)
#
# Usage:
#   bash setup_supergemma.sh
# ══════════════════════════════════════════════════════════════════

set -e

MODELS_DIR="${QUENBOT_GGUF_MODEL_DIR:-/root/models}"
MODEL_FILE="gemma-2-27b-it-Q4_K_M.gguf"
MODEL_URL="https://huggingface.co/bartowski/gemma-2-27b-it-GGUF/resolve/main/gemma-2-27b-it-Q4_K_M.gguf"

echo "════════════════════════════════════════════"
echo "🧠 SuperGemma-26B GGUF Setup"
echo "════════════════════════════════════════════"

# ─── Phase 1: System Check ───
echo ""
echo "📋 Phase 1: System Check"
RAM_GB=$(awk '/MemTotal/ {printf "%.0f", $2/1024/1024}' /proc/meminfo 2>/dev/null || echo "0")
CPU_COUNT=$(nproc 2>/dev/null || echo "1")
echo "   RAM: ${RAM_GB}GB | CPU: ${CPU_COUNT} cores"

if [ "$RAM_GB" -lt 24 ]; then
    echo "⚠️  WARNING: ${RAM_GB}GB RAM detected. SuperGemma-26B Q4_K_M needs ~16GB RAM."
    echo "   Consider using Q3_K_L quantization or a smaller model."
fi

# ─── Phase 2: Install llama-cpp-python ───
echo ""
echo "📦 Phase 2: Installing llama-cpp-python..."
pip3 install llama-cpp-python==0.3.9 2>&1 | tail -3
echo "   ✅ llama-cpp-python installed"

# ─── Phase 3: Create model directory ───
echo ""
echo "📁 Phase 3: Model Directory"
mkdir -p "$MODELS_DIR"
echo "   Directory: $MODELS_DIR"

# ─── Phase 4: Download GGUF model ───
echo ""
echo "📥 Phase 4: Model Download"

if [ -f "$MODELS_DIR/$MODEL_FILE" ]; then
    FILE_SIZE=$(du -h "$MODELS_DIR/$MODEL_FILE" | cut -f1)
    echo "   ✅ Model already exists: $MODEL_FILE ($FILE_SIZE)"
else
    echo "   Downloading $MODEL_FILE (~16GB)..."
    echo "   URL: $MODEL_URL"
    echo "   This may take 30-60 minutes depending on connection speed."
    echo ""
    
    # Use wget with resume support
    if command -v wget &>/dev/null; then
        wget -c "$MODEL_URL" -O "$MODELS_DIR/$MODEL_FILE" --show-progress
    elif command -v curl &>/dev/null; then
        curl -L -C - "$MODEL_URL" -o "$MODELS_DIR/$MODEL_FILE" --progress-bar
    else
        echo "❌ Neither wget nor curl found. Install one and retry."
        exit 1
    fi
    
    FILE_SIZE=$(du -h "$MODELS_DIR/$MODEL_FILE" | cut -f1)
    echo "   ✅ Download complete: $MODEL_FILE ($FILE_SIZE)"
fi

# ─── Phase 5: Remove Ollama/Qwen (cleanup) ───
echo ""
echo "🗑️  Phase 5: Qwen/Ollama Cleanup"

# Stop Ollama if running
if pgrep -x ollama >/dev/null 2>&1; then
    echo "   Stopping Ollama..."
    killall ollama 2>/dev/null || true
    sleep 2
    echo "   ✅ Ollama stopped"
else
    echo "   Ollama not running"
fi

# Remove Ollama models (Qwen)
if [ -d "$HOME/.ollama/models" ]; then
    OLLAMA_SIZE=$(du -sh "$HOME/.ollama/models" 2>/dev/null | cut -f1)
    echo "   Removing Ollama models ($OLLAMA_SIZE)..."
    rm -rf "$HOME/.ollama/models"
    echo "   ✅ Ollama models removed"
else
    echo "   No Ollama model directory found"
fi

# Optionally remove Ollama binary
if command -v ollama &>/dev/null; then
    echo "   Removing Ollama binary..."
    rm -f /usr/local/bin/ollama 2>/dev/null || true
    echo "   ✅ Ollama binary removed"
fi

# ─── Phase 6: Swap/Memory Optimization ───
echo ""
echo "⚡ Phase 6: Memory Optimization"

SWAP_GB=$(free -g 2>/dev/null | awk '/Swap/ {print $2}' || echo "0")
if [ "$SWAP_GB" -lt 8 ]; then
    echo "   Creating 8GB swap file..."
    if [ -f /swapfile ]; then
        swapoff /swapfile 2>/dev/null || true
        rm -f /swapfile
    fi
    fallocate -l 8G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile >/dev/null
    swapon /swapfile
    echo "   ✅ 8GB swap enabled"
    
    # Persist swap
    if ! grep -q '/swapfile' /etc/fstab 2>/dev/null; then
        echo '/swapfile none swap sw 0 0' >> /etc/fstab
    fi
else
    echo "   ✅ Swap already ${SWAP_GB}GB"
fi

# Optimize kernel params for LLM
echo "   Tuning kernel parameters..."
sysctl -w vm.swappiness=10 >/dev/null 2>&1 || true
sysctl -w vm.overcommit_memory=1 >/dev/null 2>&1 || true

# ─── Phase 7: Verify ───
echo ""
echo "🔍 Phase 7: Verification"

python3 -c "
from llama_cpp import Llama
print('   ✅ llama-cpp-python import OK')
" 2>/dev/null || {
    echo "   ❌ llama-cpp-python import failed"
    exit 1
}

if [ -f "$MODELS_DIR/$MODEL_FILE" ]; then
    echo "   ✅ Model file exists: $MODELS_DIR/$MODEL_FILE"
else
    echo "   ❌ Model file missing!"
    exit 1
fi

echo ""
echo "════════════════════════════════════════════"
echo "✅ SuperGemma-26B Setup Complete!"
echo "════════════════════════════════════════════"
echo ""
echo "Model: $MODELS_DIR/$MODEL_FILE"
echo "Backend: llama-cpp-python (GGUF)"
echo "Quantization: Q4_K_M (~16GB RAM)"
echo "Context: 8192 tokens"
echo "Threads: $CPU_COUNT"
echo ""
echo "Next steps:"
echo "  1. pm2 restart quenbot-agents --update-env"
echo "  2. tail -f python_agents/agents.log | grep SuperGemma"
echo ""
