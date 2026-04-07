# QuenBot 🤖

Gerçek zamanlı kripto piyasa verileri ile çalışan trading dashboard ve evrimsel strateji analizi.

## Özellikler

- 📊 **Gerçek Zamanlı Piyasa Verileri** – CoinGecko API üzerinden canlı fiyatlar
- 🔥 **Trend Coinler** – Anlık trend olan kripto paraları takip edin
- 🧬 **Evrimsel Strateji** – Genetik algoritma ile alış/satış eşiği optimizasyonu
- 📈 **Fiyat Grafiği** – Geçmiş fiyat verilerini görselleştirin
- 🔄 **Otomatik Yenileme** – Her 60 saniyede veri güncellenir

## Kurulum

```bash
pip install -r requirements.txt
```

## Çalıştırma

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Tarayıcıda [http://localhost:8000](http://localhost:8000) adresine gidin.

## API Endpoints

| Endpoint | Açıklama |
|---|---|
| `GET /api/prices?coins=bitcoin,ethereum&vs=usd,try` | Anlık fiyatlar |
| `GET /api/market?vs=usd&per_page=20` | Piyasa verileri |
| `GET /api/history/{coin_id}?days=30` | Geçmiş fiyat verisi |
| `GET /api/coin/{coin_id}` | Coin detay bilgisi |
| `GET /api/trending` | Trend coinler |
| `GET /api/strategy/{coin_id}?days=30` | Evrimsel strateji analizi |

## Teknolojiler

- **Backend**: FastAPI + Uvicorn
- **Veri Kaynağı**: CoinGecko API (ücretsiz, API anahtarı gerekmez)
- **Strateji**: Evrimsel algoritma ile optimize edilmiş alış/satış eşikleri
- **Frontend**: Vanilla HTML/CSS/JS dashboard
