"use client";

import { SWRConfig } from "swr";
import { Component, ReactNode, useState } from "react";
import dynamic from "next/dynamic";
import Sidebar from "@/components/Sidebar";
import TopBar from "@/components/TopBar";
import BottomTerminal from "@/components/BottomTerminal";
import StrategyControl from "@/components/StrategyControl";
import ChatPanel from "@/components/ChatPanel";
import CodeOperatorPanel from "@/components/CodeOperatorPanel";
import StrategyAlert from "@/components/StrategyAlert";
import WatchlistManager from "@/components/WatchlistManager";
import ActiveSignals from "@/components/ActiveSignals";
import PatternLibrary from "@/components/PatternLibrary";
import SignalHistory from "@/components/SignalHistory";
import LearningLog from "@/components/LearningLog";
import InterAgentTerminal from "@/components/InterAgentTerminal";
import MamisPanel from "@/components/MamisPanel";
import IntegrationPanel from "@/components/IntegrationPanel";
import MobileLiteDashboard from "@/components/MobileLiteDashboard";
import { swrConfig } from "@/lib/api";
import { BarChart3, GitBranch, Radio, Crosshair, Database, History, Brain, TerminalSquare, Radar, Activity, PanelLeft, X } from "lucide-react";

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
  const [tab, setTab] = useState<"market" | "signals" | "backtest" | "flow" | "integration" | "mamis" | "patterns" | "history" | "learning" | "intercom">("market");

  const tabs = [
    { key: "market" as const, icon: Radio, label: "Piyasa" },
    { key: "signals" as const, icon: Crosshair, label: "Sinyaller" },
    { key: "backtest" as const, icon: BarChart3, label: "Backtest" },
    { key: "flow" as const, icon: GitBranch, label: "Flow" },
    { key: "integration" as const, icon: Activity, label: "Entegrasyon" },
    { key: "mamis" as const, icon: Radar, label: "MAMIS" },
    { key: "patterns" as const, icon: Database, label: "Paternler" },
    { key: "history" as const, icon: History, label: "Geçmiş" },
    { key: "learning" as const, icon: Brain, label: "Öğrenme" },
    { key: "intercom" as const, icon: TerminalSquare, label: "İletişim" },
  ];

  return (
    <div className="flex h-full min-h-0 w-full flex-col border-t border-surface-border lg:w-[26rem] lg:flex-shrink-0 lg:border-l lg:border-t-0 xl:w-[28rem]">
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
        {tab === "market" && <WatchlistManager />}
        {tab === "signals" && <ActiveSignals />}
        {tab === "backtest" && <BacktestPanel />}
        {tab === "flow" && <AgentFlow />}
        {tab === "integration" && <IntegrationPanel />}
        {tab === "mamis" && <MamisPanel />}
        {tab === "patterns" && <PatternLibrary />}
        {tab === "history" && <SignalHistory />}
        {tab === "learning" && <LearningLog />}
        {tab === "intercom" && <InterAgentTerminal />}
      </div>
    </div>
  );
}


export default function Home() {
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);

  return (
    <ErrorBoundary fallback="Dashboard Hatası">
      <SWRConfig value={swrConfig}>
        <div className="flex min-h-svh flex-col overflow-x-hidden bg-surface lg:h-screen lg:overflow-hidden">
          <div className="border-b border-surface-border px-3 py-2 lg:hidden">
            <button
              onClick={() => setMobileSidebarOpen(true)}
              className="inline-flex items-center gap-2 rounded-lg border border-surface-border bg-surface-card px-3 py-2 text-xs font-medium text-gray-200"
            >
              <PanelLeft size={14} className="text-accent" />
              Ajanlar ve Sistem
            </button>
          </div>

          <div className="flex flex-1 min-h-0 flex-col lg:flex-row">
            <MobileLiteDashboard />

            {/* Left sidebar — Agent health */}
            <ErrorBoundary fallback="Sidebar Hatası">
              <div className="hidden lg:flex">
                <Sidebar />
              </div>
            </ErrorBoundary>

            {/* Center — Main trading area */}
            <div className="hidden min-w-0 flex-1 flex-col lg:flex">
              <ErrorBoundary fallback="TopBar Hatası">
                <TopBar />
              </ErrorBoundary>
              <div className="h-[42svh] min-h-[18rem] lg:flex-[3] lg:min-h-0">
                <ErrorBoundary fallback="Grafik Hatası">
                  <ChartCanvas />
                </ErrorBoundary>
              </div>
              <div className="h-[28rem] min-h-[16rem] lg:flex-[2] lg:min-h-0">
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
          <CodeOperatorPanel />

          {mobileSidebarOpen && (
            <div className="fixed inset-0 z-50 flex bg-black/60 lg:hidden">
              <div className="flex h-full w-[min(88vw,22rem)] flex-col bg-surface shadow-2xl">
                <div className="flex items-center justify-between border-b border-surface-border px-4 py-3">
                  <span className="text-sm font-semibold text-gray-200">Sistem Durumu</span>
                  <button
                    onClick={() => setMobileSidebarOpen(false)}
                    className="rounded-md border border-surface-border p-1 text-gray-400"
                  >
                    <X size={14} />
                  </button>
                </div>
                <div className="min-h-0 flex-1 overflow-y-auto">
                  <Sidebar />
                </div>
              </div>
              <button className="flex-1" onClick={() => setMobileSidebarOpen(false)} aria-label="Kapat" />
            </div>
          )}
        </div>
      </SWRConfig>
    </ErrorBoundary>
  );
}
