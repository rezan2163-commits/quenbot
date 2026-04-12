"use client";

import { SWRConfig } from "swr";
import { Component, ReactNode, useState, Suspense, lazy } from "react";
import dynamic from "next/dynamic";
import Sidebar from "@/components/Sidebar";
import TopBar from "@/components/TopBar";
import BottomTerminal from "@/components/BottomTerminal";
import StrategyControl from "@/components/StrategyControl";
import ChatPanel from "@/components/ChatPanel";
import StrategyAlert from "@/components/StrategyAlert";
import LiveMarketFeed from "@/components/LiveMarketFeed";
import ActiveSignals from "@/components/ActiveSignals";
import PatternLibrary from "@/components/PatternLibrary";
import SignalHistory from "@/components/SignalHistory";
import LearningLog from "@/components/LearningLog";
import { swrConfig } from "@/lib/api";
import { BarChart3, GitBranch, Radio, Crosshair, Database, History, Brain } from "lucide-react";

// Heavy components with lightweight-charts — lazy load
const ChartCanvas = dynamic(() => import("@/components/ChartCanvas"), { ssr: false, loading: () => <div className="flex-1 bg-surface animate-pulse" /> });
const BacktestPanel = dynamic(() => import("@/components/BacktestPanel"), { ssr: false, loading: () => <div className="p-4 text-gray-600 text-xs">Yükleniyor...</div> });
const AgentFlow = dynamic(() => import("@/components/AgentFlow"), { ssr: false, loading: () => <div className="p-4 text-gray-600 text-xs">Yükleniyor...</div> });

class ErrorBoundary extends Component<{ children: ReactNode; fallback?: string }, { hasError: boolean; error: string }> {
  constructor(props: { children: ReactNode; fallback?: string }) {
    super(props);
    this.state = { hasError: false, error: "" };
  }
  static getDerivedStateFromError(error: Error) {
    return { hasError: true, error: error.message };
  }
  render() {
    if (this.state.hasError) {
      return (
        <div className="flex items-center justify-center h-full bg-surface text-gray-300 p-4">
          <div className="text-center space-y-2">
            <p className="text-sm font-bold text-red-400">{this.props.fallback || "Hata"}</p>
            <p className="text-[10px] text-gray-500 max-w-xs break-all">{this.state.error}</p>
            <button onClick={() => this.setState({ hasError: false, error: "" })} className="px-3 py-1 bg-accent rounded text-white text-xs">
              Tekrar Dene
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

function RightPanel() {
  const [tab, setTab] = useState<"market" | "signals" | "backtest" | "flow" | "patterns" | "history" | "learning">("market");

  const tabs = [
    { key: "market" as const, icon: Radio, label: "Piyasa" },
    { key: "signals" as const, icon: Crosshair, label: "Sinyaller" },
    { key: "backtest" as const, icon: BarChart3, label: "Backtest" },
    { key: "flow" as const, icon: GitBranch, label: "Flow" },
    { key: "patterns" as const, icon: Database, label: "Paternler" },
    { key: "history" as const, icon: History, label: "Geçmiş" },
    { key: "learning" as const, icon: Brain, label: "Öğrenme" },
  ];

  return (
    <div className="w-80 flex-shrink-0 h-full flex flex-col border-l border-surface-border">
      {/* Tab selector — scrollable */}
      <div className="flex overflow-x-auto border-b border-surface-border bg-surface-card/30 custom-scrollbar">
        {tabs.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`flex items-center justify-center gap-1 px-2.5 py-2 text-[10px] font-medium whitespace-nowrap transition-colors ${
              tab === t.key ? "text-accent border-b-2 border-accent" : "text-gray-500 hover:text-gray-300"
            }`}
          >
            <t.icon size={11} />
            {t.label}
          </button>
        ))}
      </div>
      {/* Panel content */}
      <div className="flex-1 min-h-0">
        {tab === "market" && <LiveMarketFeed />}
        {tab === "signals" && <ActiveSignals />}
        {tab === "backtest" && <BacktestPanel />}
        {tab === "flow" && <AgentFlow />}
        {tab === "patterns" && <PatternLibrary />}
        {tab === "history" && <SignalHistory />}
        {tab === "learning" && <LearningLog />}
      </div>
    </div>
  );
}

export default function Home() {
  return (
    <ErrorBoundary fallback="Dashboard Hatası">
      <SWRConfig value={swrConfig}>
        <div className="flex flex-col h-screen overflow-hidden">
          <div className="flex flex-1 min-h-0">
            {/* Left sidebar — Agent health */}
            <ErrorBoundary fallback="Sidebar Hatası">
              <Sidebar />
            </ErrorBoundary>

            {/* Center — Main trading area */}
            <div className="flex-1 flex flex-col min-w-0">
              <ErrorBoundary fallback="TopBar Hatası">
                <TopBar />
              </ErrorBoundary>
              <div className="flex-[3] min-h-0">
                <ErrorBoundary fallback="Grafik Hatası">
                  <ChartCanvas />
                </ErrorBoundary>
              </div>
              <div className="flex-[2] min-h-0">
                <ErrorBoundary fallback="Terminal Hatası">
                  <BottomTerminal />
                </ErrorBoundary>
              </div>
            </div>

            {/* Right panel — Backtest + Flow */}
            <ErrorBoundary fallback="Panel Hatası">
              <RightPanel />
            </ErrorBoundary>
          </div>

          {/* Bottom strategy alert bar */}
          <ErrorBoundary fallback="Alert Hatası">
            <StrategyAlert />
          </ErrorBoundary>

          {/* Floating controls */}
          <StrategyControl />
          <ChatPanel />
        </div>
      </SWRConfig>
    </ErrorBoundary>
  );
}
