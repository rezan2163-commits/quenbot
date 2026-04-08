#!/usr/bin/env bash
set -euo pipefail

# ═══════════════════════════════════════════════════════
# QuenBot - One-Click Setup Script
# Target: Hetzner VPS (Ubuntu 22.04+, 8GB RAM, 4 vCPU)
# ═══════════════════════════════════════════════════════

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; NC='\033[0m'
log() { echo -e "${CYAN}[QuenBot]${NC} $1"; }
ok()  { echo -e "${GREEN}[✓]${NC} $1"; }
warn(){ echo -e "${YELLOW}[!]${NC} $1"; }
err() { echo -e "${RED}[✗]${NC} $1"; exit 1; }

log "QuenBot kurulum baslatiyor..."
log "Hedef: $(hostname) / $(uname -s) $(uname -m)"

# ─── 1. System packages ───
log "Sistem paketleri kontrol ediliyor..."
sudo apt-get update -qq
sudo apt-get install -y -qq curl git build-essential python3 python3-pip python3-venv docker.io docker-compose > /dev/null 2>&1
ok "Sistem paketleri hazir"

# ─── 2. Node.js (v20 LTS) ───
if ! command -v node &> /dev/null || [[ "$(node -v | cut -d. -f1 | cut -dv -f2)" -lt 20 ]]; then
  log "Node.js 20 LTS kuruluyor..."
  curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - > /dev/null 2>&1
  sudo apt-get install -y -qq nodejs > /dev/null 2>&1
fi
ok "Node.js $(node -v)"

# ─── 3. pnpm ───
if ! command -v pnpm &> /dev/null; then
  log "pnpm kuruluyor..."
  npm install -g pnpm > /dev/null 2>&1
fi
ok "pnpm $(pnpm -v)"

# ─── 4. PM2 ───
if ! command -v pm2 &> /dev/null; then
  log "PM2 kuruluyor..."
  npm install -g pm2 > /dev/null 2>&1
fi
ok "PM2 $(pm2 -v)"

# ─── 5. PostgreSQL (Docker) ───
log "PostgreSQL Docker konteyner kontrol ediliyor..."
if ! docker ps -q --filter "name=quenbot-postgres" | grep -q .; then
  if docker ps -aq --filter "name=quenbot-postgres" | grep -q .; then
    docker start quenbot-postgres
  else
    log "PostgreSQL konteyneri olusturuluyor..."
    docker run -d \
      --name quenbot-postgres \
      --restart unless-stopped \
      -e POSTGRES_USER=user \
      -e POSTGRES_PASSWORD=password \
      -e POSTGRES_DB=trade_intel \
      -p 5432:5432 \
      -v quenbot-pgdata:/var/lib/postgresql/data \
      postgres:16-alpine
  fi
  sleep 3
fi
ok "PostgreSQL calisiyor (port 5432)"

# ─── 6. Python Virtual Environment ───
cd "$(dirname "$0")"
PROJECT_DIR="$(pwd)"

log "Python sanal ortam hazirlaniyor..."
if [ ! -d "python_agents/.venv" ]; then
  python3 -m venv python_agents/.venv
fi
source python_agents/.venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -r python_agents/requirements.txt
deactivate
ok "Python bagimliliklar kuruldu"

# ─── 7. Node.js Dependencies ───
log "Node.js bagimliliklari kuruluyor..."
pnpm install --frozen-lockfile 2>/dev/null || pnpm install
ok "Node.js bagimliliklari hazir"

# ─── 8. Build Dashboard ───
log "Dashboard derleniyor..."
cd artifacts/market-intel
pnpm run build 2>/dev/null || npx vite build
cd "$PROJECT_DIR"
ok "Dashboard derlendi (dist/)"

# ─── 9. Create logs directory ───
mkdir -p logs
ok "Log dizini hazir"

# ─── 10. Create .env if not exists ───
if [ ! -f ".env" ]; then
  cp .env.example .env 2>/dev/null || cat > .env << 'EOF'
# QuenBot Environment Variables
DB_HOST=localhost
DB_PORT=5432
DB_USER=user
DB_PASSWORD=password
DB_NAME=trade_intel
ADMIN_PIN=BABA
PORT=3001
NODE_ENV=production
EOF
  warn ".env dosyasi olusturuldu - lufen kontrol edin"
fi

# ─── 11. Start with PM2 ───
log "PM2 ile baslatiyor..."
pm2 stop ecosystem.config.js 2>/dev/null || true
pm2 delete ecosystem.config.js 2>/dev/null || true

# Python venv'i aktivasyonu icin wrapper
cat > python_agents/start.sh << 'PYEOF'
#!/usr/bin/env bash
cd "$(dirname "$0")"
source .venv/bin/activate
exec python3 main.py
PYEOF
chmod +x python_agents/start.sh

# Update PM2 config to use the wrapper
pm2 start ecosystem.config.js
pm2 save

# ─── 12. PM2 startup (auto-start on reboot) ───
pm2 startup 2>/dev/null || warn "PM2 startup komutu icin 'sudo env PATH=\$PATH pm2 startup' calistirin"

echo ""
echo -e "${GREEN}═══════════════════════════════════════════════${NC}"
echo -e "${GREEN}  QuenBot basariyla kuruldu ve basladi!${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════${NC}"
echo ""
echo -e "  Dashboard:  ${CYAN}http://$(hostname -I | awk '{print $1}'):5173${NC}"
echo -e "  API:        ${CYAN}http://$(hostname -I | awk '{print $1}'):3001/api/health${NC}"
echo -e "  PM2 Durum:  ${CYAN}pm2 status${NC}"
echo -e "  PM2 Loglar: ${CYAN}pm2 logs${NC}"
echo ""
echo -e "  Admin PIN:  ${YELLOW}BABA${NC} (degistirmek icin .env dosyasini duzenleyin)"
echo ""
