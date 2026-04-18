"use client";
/**
 * Aşama 3 — Free Roam panel
 *
 * Shows operator-health overview:
 *  - Weekly ack badge (🟢 ≤48h / 🟡 ≤7d / 🔴 >7d → degraded)
 *  - Last monthly self-audit disagreement
 *  - Emergency lockdown state + button (with confirm-text gate)
 *  - Roll-back-to-Aşama-2 reminder (the watchdog already does this on missed ack)
 *
 * All actions hit `/api/oracle/...` proxies. Buttons require a typed
 * confirmation string ("KİLİT" / "AŞAMA2") to prevent fat-finger trips.
 */
import { useState } from "react";
import { useAsama3Status } from "@/lib/intel";

const API = process.env.NEXT_PUBLIC_API_URL || "";

function ackAgeHours(weekStartedTs?: number | null): number | null {
  if (!weekStartedTs) return null;
  return (Date.now() / 1000 - Number(weekStartedTs)) / 3600;
}

function ackBadge(present: boolean, ageHours: number | null, graceHours: number) {
  if (present) return { color: "#16a34a", label: "🟢 Ack alındı" };
  if (ageHours == null) return { color: "#9ca3af", label: "—" };
  if (ageHours <= 48) return { color: "#16a34a", label: `🟢 ${ageHours.toFixed(1)} sa` };
  if (ageHours <= graceHours) return { color: "#facc15", label: `🟡 ${ageHours.toFixed(1)} sa` };
  return { color: "#ef4444", label: `🔴 ${ageHours.toFixed(1)} sa (degraded)` };
}

export default function Asama3Panel() {
  const { data, mutate, isLoading } = useAsama3Status();
  const [confirmText, setConfirmText] = useState("");
  const [reason, setReason] = useState("");
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [resultMsg, setResultMsg] = useState<string | null>(null);

  if (isLoading) return <div style={{ padding: 16, color: "#9ca3af" }}>Aşama 3 yükleniyor…</div>;
  if (!data) return <div style={{ padding: 16, color: "#ef4444" }}>Aşama 3 status erişilemiyor.</div>;

  const wd = data.weekly_ack || {};
  const lock = data.emergency_lockdown || {};
  const sa = data.self_audit || {};
  const cfg = data.config || {};

  const ageH = ackAgeHours(wd.week_started_at_ts ?? null);
  const grace = wd.grace_hours ?? 168;
  const badge = ackBadge(Boolean(wd.ack_present), ageH, grace);

  async function engageLockdown() {
    if (confirmText.trim().toUpperCase() !== "KİLİT") {
      setResultMsg("⚠️ Onay metni 'KİLİT' olmalı");
      return;
    }
    if (!reason.trim()) {
      setResultMsg("⚠️ Sebep gerekli");
      return;
    }
    setBusy(true);
    setResultMsg(null);
    try {
      const r = await fetch(`${API}/api/oracle/emergency-lockdown`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Emergency-Token": token },
        body: JSON.stringify({ reason }),
      });
      const j = await r.json();
      setResultMsg(r.ok ? `✅ Lockdown aktif: ${j?.state?.reason || ""}` : `❌ ${j?.error || r.status}`);
      mutate();
    } catch (e: any) {
      setResultMsg(`❌ ${e?.message || e}`);
    } finally {
      setBusy(false);
      setConfirmText("");
    }
  }

  return (
    <div style={{ padding: 16, color: "#e5e7eb", display: "flex", flexDirection: "column", gap: 16 }}>
      <header>
        <h2 style={{ fontSize: 18, fontWeight: 600, margin: 0 }}>Aşama 3 — Serbest Dolaşım</h2>
        <div style={{ fontSize: 12, color: "#9ca3af", marginTop: 4 }}>
          Faz: <strong style={{ color: data.phase === "asama_3" ? "#16a34a" : "#facc15" }}>{data.phase}</strong>
          {" · "}Max direktif/saat: <strong>{cfg.max_directives_per_hour ?? "—"}</strong>
        </div>
      </header>

      {/* Weekly ack */}
      <section style={{ background: "#111827", padding: 12, borderRadius: 8, border: "1px solid #1f2937" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <div>
            <div style={{ fontSize: 13, color: "#9ca3af" }}>Haftalık operator ack</div>
            <div style={{ fontSize: 16, fontWeight: 600, color: badge.color }}>{badge.label}</div>
          </div>
          <div style={{ textAlign: "right", fontSize: 12, color: "#9ca3af" }}>
            <div>Hafta: <strong>{wd.current_week || "—"}</strong></div>
            <div>Grace: {grace} sa · Degraded: <strong>{wd.degraded ? "EVET" : "hayır"}</strong></div>
          </div>
        </div>
        <div style={{ marginTop: 8, fontSize: 11, color: "#6b7280" }}>
          Ack komutu:{" "}
          <code style={{ color: "#a5b4fc" }}>
            python python_agents/scripts/ack_weekly.py --week {wd.current_week || "YYYY-WW"} --note "..."
          </code>
        </div>
      </section>

      {/* Self-audit */}
      <section style={{ background: "#111827", padding: 12, borderRadius: 8, border: "1px solid #1f2937" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <div>
            <div style={{ fontSize: 13, color: "#9ca3af" }}>Aylık öz-denetim disagreement</div>
            <div style={{
              fontSize: 16, fontWeight: 600,
              color: sa.alert_emitted ? "#ef4444" : "#16a34a",
            }}>
              {sa.disagreement_rate != null ? `${(sa.disagreement_rate * 100).toFixed(1)}%` : "—"}
              {sa.alert_emitted && " 🟠"}
            </div>
          </div>
          <div style={{ textAlign: "right", fontSize: 12, color: "#9ca3af" }}>
            <div>Ay: <strong>{sa.month_label || "—"}</strong></div>
            <div>Sample: {sa.sample_size ?? "—"} · Eşik: {sa.threshold != null ? `${(sa.threshold * 100).toFixed(0)}%` : "—"}</div>
          </div>
        </div>
      </section>

      {/* Emergency lockdown */}
      <section style={{
        background: lock.engaged ? "#7f1d1d" : "#111827",
        padding: 12, borderRadius: 8,
        border: `1px solid ${lock.engaged ? "#dc2626" : "#1f2937"}`,
      }}>
        <div style={{ fontSize: 13, color: "#9ca3af", marginBottom: 8 }}>Emergency Lockdown</div>
        {lock.engaged ? (
          <div style={{ color: "#fecaca" }}>
            🚨 ENGAGED — kaynak: <strong>{lock.source}</strong> · sebep: {lock.reason}
            {lock.engaged_at ? ` · @ ${new Date(Number(lock.engaged_at) * 1000).toISOString()}` : ""}
            <div style={{ marginTop: 8, fontSize: 11, color: "#fca5a5" }}>
              Manuel reset: <code>python python_agents/scripts/emergency_lockdown.py --release --operator &lt;ad&gt;</code>
            </div>
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <input
              type="password"
              placeholder="X-Emergency-Token"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              style={{ background: "#0b1220", border: "1px solid #1f2937", borderRadius: 4, padding: "6px 8px", color: "#e5e7eb" }}
            />
            <input
              type="text"
              placeholder="Sebep (kayıt için)"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              style={{ background: "#0b1220", border: "1px solid #1f2937", borderRadius: 4, padding: "6px 8px", color: "#e5e7eb" }}
            />
            <input
              type="text"
              placeholder="Onaylamak için 'KİLİT' yaz"
              value={confirmText}
              onChange={(e) => setConfirmText(e.target.value)}
              style={{ background: "#0b1220", border: "1px solid #1f2937", borderRadius: 4, padding: "6px 8px", color: "#e5e7eb" }}
            />
            <button
              onClick={engageLockdown}
              disabled={busy || confirmText.trim().toUpperCase() !== "KİLİT"}
              style={{
                background: confirmText.trim().toUpperCase() === "KİLİT" ? "#dc2626" : "#374151",
                color: "#fff", padding: "8px 12px", borderRadius: 4, border: "none",
                cursor: busy ? "wait" : "pointer", fontWeight: 600,
              }}
            >
              🚨 EMERGENCY LOCKDOWN ENGAGE
            </button>
            {resultMsg && <div style={{ fontSize: 12, color: "#facc15" }}>{resultMsg}</div>}
          </div>
        )}
      </section>

      {/* Allowlist / blocklist */}
      <section style={{ background: "#111827", padding: 12, borderRadius: 8, border: "1px solid #1f2937", fontSize: 12 }}>
        <div style={{ color: "#9ca3af", marginBottom: 6 }}>Direktif politikası</div>
        <div><strong>Allowlist:</strong> {(cfg.allowlist || []).join(", ") || "—"}</div>
        <div style={{ marginTop: 4, color: "#fca5a5" }}>
          <strong>Hard blocklist:</strong> {(cfg.blocklist_hard || []).join(", ") || "—"}
        </div>
      </section>
    </div>
  );
}
