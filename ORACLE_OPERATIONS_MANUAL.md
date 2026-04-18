# Oracle Stack Operations Manual (Phase 6)

Bu doküman §1–§12 Oracle Stack'in operatör kılavuzudur. PR1 (detectors), PR2 (factor graph + brain), PR3 (runtime supervisor + systemd) tamamlandıktan sonra production'a nasıl güvenle alınacağını açıklar.

---

## 1. Genel ilkeler

- **Tüm flag'ler default-OFF.** Hiçbir yeni modül davranış değiştirmez. Operatör açık etmeden devreye girmez.
- **Additive.** Varolan tablolar, EventType değerleri, config default'ları korunur.
- **Shadow-first.** Qwen Oracle Brain üretime açılınca bile `QUENBOT_ORACLE_BRAIN_SHADOW=1` ile başlatılmalı.
- **Safety-net korunur.** `safety_net.tripped` olduğunda hiçbir direktif uygulanmaz.

---

## 2. Env flag referansı

### Sinyal yolu
| Flag | Default | Amaç |
|---|---|---|
| `QUENBOT_ORACLE_BUS_ENABLED` | `1` | Registry; kapatmak nadir senaryo. |
| `QUENBOT_BOCPD_ENABLED` | `0` | Bayesian changepoint. |
| `QUENBOT_HAWKES_ENABLED` | `0` | Self/cross excitation. |
| `QUENBOT_LOB_THERMO_ENABLED` | `0` | Entropy cooling. |
| `QUENBOT_WASSERSTEIN_ENABLED` | `0` | Distribution drift. |
| `QUENBOT_PATH_SIGNATURE_ENABLED` | `0` | Path sig similarity. |
| `QUENBOT_MIRROR_FLOW_ENABLED` | `0` | Mirror execution. |
| `QUENBOT_TDA_ENABLED` | `0` | Topological whale birth. |
| `QUENBOT_ONCHAIN_ENABLED` | `0` | Causal on-chain bridge. |

### Füzyon (§10)
| Flag | Default | Amaç |
|---|---|---|
| `QUENBOT_FACTOR_GRAPH_ENABLED` | `0` | Loopy BP füzyonu. |
| `QUENBOT_FG_BP_ITER` | `100` | İterasyon sayısı. |
| `QUENBOT_FG_DAMPING` | `0.5` | Stabilite için damping. |
| `QUENBOT_FG_PUBLISH_HZ` | `0.5` | IFI yayın hızı. |

### Brain (§11)
| Flag | Default | Amaç |
|---|---|---|
| `QUENBOT_ORACLE_BRAIN_ENABLED` | `0` | Merkezi orkestrasyon. |
| `QUENBOT_ORACLE_BRAIN_SHADOW` | `1` | Direktifleri uygulama, sadece logla. |
| `QUENBOT_ORACLE_BRAIN_LEARN_INTERVAL_MIN` | `10` | Ağırlık revizyon tick'i. |
| `QUENBOT_ORACLE_BRAIN_TEACH_INTERVAL_MIN` | `60` | LLM özet çağrı aralığı. |
| `QUENBOT_ORACLE_BRAIN_RAG_TOP_K` | `5` | RAG top-k. |

### Runtime (§12)
| Flag | Default | Amaç |
|---|---|---|
| `QUENBOT_RUNTIME_SUPERVISOR_ENABLED` | `0` | Health aggregator + heartbeat. |
| `QUENBOT_RUNTIME_HEALTH_CHECK_INTERVAL_SEC` | `30` | Tick aralığı. |
| `QUENBOT_RUNTIME_MAX_RESTART_ATTEMPTS` | `3` | Cap. |
| `QUENBOT_WATCHDOG_ENABLED` | `0` | Heartbeat dosyası yazımı. |
| `QUENBOT_WATCHDOG_TIMEOUT_SEC` | `120` | External watchdog tolerance. |

---

## 3. Production'a açma sırası (önerilir)

**1. Gözlem turu (1 hafta).** Sadece `QUENBOT_ORACLE_BUS_ENABLED=1`. Detector'lar kapalı. Dashboard Oracle sekmesi "Dormant" gösterir.

**2. Detector'lar PR1.** Sırayla açın: `BOCPD → HAWKES → LOB_THERMO → WASSERSTEIN → PATH_SIG → MIRROR_FLOW → TDA → ONCHAIN`. Her detector sonrası `/api/oracle/summary`'yi kontrol edin, en az 24 saat gözleyin.

**3. Factor Graph.** `QUENBOT_FACTOR_GRAPH_ENABLED=1`. `/api/oracle/factor-graph/BTCUSDT` ile IFI değerlerini doğrulayın.

**4. Oracle Brain SHADOW.** `QUENBOT_ORACLE_BRAIN_ENABLED=1 QUENBOT_ORACLE_BRAIN_SHADOW=1`. `/api/oracle/brain/directives` ile direktifleri izleyin. **Minimum 2 hafta shadow**.

**5. Runtime Supervisor.** `QUENBOT_RUNTIME_SUPERVISOR_ENABLED=1 QUENBOT_WATCHDOG_ENABLED=1`. `/api/runtime/status` ve `/tmp/quenbot_heartbeat` güncel kalmalı.

**6. Brain LIVE (opsiyonel, gelecek).** Shadow kaldırma karar ağına bağlanma gerektirir; bu dokümana eklenecek.

---

## 4. API endpoint'leri

| Endpoint | Amaç |
|---|---|
| `GET /api/oracle/summary` | Detector + kanal + brain + factor_graph özet. |
| `GET /api/oracle/channels/{symbol}` | Bir sembolün tüm kanal değerleri. |
| `GET /api/oracle/detector/{name}` | Detector snapshot. |
| `GET /api/oracle/factor-graph/{symbol}` | §10 IFI + marginals + weights. |
| `GET /api/oracle/brain/directives` | §11 sembol bazlı son direktifler. |
| `GET /api/oracle/brain/traces?limit=N` | §11 reasoning trace'leri. |
| `GET /api/oracle/brain/health` | §11 brain + RAG stats. |
| `GET /api/runtime/status` | §12 supervisor status + metrics. |
| `GET /api/intel/summary` | Agregated health tüm modüllerin. |

---

## 5. Systemd kurulumu (§12)

```bash
sudo bash /opt/quenbot/scripts/install_systemd.sh
sudo systemctl enable --now quenbot.service
journalctl -u quenbot.service -f
```

Watchdog cron (opsiyonel, observe-only default):
```
*/2 * * * *  /usr/local/bin/quenbot_watchdog.sh >> /var/log/quenbot_watchdog.log 2>&1
```

Aktif restart için: `QB_WATCHDOG_RESTART=1` cron environment'ında.

---

## 6. Troubleshooting

| Belirti | Kontrol |
|---|---|
| `/api/oracle/summary` → `enabled:false` | `QUENBOT_ORACLE_BUS_ENABLED` set mi? |
| IFI her zaman 0 | Signal bus'ta kanal var mı? (`/api/oracle/channels/{sym}`) |
| Brain direktifi `MONITOR` | Threshold'ler karşılanmıyor; bu normal. |
| Brain `HOLD_OFF` kalıcı | topology + mirror kanalları > 0.8; veri kalitesini kontrol edin. |
| Heartbeat stale | `journalctl -u quenbot`; event loop blok olabilir. |
| `oracle_reasoning` RAG boş | ChromaDB import hatası; `backend=inmem` fallback'e düşmüş olabilir. |

---

## 7. Kapatma prosedürü (güvenli)

1. Dashboard'dan Oracle sekmesini gözleyin.
2. `sudo systemctl stop quenbot.service` — SIGTERM → graceful shutdown (§12 handler).
3. 30sn içinde proses sona ermezse `systemctl kill -s KILL`.
4. Flag'leri sırayla kapatıp tekrar başlatmak için `/etc/systemd/system/quenbot.service.d/override.conf` kullanın.

---

## 8. Doğrulama checklist

- [ ] `bash TEST_INTEL_UPGRADE.sh` — 4 faz yeşil
- [ ] `pytest python_agents -q` — 161+ test yeşil
- [ ] `/api/oracle/summary` canlı response
- [ ] `/api/oracle/brain/directives` shadow direktifler
- [ ] `/api/runtime/status` heartbeat < 60sn
