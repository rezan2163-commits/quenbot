# QuenBot Oracle — Uzun Vadeli İşletim Kılavuzu

> **Durum:** Aşama 3 — Serbest Dolaşım (Free Roam Mode)
> **Felsefe:** Free Roam ≠ No Oversight. Günlük + haftalık + aylık disiplin zorunludur.
> **Acil çıkış:** Her zaman 1 komut uzakta (`touch /tmp/quenbot_emergency`).

---

## 🗓️ Günlük (her sabah 10 dakika)

- Günlük raporu oku (cron ile `python_agents/.cron_logs/daily.log`).
- Dashboard'da **Aşama 3 — Serbest Dolaşım** panelini kontrol et:
  - Faz `asama_3` mü? Yoksa `asama_2_degraded` mı?
  - Auto-rollback son 24 saatte tetiklendi mi?
  - Lockdown engaged mi?
- Anormallik varsa → `python python_agents/scripts/check_status.py` (hızlı tarama) veya panel sekmelerini incele.

---

## 🗓️ Haftalık (her Pazartesi 20 dakika) — **ZORUNLU**

1. Pazar 18:00 (Europe/Istanbul) cron'u haftalık raporu üretir:
   `python_agents/reports/weekly_strategic_<YYYY-WW>.md`
2. Raporu oku.
3. Ack komutunu çalıştır:
   ```bash
   python python_agents/scripts/ack_weekly.py --week YYYY-WW --note "..."
   ```
4. **Aksi halde 7 gün sonra sistem otomatik olarak Aşama 2 throttles'a düşer.**
   (Watchdog `WEEKLY_ACK_MISSING` + `SYSTEM_AUTO_DEGRADED` event'i emit eder, panel kırmızı olur.)
5. Ack alındığı saat içinde sistem otomatik Aşama 3'e geri döner — ekstra adım gerekmez.

---

## 🗓️ Aylık (her ayın 1'i 30 dakika) — **ZORUNLU**

1. 03:00 UTC cron'u Qwen self-audit çalıştırır:
   `python_agents/reports/self_audit_<YYYY-MM>.md`
2. Raporu oku — özellikle `disagreement_rate`'i.
3. **`disagreement_rate > %40` ise:**
   - Prompt template'lerini gözden geçir (`python_agents/qwen_oracle_brain.py::_maybe_teach`).
   - RAG koleksiyonunu denetle (yanlış örnekler birikmiş olabilir).
   - Fast Brain retraining adayı (`python_agents/scripts/train_fast_brain.py`).

---

## 🗓️ Çeyreklik (kritik gözden geçirme)

- **Mimari:** Yeni oracle kanalı eklenebilir mi? Mevcut kanallardan emekli edilecek var mı?
- **Fast Brain retraining:** Otomatik değil — operatör başlatır.
- **Counterfactual DB integrity:** Toplam satır sayısı + label dağılımı sapması.
- **Cross-asset graph:** Yeni eklenen sembollerin lead/lag'leri sağlıklı mı?

---

## 🗓️ Yıllık

- Üçüncü taraf güvenlik / mimari audit (canlı production ise).
- Tam tech debt review.
- Stratejik roadmap güncelleme (yeni faz veya konsolidasyon).

---

## 🚨 Acil Durumlar

| Durum | Komut | Etki |
|---|---|---|
| Felaket — sistemi DURDUR | `touch /tmp/quenbot_emergency` | Sentinel watcher 5 sn içinde lockdown engage eder. Mevcut paper pozisyonlar doğal exit ile kapanır. |
| API üzerinden kilit | `curl -XPOST -H "X-Emergency-Token: $TOKEN" -d '{"reason":"..."}' /api/oracle/emergency-lockdown` | Aynı etki, network üzerinden. |
| Şüphe — Qwen'i shadow'a al | `QUENBOT_ORACLE_BRAIN_SHADOW=1` + restart | Kararlar log'lanır ama publish edilmez. |
| Belirsizlik — auto-rollback'i zorla | `POST /api/oracle/autorollback/force` | Aşama 1'e zorla rollback. |
| Lockdown'dan çık | `python python_agents/scripts/emergency_lockdown.py --release --operator <ad> --note "..."` | Sentinel dosya otomatik silinir, sistem yeniden değerlendirir. |

---

## 📁 Önemli Dosya Yolları

- Haftalık raporlar: `python_agents/reports/weekly_strategic_*.md`
- Aylık self-audit: `python_agents/reports/self_audit_*.md`
- Operator ack'leri: `python_agents/.weekly_ack/.weekly_ack_*.json`
- Lockdown snapshot'ları: `python_agents/.emergency/emergency_lockdown_*.json`
- Self-audit JSON sidecar: `python_agents/.self_audit_latest.json`
- Cron log'ları: `python_agents/.cron_logs/`
- Sentinel: `/tmp/quenbot_emergency`

---

## 🔁 Aşama 2'ye geri dönme (kalıcı)

Tek env dosyası değişikliği yeterli — `AŞAMA_3_TODAY.md §5` bölümündeki 2 değişikliği geri al + restart.
