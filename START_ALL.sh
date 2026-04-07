#!/bin/bash

set -e

echo "🚀 QuenBot - Sistem Başlatılıyor"
echo "=================================="

# Renk kodları
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 1. PostgreSQL kontrolü
echo -e "${YELLOW}1. PostgreSQL kontrol ediliyor...${NC}"
if ! docker ps | grep -q "quenbot-db"; then
    echo -e "${YELLOW}   PostgreSQL başlatılıyor...${NC}"
    docker run -d \
      --name quenbot-db \
      -e POSTGRES_PASSWORD=password \
      -e POSTGRES_USER=user \
      -e POSTGRES_DB=trade_intel \
      -p 5432:5432 \
      postgres:15
    sleep 5
    echo -e "${GREEN}   ✓ PostgreSQL başlatıldı${NC}"
else
    echo -e "${GREEN}   ✓ PostgreSQL zaten çalışıyor${NC}"
fi

# 2. .env dosya kontrolü
echo -e "${YELLOW}2. .env dosya kontrol ediliyor...${NC}"
if [ ! -f "/workspaces/quenbot/python_agents/.env" ]; then
    echo -e "${YELLOW}   .env dosya oluşturuluyor...${NC}"
    cat > /workspaces/quenbot/python_agents/.env <<EOF
DATABASE_URL=postgresql://user:password@localhost:5432/trade_intel
BINANCE_API_KEY=
BINANCE_SECRET_KEY=
BYBIT_API_KEY=
BYBIT_SECRET_KEY=
EOF
    echo -e "${GREEN}   ✓ .env dosya oluşturuldu${NC}"
else
    echo -e "${GREEN}   ✓ .env dosya mevcut${NC}"
fi

# 3. Python dependencies kontrolü
echo -e "${YELLOW}3. Python dependencies kontrol ediliyor...${NC}"
cd /workspaces/quenbot/python_agents
if ! pip list | grep -q "asyncpg"; then
    echo -e "${YELLOW}   Paketler yükleniyor...${NC}"
    pip install -q -r requirements.txt
    echo -e "${GREEN}   ✓ Python paketleri yüklendi${NC}"
else
    echo -e "${GREEN}   ✓ Python paketleri zaten yüklü${NC}"
fi

# 4. Node dependencies kontrolü
echo -e "${YELLOW}4. Node dependencies kontrol ediliyor...${NC}"
cd /workspaces/quenbot
if ! pnpm --dir artifacts/api-server ls | grep -q "express"; then
    echo -e "${YELLOW}   npm paketleri yükleniyor...${NC}"
    pnpm --dir artifacts/api-server install --frozen-lockfile
    pnpm --dir artifacts/market-intel install --frozen-lockfile
    echo -e "${GREEN}   ✓ npm paketleri yüklendi${NC}"
else
    echo -e "${GREEN}   ✓ npm paketleri zaten yüklü${NC}"
fi

echo ""
echo -e "${GREEN}===============================================${NC}"
echo -e "${GREEN}✅ Başlangıç hazırlığı tamamlandı!${NC}"
echo -e "${GREEN}===============================================${NC}"
echo ""
echo "Şimdi birden fazla terminali açın ve şunları çalıştırın:"
echo ""
echo "Terminal 1 (Python Agents):"
echo "  cd /workspaces/quenbot/python_agents"
echo "  python3 main.py"
echo ""
echo "Terminal 2 (API Server):"
echo "  cd /workspaces/quenbot"
echo "  pnpm --dir artifacts/api-server run dev"
echo ""
echo "Terminal 3 (Dashboard):"
echo "  cd /workspaces/quenbot"
echo "  pnpm --dir artifacts/market-intel run preview"
echo ""
echo "Veya hepsini birden başlatmak için:"
echo "  bash /workspaces/quenbot/START_ALL_ASYNC.sh"
echo ""
