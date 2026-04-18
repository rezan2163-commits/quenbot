"use client";

import { useEffect, useState } from "react";

const KEY = "mc_wake_anim_disabled";
const DATE_KEY = "mc_wake_anim_last_date";

export function WakeAnimation() {
  const [active, setActive] = useState(false);

  useEffect(() => {
    if (typeof window === "undefined") return;
    if (window.localStorage.getItem(KEY) === "1") return;
    const today = new Date().toISOString().slice(0, 10);
    const last = window.localStorage.getItem(DATE_KEY);
    if (last === today) return;
    window.localStorage.setItem(DATE_KEY, today);
    setActive(true);
    const t = setTimeout(() => setActive(false), 5000);
    return () => clearTimeout(t);
  }, []);

  if (!active) return null;

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/70 pointer-events-none"
      onAnimationEnd={() => setActive(false)}
    >
      <div className="text-center select-none">
        <div className="relative mx-auto h-40 w-40">
          <div className="absolute inset-0 rounded-full border-2 border-accent animate-ping opacity-40" />
          <div className="absolute inset-4 rounded-full border-2 border-purple-400 animate-ping opacity-60" style={{ animationDelay: "0.3s" }} />
          <div className="absolute inset-10 rounded-full bg-gradient-to-br from-purple-500 to-accent animate-pulse" />
          <div className="absolute inset-0 flex items-center justify-center text-4xl font-bold text-white">Q</div>
        </div>
        <p className="mt-4 text-sm uppercase tracking-[0.3em] text-gray-300 animate-pulse">QuenBot Uyanıyor</p>
      </div>
    </div>
  );
}

export function WakeAnimationToggle() {
  const [disabled, setDisabled] = useState(false);
  useEffect(() => {
    if (typeof window !== "undefined") {
      setDisabled(window.localStorage.getItem(KEY) === "1");
    }
  }, []);
  const toggle = () => {
    const next = !disabled;
    setDisabled(next);
    if (typeof window !== "undefined") {
      window.localStorage.setItem(KEY, next ? "1" : "0");
    }
  };
  return (
    <button
      onClick={toggle}
      className="text-[10px] text-gray-500 hover:text-gray-300 underline"
      title="Günlük açılış animasyonunu aç/kapat"
    >
      {disabled ? "Açılış animasyonunu aç" : "Açılış animasyonunu kapat"}
    </button>
  );
}
