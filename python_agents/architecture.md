# Otonom Çoklu Ajanlı Piyasa İstihbarat Sistemi

## 1. Sistem Mimarisi

Bu sistem dört temel ajan üzerine kuruludur:

1. Scout Agent
2. Strategist Agent
3. Ghost Simulator Agent
4. Auditor Agent

Her ajan, açık uçlu yapıya izin verecek şekilde `AgentBase` üzerinden genişletilebilir.

## 2. Mantıksal Akış Diyagramı

```mermaid
flowchart TD
    A[Binance / Bybit Spot + Futures Trades] -->|Gerçekleşmiş İşlemler| B[Scout Agent]
    B -->|T-10 Dakika Olay Seti| DB[PostgreSQL]
    B --> C[Strategist Agent]
    C -->|Benzerlik Analizi (Cosine Similarity)| D[Signal]
    D --> E[Ghost Simulator Agent]
    E -->|Paper Trading Testi| F[Simulation Result]
    F --> G[Auditor Agent]
    G -->|Blacklist Pattern / Threshold Update| C
    G --> DB
    DB --> H[Dashboard / Looker Studio / Google Sheets]
    H -->|Monitoring| I[Operatör]
```

## 3. Veri Tabanı Şeması

### trades
- id: SERIAL PRIMARY KEY
- exchange: VARCHAR(50)
- market_type: VARCHAR(20)  # spot / futures
- symbol: VARCHAR(20)
- price: NUMERIC
- quantity: NUMERIC
- side: VARCHAR(10)  # buy / sell
- timestamp: TIMESTAMP
- trade_id: VARCHAR(100) UNIQUE
- created_at: TIMESTAMP

### price_movements
- id: SERIAL PRIMARY KEY
- exchange: VARCHAR(50)
- market_type: VARCHAR(20)
- symbol: VARCHAR(20)
- start_price: NUMERIC
- end_price: NUMERIC
- change_pct: NUMERIC
- volume: NUMERIC
- buy_volume: NUMERIC
- sell_volume: NUMERIC
- direction: VARCHAR(10)
- aggressiveness: NUMERIC
- start_time: TIMESTAMP
- end_time: TIMESTAMP
- t10_data: JSONB
- created_at: TIMESTAMP

### signals
- id: SERIAL PRIMARY KEY
- market_type: VARCHAR(20)
- symbol: VARCHAR(20)
- signal_type: VARCHAR(50)
- confidence: NUMERIC
- price: NUMERIC
- timestamp: TIMESTAMP
- status: VARCHAR(20)
- metadata: JSONB
- created_at: TIMESTAMP

### simulations
- id: SERIAL PRIMARY KEY
- signal_id: INTEGER REFERENCES signals(id)
- market_type: VARCHAR(20)
- symbol: VARCHAR(20)
- entry_price: NUMERIC
- exit_price: NUMERIC
- quantity: NUMERIC
- side: VARCHAR(10)
- status: VARCHAR(20)
- pnl: NUMERIC
- pnl_pct: NUMERIC
- entry_time: TIMESTAMP
- exit_time: TIMESTAMP
- stop_loss: NUMERIC
- take_profit: NUMERIC
- metadata: JSONB
- created_at: TIMESTAMP

### blacklist_patterns
- id: SERIAL PRIMARY KEY
- pattern_type: VARCHAR(50)
- pattern_data: JSONB
- confidence: NUMERIC
- reason: TEXT
- created_by: VARCHAR(50)
- created_at: TIMESTAMP

### watchlist
- id: SERIAL PRIMARY KEY
- symbol: VARCHAR(20)
- market_type: VARCHAR(20)
- description: TEXT
- created_at: TIMESTAMP

## 4. Ajan Yapısı

### Scout Agent
- Binance Spot / Binance Futures / Bybit Spot / Bybit Futures trade kanallarını dinler.
- Sadece gerçekleşmiş işlemleri kaydeder.
- Piyasa hareketini T-10 dakika önceki tüm işlemlerle değerlendirir.
- Alım/satım agresifliği ve işlem frekansını kaydeder.

### Strategist Agent
- Son T-10 dakika hareketini geçmiş başarılı hareketlerle vektörel benzerlik ile karşılaştırır.
- `cosine_similarity` %70 üzerine çıkar çıkmaz sinyal üretir.
- Sinyal, `long` veya `short` bias içerir.

### Ghost Simulator Agent
- Gerçek piyasa yerine paper trading içinde pozisyon açar.
- %5 target take-profit ve dinamik stop-loss kullanır.
- Sonucu izler ve kapatır.
- Başarı oranını ve gerçek doğruluğu ölçer.

### Auditor Agent
- Kapalı simülasyonları inceler.
- Yanlış tahminlerden false-positive desenleri çıkarır.
- Strateji eşiğini ve filtreleri kendi kendine günceller.

## 5. Açık Uçlu Genişletilebilirlik
- Yeni ajanlar `AgentBase` sınıfını genişleterek eklenebilir.
- `WatchlistManager` yeni haber, sosyal medya veya on-chain ajanları tarafından kullanılabilir.
- Veritabanı katmanı kolayca BigQuery veya başka bir veri ambarına genişletilebilir.

## 6. Başlama Adımları
1. `python_agents/architecture.md` ve `python_agents/agent_base.py` eklendi.
2. `python_agents/scout_agent.py` canlı spot/futures trade entegrasyonu için güncelleniyor.
3. `python_agents/database.py` ve `artifacts/api-server/src/db.ts` veri modeline market_type ve watchlist ekleniyor.
4. `python_agents/strategist_agent.py` cosine similarity temelli sinyal üretimi eklenecek.
5. `python_agents/ghost_simulator_agent.py` paper trading doğruluk testleri iyileştirilecek.
