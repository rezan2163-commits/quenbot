# 🚀 QUENBOT - VERİ AKIŞINI BAŞLAT

**Durum:**
- ✅ PostgreSQL: Çalışıyor (Port 5432)
- ✅ Tüm kodlar hazır (hatasız)
- ✅ Database schema: Oluşturulacak

**Sorun:** Veri gelmiyor çünkü servisler başlatılmamış

---

## 3 TERMINAL AÇIN

### Terminal 1 - Python Agents (Scout, Strategist, Ghost, Auditor)

```bash
cd /workspaces/quenbot/python_agents
python3 main.py
```

**Beklenen:** 
```
🤖 QUENBOT - Multi-Agent Market Intelligence System
✓ Database initialized
✓ All agents initialized
✓ Monitoring 10 symbols: ['BTCUSDT', 'ETHUSDT', ...]
🚀 Starting agent system...
✓ Connected to Binance SPOT WebSocket
✓ Connected to Binance FUTURES WebSocket
✓ Connected to Bybit SPOT WebSocket
✓ Connected to Bybit FUTURES WebSocket
REST API fallback fetch completed for 10 symbols
```

**Veri şu anda veritabanına akıyor!**

---

### Terminal 2 - API Server (Verilerinizi sunuyor)

```bash
cd /workspaces/quenbot
pnpm --dir artifacts/api-server run dev
```

**Beklenen:**
```
Connected to PostgreSQL database
Server listening on port 3001
```

---

### Terminal 3 - Dashboard (UI göster)

```bash
cd /workspaces/quenbot
pnpm --dir artifacts/market-intel run preview
```

**Beklenen:**
```
  ➜  Local:   http://localhost:4173/
  ➜  press h to show help
```

---

## SONRA

Tarayıcıda açın: **http://localhost:4173**

Verileri görülürüz:
- Live prices (son alım-satım)
- Performance metrics
- Signals generated
- Paper trading results

---

## HATA GIDERMESİ

**"API Error: Failed to fetch"** → API Server terminal'ı kontrol et
**"Connection refused"** → Terminal 1'de Python Agents çalışıyor mu?
**Hiç veri yok** → Apache başlatmayı beklemeyi 10+ saniyeye çıkar

---

## ⚡ TIP

Tüm üç terminali aynı anda açabilirsiniz, sıra önemli değil.

