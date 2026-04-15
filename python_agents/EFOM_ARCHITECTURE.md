# EFOM Architecture

```mermaid
flowchart LR
    subgraph Existing System
        SC[Scout / Trade Stream]
        ER[ERIFE-like Regime Signals]
        MA[MAMIS Layer]
        GS[Ghost Simulator]
        DB[(PostgreSQL)]
        BUS[[Event Bus]]
    end

    subgraph EFOM Observer
        CTL[Contextual Trade Logger]
        CA[Critic Agent]
        HPO[Hyperparameter Optimizer]
        CFG[(runtime_config.json)]
        CSV[(contextual_trade_log.csv)]
    end

    SC --> DB
    GS --> DB
    MA --> BUS
    ER --> DB

    DB -->|closed simulations poll| CTL
    BUS -->|mamis.bar cache| CTL
    CTL --> CSV
    CSV --> CA
    CA -->|JSON suggestions| HPO
    CSV --> HPO
    HPO --> CFG
    CFG -->|session bootstrap overrides| MA
    CFG -->|session bootstrap overrides| ER
```

Observer kuralı korunur:
- MAMIS ve ERIFE çekirdek sınıfları değiştirilmez.
- EFOM kapanan işlemleri veritabanından, mikro-yapı bağlamını mevcut event bus akışından dinler.
- Optimizasyon çıktısı bir runtime config dosyasına yazılır ve yeni oturum başlangıcında dışarıdan yüklenir.