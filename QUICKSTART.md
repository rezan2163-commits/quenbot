# QuenBot - Multi-Agent Market Intelligence System 🤖

## Sistem Mimarisi

QuenBot, dört ana ajantan oluşan bir otonom multi-agent pazar zekası sistemidir:

### 1. **Scout Agent** - Veri Toplama
- **Görev**: Binance ve Bybit'ten gerçek ticaret verilerini topla
- **API'ler**:
  - Binance WebSocket: `wss://stream.binance.com:9443/ws` (Spot), `wss://fstream.binance.com/public` (Futures)
  - Bybit WebSocket: `wss://stream.bybit.com/v5/public/spot` (Spot), `wss://stream.bybit.com/v5/public/linear` (Futures)
  - Fallback REST API: Periyodik olarak son işlemleri çeker
- **Veriler**: Her sembol için alım-satım işlemleri, fiyat hareketleri, hacim analizi
- **Saat**: 24/7 canlı veri akışı + 30 saniyelik REST yedeklemesi

### 2. **Strategist Agent** - İşaret Üretimi
- **Görev**: Tarihsel hareketlerle karşılaştırarak benzerlik tabanlı alım-satım işaretleri oluştur
- **Yöntem**: Cosine similarity kullanarak koşullar eşleşen geçmiş hareketi bul
- **Sonuç**: `signal_type` (long/short), `confidence` (0-1), `position_bias`
- **Periyotluk**: Hareket tespit edildiğinde tetiklenir

### 3. **Ghost Simulator Agent** - Kağıt Ticaret Simülasyonu
- **Görev**: İşaretleri alarak "hayaleti" (paper trading) pozisyonları yönet
- **Kontrol**: TP/SL logic, zaman aşımı, commission hesaplaması
- **Veri**: Her pozisyon için PnL, PnL%, kapalı analiz
- **Auditor'a**: Başarısız pozisyonları raporta

### 4. **Auditor Agent** - Analiz & İyileştirme
- **Görev**: Kapalı simülasyonları analiz ederek hata oranlarını izle
- **Ölçüm**: Başarı oranı, avg win/loss, signal type başarısızlıkları
- **Eylem**: Eğer oran düşükse, öneriler oluştur

---

## Kurulum

### 1. Gerekli Paketleri Yükle

```bash
cd /workspaces/quenbot/python_agents
pip install -r requirements.txt
```

Önemli paketler:
- `asyncpg` - PostgreSQL async
- `websockets` - WebSocket bağlantıları
- `aiohttp` - Async HTTP
- `binance-connector` - Binance REST
- `pybit` - Bybit REST
- `numpy`, `pandas`, `scikit-learn` - Veri analizi

### 2. PostgreSQL Veritabanı Ayarla

```bash
# Docker'da çalıştırıyorsanız (veya yerel PostgreSQL)
docker run -d \
  --name quenbot-db \
  -e POSTGRES_PASSWORD=password \
  -e POSTGRES_USER=user \
  -e POSTGRES_DB=trade_intel \
  -p 5432:5432 \
  postgres:15
```

### 3. Ortam Değişkenlerini Ayarla

`.env` dosya oluştur:

```env
DATABASE_URL=postgresql://user:password@localhost:5432/trade_intel
BINANCE_API_KEY=your_key_here
BINANCE_SECRET_KEY=your_secret_here
BYBIT_API_KEY=your_key_here
BYBIT_SECRET_KEY=your_secret_here
```

---

## Çalıştırma

### Ana Sistemi Başlat

```bash
cd /workspaces/quenbot/python_agents
python3 main.py
```

**Beklenen Çıktı:**

```
================================================================================
🤖 QUENBOT - Multi-Agent Market Intelligence System
================================================================================
✓ Database initialized
✓ All agents initialized
✓ Monitoring 10 symbols: ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', ...]
================================================================================
🚀 Starting agent system...
✓ Connected to Binance SPOT WebSocket: wss://stream.binance.com:9443/ws/...
✓ Connected to Binance FUTURES WebSocket: wss://fstream.binance.com/public?streams=...
✓ Connected to Bybit SPOT WebSocket: wss://stream.bybit.com/v5/public/spot
✓ Connected to Bybit FUTURES WebSocket: wss://stream.bybit.com/v5/public/linear
Sent subscription to Bybit spot: 10 symbols
REST API fallback fetch completed for 10 symbols
...
```

---

## Veri Akışı

```
1. Scout Agent (WebSocket + REST)
   ↓ Alım-satım işlemleri
   ↓ Database.trades
   
2. Scout Agent (Hareketler)
   ↓ %2+ fiyat değişikliği
   ↓ Database.price_movements
   
3. Strategist Agent
   ↓ Tarihsel profillerle karşılaş
   ↓ Benzerlik skor > 0.7
   ↓ Database.signals
   
4. Ghost Simulator Agent
   ↓ Pending sinyaller
   ↓ Entry → TP/SL/Timeout
   ↓ Database.simulations
   
5. Auditor Agent
   ↓ Her 24 saat
   ↓ Kapalı simülasyonları analiz
   ↓ Database.audit_records
```

---

## Veritabanı Şeması

**Ana Tablolar:**

| Tablo | Amaç | Alanlar |
|-------|------|--------|
| `trades` | Ham ticaret verileri | exchange, market_type, symbol, price, qty, side, timestamp |
| `price_movements` | Tespit edilen hareketler | symbol, change_pct, volume, direction, t10_data |
| `signals` | Üretilen sinyaller | symbol, signal_type, confidence, status, metadata |
| `simulations` | Kağıt ticaret pozisyonları | symbol, entry/exit_price, pnl, stop_loss, take_profit |
| `audit_records` | Başarı/başarısızlık analizi | success_rate, avg_win_pct, avg_loss_pct |
| `failure_analysis` | Hatal signal tipi incelemesi | signal_type, failure_count, avg_loss_pct |

---

## Konfigürasyon

`config.py` içinde ayarlanabilir parametreler:

```python
# Sembolleri izle
WATCHLIST = ["BTCUSDT", "ETHUSDT", "BNBUSDT", ...]

# Hareket eşiği
PRICE_MOVEMENT_THRESHOLD = 0.02  # 2%

# Benzerlik eşiği
SIMILARITY_THRESHOLD = 0.7

# Kağıt ticaret
GHOST_TAKE_PROFIT_PCT = 0.05  # %5
GHOST_STOP_LOSS_PCT = 0.03    # %3

# Kimlik doğrulama
GHOST_SIMILARITY_THRESHOLD = 0.7
AUDIT_LEARNING_RATE = 0.1
```

---

## Health Check

Sistem her 60 saniyede bir sağlık kontrolü yapar:

```
📊 HEALTH CHECK
  Scout: ✓ (4/4 connections)
  Strategist: ✓
  Ghost Simulator: ✓ (3 active)
  Auditor: ✓
```

---

## Dashboard Entegrasyonu

API Server üzerinden veriler şu endpointler üzerinde sunulur:

```bash
GET /api/trades              # Son işlemler
GET /api/movements           # Fiyat hareketleri
GET /api/signals             # Üretilen sinyaller
GET /api/simulations         # Kağıt ticaret pozisyonları
GET /api/bot/summary         # Dashboard özeti
GET /api/live/prices         # Canlı fiyatlar
```

---

## Sorun Giderme

### "Veri gelmiyor 😭"

1. **WebSocket Bağlantısını Kontrol Et**
   ```bash
   # A separate terminal
   python3 -c "
   import websockets
   import asyncio
   async def test():
       async with websockets.connect('wss://stream.binance.com:9443/ws/btcusdt@trade') as ws:
           msg = await ws.recv()
           print(msg)
   asyncio.run(test())
   "
   ```

2. **Veritabanını Kontrol Et**
   ```bash
   psql postgresql://user:password@localhost:5432/trade_intel
   SELECT COUNT(*) FROM trades;
   ```

3. **Günlükleri Kontrol Et**
   ```bash
   tail -f /workspaces/quenbot/python_agents/agents.log
   ```

### "Connection refused"

- PostgreSQL çalışıyor mu? → `docker ps`
- DATABASE_URL doğru mu? → `.env` kontrol et
- Port açık mı? → `netstat -tlnp | grep 5432`

### "Rate limiting"

Scout Agent otomatik olarak 5 saniye bekleme ile yeniden bağlanır. Binance/Bybit rate limitlerinden haberdar ol.

---

## İleri Kullanım

### Kıstırı Tasarısı

1. **Custom Strategy Ekle**: `strategy.py`'de yeni `analyze_*` method yaz
2. **Blacklist Eklenmesi**: `Database.insert_blacklist_pattern()` kullan
3. **Real Trading**: Aynı logic'i, Ghost Simulator yerine gerçek executor'la değiştir

---

## Geliştirme Yol Haritası

- [ ] Gerçek ticaret ihraçatı (Binance/Bybit order execution)
- [ ] Portfolio risk yönetimi
- [ ] Advanced ML stratejileri
- [ ] WebUI dashboard (canlı görselleştirme)
- [ ] Backtesting framework

---

**Son Güncelleme:** 7 Nisan 2026
**API Versiyonları:** Binance (2026), Bybit V5
