# QuenBot Mission Control

Operatörün yeni ana sayfası. "NASA görev kontrolü" ile "canlı hücre biyolojisi"
arasında, QuenBot'un tüm organlarını tek ekranda gösteren canlı takımyıldız.

## Amaç

Mission Control, birbirinden bağımsız 50+ modülün sağlığını, olay akışını ve
Qwen direktif hattını tek bir görsel dilde bir araya getirir. Amaç, operatörün
30 saniyede "sistem nefes alıyor mu?" sorusuna sezgisel olarak yanıt
verebilmesi.

- Herhangi bir **Oracle Stack** / beyin / dedektör / güvenlik mantığı
  değiştirilmez. Mission Control yalnızca okur.
- Tüm rakamlar **gerçek olaylardan** türetilir (`event_bus`, `runtime_supervisor`,
  `database`, `safety_net`). Mock yoktur.
- Bir altsistem ulaşılamazsa ilgili kutu `status="unknown"` veya `null` döner;
  sayfa çökmez, yalnızca o kısım boş görünür.

## Erişim

- Dashboard ana panelindeki TopBar'da **Mission Control** pusula simgesine tıkla.
- Doğrudan URL: `http://<host>:5173/mission-control`
- İlk girişin her gün ilk beş saniyesinde "uyanma" animasyonu oynar. Kapatmak
  için alt bardaki bağlantıyı kullan (localStorage anahtarı
  `mc_wake_anim_disabled`).

## 3 Katmanlı Görünüm

1. **Vital Signs (üst şerit)** — 9 kritik göstergeyi sparkline ile gösterir:
   Scout akışı, Qwen direktifleri, Safety Net, aktif sinyal, Ghost P&L 24s,
   WS uptime, uyarı sayısı, IFI, test durumu.
2. **Organ özeti** — 7 organın (Ajanlar, Beyin, Dedektörler, Füzyon, Öğrenme,
   Güvenlik, Çalışma) ortalama sağlık skoru ve modül dağılımı.
3. **Constellation Canvas** — Qwen çekirdekte, diğer organlar yörüngede;
   bağlantılar olay akış yoğunluğuna göre kalınlaşır (hot/warm/cool/silent).
   Bir modüle tıkla → **Autopsy Drawer** sağdan açılır.

## Renk Dili

- 🟢 **Yeşil / healthy** — skor ≥ 90, nabız taze, throughput var.
- 🟡 **Amber / slow** — 60 ≤ skor < 90 veya latency > 500ms.
- 🔴 **Kırmızı / unhealthy** — skor < 60, stale nabız (>5× period) veya error > %5.
- ⚫ **Gri / dormant** — beklendiği gibi pasif (örn. `gemma_decision_core`
  env flag kapalıyken).
- ⚪ **Zinc / disabled** — feature flag ile kapatılmış modül.

Qwen modülü etrafındaki renk halkası, faz durumunu gösterir:
- **Mor** → Faz 0 (gözlem)
- **Yeşil** → Faz 1 (gatekeeper)
- **Amber** → Faz 2 (etki takibi)
- **Kırmızı + 🚨 LOCKDOWN** → Faz 3 (acil kilit)

## 30 Saniyelik Rutin

1. Sağ üstteki **Sistem Skoru**'na bak → 85+ ise her şey yolunda.
2. Üst şeritte kırmızı/amber varsa önce **Safety Net** ve **Warnings**'e bak.
3. Takımyıldızda sönük (silent) kenarlar varsa → ilgili modüle tıkla → otopsi.
4. Qwen halkası amber/kırmızı ise faz etiketi ne diyor? LOCKDOWN sırasında
   hiçbir yeni pozisyon açılmaz (Safety Net devrede).

## SSE & Polling

- Öncelikli kanal: `GET /api/mission-control/stream` (Server-Sent Events).
  Her saniye bir JSON snapshot. Frame tipik < 50ms.
- SSE iki kez arka arkaya düşerse otomatik olarak **polling**'e geçer:
  `GET /api/mission-control/snapshot` 2 saniyede bir.
- Üst barda renkli nokta: yeşil = live, amber = polling, kırmızı = offline.
- Snapshot cache TTL varsayılan **1 saniye** (env: `MISSION_CONTROL_SNAPSHOT_TTL_SEC`).

## Autopsy (Qwen Tanısı)

- `GET /api/mission-control/autopsy/<module_id>` son olayları, bağımlılıkları
  ve Qwen'in kısa Türkçe tanısını döner.
- Qwen çağrıları modül başına **60 saniyelik bucket** ile cache'lenir; spam yok.
- LLM erişilemezse `diagnosis=null` döner, panel "Tanı üretilemedi" der.

## Restart Komutu

- `POST /api/mission-control/restart/<module_id>`
- Sadece `RuntimeSupervisor._restart_callback` tanımlıysa çalışır; değilse 503.
- İsteğe bağlı admin kilidi: `QUENBOT_ADMIN_TOKEN` env var'ını ayarla ve
  `X-Admin-Token: <token>` header'ı ile gönder. Token yanlışsa 401.
- Drawer'daki "Yeniden Başlat" düğmesi token sorar (boş bırakılabilir).

## Emergency Lockdown

Faz 3'e geçildiğinde (`QUENBOT_EMERGENCY_LOCKDOWN=1` veya Oracle Stack stop_all
komutu) üst barda kırmızı yanıp sönen **🚨 LOCKDOWN** rozeti çıkar. Bu durumda:
- Aktif sinyal kartı donar.
- Safety Net pill `crit` olur.
- Qwen halkası kırmızıya döner.
- Hiçbir yeni pozisyon açılmaz (Safety Net sorumluluğu, Mission Control bunu
  değiştirmez, yalnızca gösterir).

## Mimari

```
Dashboard (Next.js, 5173)
  └─ /api/mission-control/* → Express (api-server, 3001)
       └─ proxy → aiohttp (python_agents/main.py, 3002)
            └─ mission_control_aggregator (salt okuma)
                 ├─ event_bus._history / _latest_heartbeats
                 ├─ runtime_supervisor.status()
                 ├─ database.get_summary()
                 └─ safety_net durumu (best-effort)
```

## Dosya Haritası

**Python**:
- `python_agents/module_registry.py` — 50 modül, 7 organ, salt veri.
- `python_agents/mission_control_aggregator.py` — snapshot/timeline/autopsy.
- `python_agents/main.py` (değiştirildi) — 4 yeni route + external signals helper.

**TypeScript**:
- `artifacts/api-server/src/index.ts` (değiştirildi) — 4 proxy route + SSE
  pass-through.
- `dashboard/src/lib/missionControl.ts` — tipler + SSE/SWR hook.
- `dashboard/src/components/MissionControl/*.tsx` — 7 bileşen.
- `dashboard/src/app/mission-control/page.tsx` — rota.

## Testler

- `tests/test_module_registry.py` — 11 fonksiyon, kayıt tutarlılığı.
- `tests/test_mission_control_aggregator.py` — 19 fonksiyon, snapshot
  semantiği ve sağlık skoru formülü.
- `tests/test_mission_control_routes.py` — 10 fonksiyon, aiohttp TestServer
  üzerinden rota davranışları (SSE dahil).

Tüm paket: `pytest -q python_agents/tests` → 422 passed, 0 regresyon.
