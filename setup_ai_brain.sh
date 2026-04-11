#!/usr/bin/env bash
###############################################################################
# QuenBot V2 — AI Brain Setup Script
# Target:  Ubuntu 24.04 / 4 vCPU / 8 GB RAM / No GPU (Nuremberg Cloud)
# Installs Ollama, pulls a quantized model, configures swap & CPU optimization
###############################################################################
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log()  { echo -e "${GREEN}[✓]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
err()  { echo -e "${RED}[✗]${NC} $*"; }
info() { echo -e "${CYAN}[i]${NC} $*"; }

SWAP_SIZE_GB=4
OLLAMA_PORT=11434
MODEL_PRIMARY="gemma3:4b-it-q4_K_M"
MODEL_FALLBACK="qwen3:1.7b"

###############################################################################
# 1. System Requirements Check
###############################################################################
check_system() {
    info "Checking system requirements..."

    TOTAL_RAM_KB=$(grep MemTotal /proc/meminfo | awk '{print $2}')
    TOTAL_RAM_GB=$((TOTAL_RAM_KB / 1024 / 1024))
    CPU_CORES=$(nproc)

    log "Detected: ${CPU_CORES} vCPUs, ${TOTAL_RAM_GB} GB RAM"

    if [ "$TOTAL_RAM_GB" -lt 4 ]; then
        err "Minimum 4 GB RAM required. Detected: ${TOTAL_RAM_GB} GB"
        exit 1
    fi
}

###############################################################################
# 2. Swap File Configuration (OOM Prevention)
###############################################################################
configure_swap() {
    info "Configuring ${SWAP_SIZE_GB}GB swap file..."

    CURRENT_SWAP_KB=$(grep SwapTotal /proc/meminfo | awk '{print $2}')
    CURRENT_SWAP_GB=$((CURRENT_SWAP_KB / 1024 / 1024))

    if [ "$CURRENT_SWAP_GB" -ge "$SWAP_SIZE_GB" ]; then
        log "Swap already configured: ${CURRENT_SWAP_GB} GB. Skipping."
        return 0
    fi

    SWAPFILE="/swapfile_quenbot"

    if [ -f "$SWAPFILE" ]; then
        warn "Swap file exists, removing old one..."
        sudo swapoff "$SWAPFILE" 2>/dev/null || true
        sudo rm -f "$SWAPFILE"
    fi

    sudo fallocate -l "${SWAP_SIZE_GB}G" "$SWAPFILE"
    sudo chmod 600 "$SWAPFILE"
    sudo mkswap "$SWAPFILE"
    sudo swapon "$SWAPFILE"

    # Persist across reboots
    if ! grep -q "$SWAPFILE" /etc/fstab 2>/dev/null; then
        echo "${SWAPFILE} none swap sw 0 0" | sudo tee -a /etc/fstab > /dev/null
    fi

    # Optimize swappiness for LLM workloads (low = prefer RAM)
    sudo sysctl vm.swappiness=10
    echo "vm.swappiness=10" | sudo tee -a /etc/sysctl.conf > /dev/null 2>&1 || true

    log "Swap configured: ${SWAP_SIZE_GB} GB at ${SWAPFILE}"
}

###############################################################################
# 3. CPU Optimization for LLM Inference
###############################################################################
optimize_cpu() {
    info "Applying CPU optimizations for LLM inference..."

    # Set CPU governor to performance if available
    if command -v cpufreq-set &>/dev/null; then
        for cpu in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
            echo "performance" | sudo tee "$cpu" > /dev/null 2>&1 || true
        done
        log "CPU governor set to performance"
    else
        warn "cpufreq-set not available (VM likely controls governor)"
    fi

    # Increase file descriptor limits
    if ! grep -q "quenbot" /etc/security/limits.conf 2>/dev/null; then
        echo "# quenbot LLM limits" | sudo tee -a /etc/security/limits.conf > /dev/null
        echo "* soft nofile 65536" | sudo tee -a /etc/security/limits.conf > /dev/null
        echo "* hard nofile 65536" | sudo tee -a /etc/security/limits.conf > /dev/null
        log "File descriptor limits raised"
    fi

    # Transparent Huge Pages — disable for more predictable latency
    if [ -f /sys/kernel/mm/transparent_hugepage/enabled ]; then
        echo "madvise" | sudo tee /sys/kernel/mm/transparent_hugepage/enabled > /dev/null 2>&1 || true
    fi

    log "CPU optimizations applied"
}

###############################################################################
# 4. Install Ollama
###############################################################################
install_ollama() {
    info "Installing Ollama..."

    if command -v ollama &>/dev/null; then
        CURRENT_VERSION=$(ollama --version 2>/dev/null || echo "unknown")
        log "Ollama already installed: ${CURRENT_VERSION}"
    else
        curl -fsSL https://ollama.com/install.sh | sh
        log "Ollama installed successfully"
    fi

    # Configure Ollama for CPU-only, memory-constrained env
    sudo mkdir -p /etc/systemd/system/ollama.service.d

    cat <<'OLLAMA_OVERRIDE' | sudo tee /etc/systemd/system/ollama.service.d/override.conf > /dev/null
[Service]
Environment="OLLAMA_HOST=0.0.0.0:11434"
Environment="OLLAMA_NUM_PARALLEL=1"
Environment="OLLAMA_MAX_LOADED_MODELS=1"
Environment="OLLAMA_KEEP_ALIVE=10m"
Environment="OLLAMA_FLASH_ATTENTION=1"
LimitNOFILE=65536
OLLAMA_OVERRIDE

    sudo systemctl daemon-reload
    sudo systemctl enable ollama
    sudo systemctl restart ollama

    # Wait for Ollama to be ready
    info "Waiting for Ollama to start..."
    for i in $(seq 1 30); do
        if curl -sf http://localhost:${OLLAMA_PORT}/api/tags > /dev/null 2>&1; then
            log "Ollama is running on port ${OLLAMA_PORT}"
            return 0
        fi
        sleep 2
    done

    err "Ollama failed to start within 60 seconds"
    sudo journalctl -u ollama --no-pager -n 20
    exit 1
}

###############################################################################
# 5. Pull Quantized Model
###############################################################################
pull_model() {
    info "Pulling quantized model for 8GB RAM instance..."

    # Try primary model first (smallest, best for CPU)
    if ollama pull "$MODEL_PRIMARY" 2>/dev/null; then
        log "Model pulled: ${MODEL_PRIMARY}"
        ACTIVE_MODEL="$MODEL_PRIMARY"
    else
        warn "Primary model failed, trying fallback..."
        if ollama pull "$MODEL_FALLBACK" 2>/dev/null; then
            log "Fallback model pulled: ${MODEL_FALLBACK}"
            ACTIVE_MODEL="$MODEL_FALLBACK"
        else
            err "Failed to pull any model. Check network/disk space."
            exit 1
        fi
    fi

    # Create QuenBot-specific Modelfile with optimized parameters
    MODELFILE_PATH="/tmp/quenbot_modelfile"
    cat > "$MODELFILE_PATH" <<EOF
FROM ${ACTIVE_MODEL}

PARAMETER temperature 0.3
PARAMETER top_p 0.85
PARAMETER top_k 30
PARAMETER repeat_penalty 1.15
PARAMETER num_ctx 2048
PARAMETER num_thread $(nproc)
PARAMETER num_predict 512

SYSTEM """You are QuenBot Central Intelligence, a specialized cryptocurrency trading analysis AI.
You operate as part of a multi-agent trading system with the following agents:
- Scout: Market data collection and anomaly detection
- Strategist: Signal generation and pattern analysis
- Ghost Simulator: Paper trading and backtesting
- Auditor: Quality control and root cause analysis
- Brain: Pattern learning and prediction

You provide structured, data-driven analysis. Always respond in valid JSON when requested.
Be concise. Focus on actionable insights. Never hallucinate data."""
EOF

    ollama create quenbot-brain -f "$MODELFILE_PATH"
    rm -f "$MODELFILE_PATH"

    log "Custom model 'quenbot-brain' created with optimized parameters"
}

###############################################################################
# 6. Verify Installation
###############################################################################
verify_install() {
    info "Verifying installation..."

    # Test API endpoint
    RESPONSE=$(curl -sf http://localhost:${OLLAMA_PORT}/api/tags)
    if [ -z "$RESPONSE" ]; then
        err "Ollama API not responding"
        exit 1
    fi
    log "Ollama API responding"

    # Quick inference test
    info "Running inference test (this may take 30-60s on CPU)..."
    TEST_RESPONSE=$(curl -sf http://localhost:${OLLAMA_PORT}/api/generate \
        -d '{"model":"quenbot-brain","prompt":"Respond with only: OK","stream":false}' \
        --max-time 120 || echo "TIMEOUT")

    if echo "$TEST_RESPONSE" | grep -q "response"; then
        log "Inference test passed"
    else
        warn "Inference test slow or failed — model may need warmup"
    fi

    # Print summary
    echo ""
    echo "=============================================="
    echo -e "  ${GREEN}QuenBot AI Brain — Setup Complete${NC}"
    echo "=============================================="
    echo "  Ollama:      http://localhost:${OLLAMA_PORT}"
    echo "  Model:       quenbot-brain"
    echo "  CPU Threads: $(nproc)"
    echo "  Swap:        ${SWAP_SIZE_GB} GB"
    echo "  Swappiness:  10"
    echo "=============================================="
    echo ""
}

###############################################################################
# Main
###############################################################################
main() {
    echo ""
    echo "=============================================="
    echo "  QuenBot V2 — AI Brain Setup"
    echo "  Target: 4 vCPU / 8 GB RAM / CPU-only"
    echo "=============================================="
    echo ""

    check_system
    configure_swap
    optimize_cpu
    install_ollama
    pull_model
    verify_install

    log "Setup complete. Run 'python3 deploy_central_intelligence.py' next."
}

main "$@"
