"use client";

import Sidebar from "@/components/Sidebar";
import TopBar from "@/components/TopBar";
import ChartCanvas from "@/components/ChartCanvas";
import BottomTerminal from "@/components/BottomTerminal";
import StrategyControl from "@/components/StrategyControl";
import ChatPanel from "@/components/ChatPanel";

export default function Home() {
  return (
    <div className="flex h-screen overflow-hidden">
      {/* Sidebar — Agent health */}
      <Sidebar />

      {/* Main area */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Top KPIs + movers */}
        <TopBar />

        {/* Chart — 60% height */}
        <div className="flex-[3] min-h-0">
          <ChartCanvas />
        </div>

        {/* Terminal — 40% height */}
        <div className="flex-[2] min-h-0">
          <BottomTerminal />
        </div>
      </div>

      {/* Floating controls */}
      <StrategyControl />
      <ChatPanel />
    </div>
  );
}
