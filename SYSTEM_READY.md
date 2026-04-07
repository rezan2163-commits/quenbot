# 🚀 QuenBot Sistemi HAZIR

**Tarih**: 7 Nisan 2026  
**API Versiyonları**: Binance 2026, Bybit V5

## ✅ Tamamlanan Bileşenler

### Python Multi-Agent System (python_agents/)

```
✓ config.py            - 2026 API endpoints (Binance/Bybit güncellemesi)
✓ database.py          - PostgreSQL persistence + audit tables
✓ agent_base.py        - Base class + watchlist manager
✓ scout_agent.py       - WebSocket + REST fallback veri çekme
✓ strategist_agent.py  - Benzerlik tabanlı sinyal üretimi
✓ strategy.py          - Cosine similarity + evolutionary optimizer
✓ ghost_simulator_agent.py - Paper trading + PnL tracking
✓ auditor_agent.py     - Başarı analizi + false positive detection
✓ main.py              - Agent orchestrator + health monitor
✓ requirements.txt     - Tüm dependencies
```

### Node.js Frontend (artifacts/market-intel/)

```
✓ Dashboard BUILD: ✓ (148.83 kB gzip)
  - Real-time price fetch
  - Live trade feed
  - Bot performance tracking
  - Watchlist support
```

### Node.js API Server (artifacts/api-server/)

```
✓ TypeScript CHECK: ✓ (no errors)
  - /api/trades
  - /api/movements
  - /api/signals
  - /api/simulations
  - /api/bot/summary
  - /api/live/prices
```

---

## 📊 Veri Akışı Özeti

```
Binance (Spot + Futures)
  ├─ WebSocket: wss://stream.binance.com:9443/ws
  ├─ WebSocket: wss://fstream.binance.com/public
  └─ REST (Fallback): /api/v3/trades

         ↓ Scout Agent

Bybit (Spot + Futures)
  ├─ WebSocket: wss://stream.bybit.com/v5/public/spot
  ├─ WebSocket: wss://stream.bybit.com/v5/public/linear
  └─ REST (Fallback): /v5/market/recent-trade

         ↓

    📦 Database.trades
    ├─ exchange (binance/bybit)
    ├─ market_type (spot/futures)
    ├─ symbol, price, quantity
    ├─ side (buy/sell)
    └─ timestamp

         ↓ Scout + Strategist

    📦 Database.price_movements
    ├─ Detected changes > 2%
    ├─ Volume analysis
    ├─ Direction bias (long/short)
    └─ t10_data: price profile

         ↓ Strategist

    📦 Database.signals
    ├─ signal_type
    ├─ confidence (0-1)
    ├─ price, timestamp
    └─ metadata

         ↓ Ghost Simulator

    📦 Database.simulations
    ├─ entry_price, exit_price
    ├─ PnL, PnL%
    ├─ stop_loss, take_profit
    └─ status (open/closed)

         ↓ Auditor

    📦 Database.audit_records
    ├─ success_rate
    ├─ avg_win/loss %
    └─ failure analysis

         ↓

    💡 API Server → Dashboard
```

---

## 🔧 API Uç Noktaları

### Trade Verisi
```
GET /api/trades?symbol=BTCUSDT&market_type=spot&limit=100
```

### Fiyat Hareketleri
```
GET /api/movements?symbol=BTCUSDT&hours=24
```

### Generated Sinyaller
```
GET /api/signals?status=pending
```

### Simülasyon Sonuçları
```
GET /api/simulations?status=closed
```

### Canlı Fiyatlar
```
GET /api/live/prices
```

### Bot Özeti
```
GET /api/bot/summary
```

---

## 🎯 Özellikler

### Scout Agent
- ✅ 4 WebSocket (Binance Spot/Futures + Bybit Spot/Futures)
- ✅ REST API fallback her 30 saniye
- ✅ Gerçek ticaret verileri (OrderBook değil)
- ✅ Market type tagging (spot vs futures)
- ✅ Otomatik yeniden bağlantı (5sec retry)

### Strategist Agent
- ✅ Tarihsel hareketler ile karşılaştırma
- ✅ Cosine similarity scoring
- ✅ Confidence metriği (0-1)
- ✅ Direction bias (long/short)
- ✅ T10 window analysis

### Ghost Simulator
- ✅ Paper trading pozisyonları
- ✅ Take Profit: %5, Stop Loss: %3
- ✅ Commission simulasyonu: 0.1%
- ✅ Timeout: 24 saat
- ✅ PnL + PnL% tracking

### Auditor
- ✅ Periyodik analiz (24 saatlik)
- ✅ Başarı oranı hesaplaması
- ✅ False positive detection
- ✅ Signal type başarısızlık analizi
- ✅ Rekomendasyonlar

---

## 🚀 Başlat

### 1. Veritabanı

```bash
docker run -d \
  --name quenbot-db \
  -e POSTGRES_PASSWORD=password \
  -e POSTGRES_USER=user \
  -e POSTGRES_DB=trade_intel \
  -p 5432:5432 \
  postgres:15
```

### 2. .env Dosyası

```
DATABASE_URL=postgresql://user:password@localhost:5432/trade_intel
```

### 3. Sistemı Çalıştır

```bash
cd /workspaces/quenbot/python_agents
python3 main.py
```

### 4. Dashboard (2. terminal)

```bash
cd /workspaces/quenbot && pnpm --dir artifacts/market-intel run preview
```

### 5. API Server (3. terminal)

```bash
cd /workspaces/quenbot && pnpm --dir artifacts/api-server run dev
```

---

## 📈 Beklenen Çıktı (60 saniye içinde)

```
[2026-04-07 10:30:00] 🤖 QUENBOT - Multi-Agent Market Intelligence System
[2026-04-07 10:30:01] ✓ Database initialized
[2026-04-07 10:30:02] ✓ All agents initialized
[2026-04-07 10:30:02] ✓ Monitoring 10 symbols
[2026-04-07 10:30:03] 🚀 Starting agent system...
[2026-04-07 10:30:05] ✓ Connected to Binance SPOT WebSocket
[2026-04-07 10:30:06] ✓ Connected to Binance FUTURES WebSocket
[2026-04-07 10:30:07] ✓ Connected to Bybit SPOT WebSocket
[2026-04-07 10:30:08] ✓ Connected to Bybit FUTURES WebSocket
[2026-04-07 10:30:09] Sent subscription to Bybit spot: 10 symbols
[2026-04-07 10:30:10] REST API fallback fetch completed for 10 symbols
[2026-04-07 10:30:15] Binance spot trade: BTCUSDT buy @ 67234.50 x 0.125
[2026-04-07 10:30:16] Bybit futures trade: ETHUSDT sell @ 3456.20 x 5.5
[2026-04-07 10:30:45] 📊 Movement detected [spot] BNBUSDT: 2.45%, direction=long
[2026-04-07 10:31:00] Generated signal: BNBUSDT long, confidence=0.82
[2026-04-07 10:31:02] Created simulation 1: long BNBUSDT @ 567.89
[2026-04-07 10:32:00] 📊 HEALTH CHECK
  Scout: ✓ (4/4 connections)
  Strategist: ✓
  Ghost Simulator: ✓ (1 active)
  Auditor: ✓
```

---

## 🐛 Sorun Giderme

**Q: Veri gelmiyor!**  
A: `tail -f /workspaces/quenbot/python_agents/agents.log` ile günlükleri kontrol et

**Q: WebSocket bağlantı hatası**  
A: Fineweb erişimi var mı? → `curl https://stream.binance.com`

**Q: "Database connection refused"**  
A: PostgreSQL çalışıyor mu? → `docker ps`

**Q: Benzerlik hiç trigger olmuyor**  
A: PRICE_MOVEMENT_THRESHOLD=0.02 (2%) kontrolü → config.py

---

## 📋 Sonraki Adımlar

1. **Gerçek Trading**: Ghost Simulator → Binance order execution
2. **Portfolio Risk**: Position sizing, correlation analysis
3. **Advanced ML**: LSTM forecasting, model ensemble
4. **Backtesting**: Tarihsel veri üzerinde strategy test
5. **WebUI**: Real-time graphs, live feed viewer

---

**✅ Sistem Başlamaya Hazır!**

Hata yok ✓
Tüm API'ler güncellendi ✓
Database schema ready ✓
Veri akışı entegre ✓

**Başlat:** `python3 python_agents/main.py`

