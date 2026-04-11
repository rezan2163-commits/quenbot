"""
Performance Attribution System
===============================
ENHANCEMENT #3: Track which agent/pattern generated the profit

Her position için:
- Hangi agent önerdi?
- Hangi pattern match'ti?
- Market regime neydi?
- Net PnL neydi?
- Attribution: Her component'in katkısı

Bu data ile learning loop'u güçlendiriyoruz
"""

import logging
from datetime import datetime
from typing import Dict, Any, Optional, List
import numpy as np

logger = logging.getLogger(__name__)


class PerformanceAttributor:
    """
    Trading performance'ı breakdowna et:
    - Agent attribution
    - Pattern attribution  
    - Market regime impact
    - Temporal attribution
    """
    
    def __init__(self, db_connection):
        self.db = db_connection
        self.attribution_cache = {}  # Quick lookup
    
    async def record_position_close(self, 
                                    position_id: int,
                                    primary_agent: str,
                                    primary_pattern_id: Optional[int],
                                    contributing_agents: List[str],
                                    market_regime: str,
                                    entry_price: float,
                                    exit_price: float,
                                    position_size: float,
                                    entry_time: datetime,
                                    exit_time: datetime,
                                    gemma_confidence: float = 0.0) -> Dict[str, Any]:
        """
        Position kapatıldığında attribution'ı kaydet
        """
        
        # 1. Calculate PnL
        pnl = (exit_price - entry_price) * position_size
        pnl_pct = ((exit_price - entry_price) / entry_price) * 100
        
        # 2. Time metrics
        duration_seconds = (exit_time - entry_time).total_seconds()
        duration_hours = duration_seconds / 3600
        pnl_per_hour = pnl / max(duration_hours, 0.01)
        
        # 3. Build attribution record
        attribution_data = {
            "position_id": position_id,
            "primary_agent": primary_agent,
            "primary_pattern_id": primary_pattern_id,
            "contributing_agents": contributing_agents,
            "market_regime": market_regime,
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 4),
            "pnl_per_hour": round(pnl_per_hour, 4),
            "duration_seconds": int(duration_seconds),
            "entry_price": float(entry_price),
            "exit_price": float(exit_price),
            "position_size": float(position_size),
            "gemma_confidence": float(gemma_confidence),
            "success": 1 if pnl > 0 else 0,
        }
        
        # 4. Save to DB
        try:
            await self.db.db_execute("""
                INSERT INTO position_attribution 
                (position_id, primary_agent, pattern_id, agents_involved, market_regime, 
                 pnl, pnl_pct, duration_sec, gemma_confidence, success)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                position_id,
                primary_agent,
                primary_pattern_id,
                ",".join(contributing_agents),
                market_regime,
                attribution_data["pnl"],
                attribution_data["pnl_pct"],
                int(duration_seconds),
                gemma_confidence,
                attribution_data["success"],
            ))
            logger.info(f"✅ Attribution recorded: {position_id} | Agent={primary_agent} | PnL={pnl_pct:.2f}%")
        except Exception as e:
            logger.error(f"❌ Attribution save error: {e}")
        
        # 5. Update agent performance
        await self._update_agent_stats(primary_agent, pnl_pct, market_regime)
        
        # 6. Update pattern performance
        if primary_pattern_id:
            await self._update_pattern_stats(primary_pattern_id, pnl_pct, market_regime)
        
        return attribution_data
    
    async def _update_agent_stats(self, agent_name: str, pnl_pct: float, regime: str):
        """
        Agent'in performance stats'ı update et
        Hangi regime'de ne kadar kazandığı track et
        """
        try:
            # Get current stats
            result = await self.db.db_query("""
                SELECT COUNT(*), SUM(pnl_pct), SUM(success) 
                FROM agent_performance 
                WHERE agent_name = %s AND market_regime = %s
            """, (agent_name, regime))
            
            if result and result[0][0] > 0:
                count, sum_pnl, sum_success = result[0]
                # Update
                await self.db.db_execute("""
                    UPDATE agent_performance 
                    SET trades_count = %s, total_pnl_pct = %s, win_count = %s
                    WHERE agent_name = %s AND market_regime = %s
                """, (count + 1, (sum_pnl or 0) + pnl_pct, (sum_success or 0) + (1 if pnl_pct > 0 else 0),
                      agent_name, regime))
            else:
                # Insert new
                await self.db.db_execute("""
                    INSERT INTO agent_performance 
                    (agent_name, market_regime, trades_count, total_pnl_pct, win_count)
                    VALUES (%s, %s, %s, %s, %s)
                """, (agent_name, regime, 1, pnl_pct, 1 if pnl_pct > 0 else 0))
        except Exception as e:
            logger.debug(f"Agent stats update error: {e}")
    
    async def _update_pattern_stats(self, pattern_id: int, pnl_pct: float, regime: str):
        """Pattern'in performance'ını track et"""
        try:
            result = await self.db.db_query("""
                SELECT COUNT(*), SUM(pnl_pct), SUM(success)
                FROM pattern_performance
                WHERE pattern_id = %s AND market_regime = %s
            """, (pattern_id, regime))
            
            if result and result[0][0] > 0:
                count, sum_pnl, sum_success = result[0]
                await self.db.db_execute("""
                    UPDATE pattern_performance
                    SET trades_count = %s, total_pnl_pct = %s, win_count = %s
                    WHERE pattern_id = %s AND market_regime = %s
                """, (count + 1, (sum_pnl or 0) + pnl_pct, (sum_success or 0) + (1 if pnl_pct > 0 else 0),
                      pattern_id, regime))
            else:
                await self.db.db_execute("""
                    INSERT INTO pattern_performance
                    (pattern_id, market_regime, trades_count, total_pnl_pct, win_count)
                    VALUES (%s, %s, %s, %s, %s)
                """, (pattern_id, regime, 1, pnl_pct, 1 if pnl_pct > 0 else 0))
        except Exception as e:
            logger.debug(f"Pattern stats update error: {e}")
    
    async def get_agent_rankings(self, regime: Optional[str] = None) -> List[Dict]:
        """
        Agents'ı performance'a göre rank et
        Hangi agent X regime'de best performans gösterdi?
        """
        try:
            where_clause = "WHERE market_regime = %s" if regime else ""
            params = [regime] if regime else []
            
            results = await self.db.db_query(f"""
                SELECT agent_name, market_regime, trades_count, 
                       total_pnl_pct, win_count,
                       ROUND(100.0 * win_count / trades_count, 1) as win_rate,
                       ROUND(total_pnl_pct / trades_count, 2) as avg_pnl
                FROM agent_performance
                {where_clause}
                ORDER BY total_pnl_pct DESC
                LIMIT 20
            """, params)
            
            return [
                {
                    "agent": row[0],
                    "regime": row[1],
                    "trades": row[2],
                    "total_pnl": row[3],
                    "wins": row[4],
                    "win_rate": row[5],
                    "avg_pnl": row[6],
                }
                for row in (results or [])
            ]
        except Exception as e:
            logger.error(f"Agent rankings error: {e}")
            return []
    
    async def get_top_patterns(self, regime: Optional[str] = None) -> List[Dict]:
        """
        Top-performing patterns'ı listele
        """
        try:
            where_clause = "WHERE market_regime = %s" if regime else ""
            params = [regime] if regime else []
            
            results = await self.db.db_query(f"""
                SELECT pattern_id, market_regime, trades_count,
                       total_pnl_pct, win_count,
                       ROUND(100.0 * win_count / trades_count, 1) as win_rate,
                       ROUND(total_pnl_pct / trades_count, 2) as avg_pnl
                FROM pattern_performance
                {where_clause}
                ORDER BY total_pnl_pct DESC
                LIMIT 20
            """, params)
            
            return [
                {
                    "pattern_id": row[0],
                    "regime": row[1],
                    "trades": row[2],
                    "total_pnl": row[3],
                    "wins": row[4],
                    "win_rate": row[5],
                    "avg_pnl": row[6],
                }
                for row in (results or [])
            ]
        except Exception as e:
            logger.error(f"Pattern rankings error: {e}")
            return []
    
    async def get_performance_breakdown(self) -> Dict[str, Any]:
        """
        Complete performance breakdown for dashboard
        """
        try:
            # Agent breakdown
            agent_stats = await self.get_agent_rankings()
            
            # Pattern breakdown
            pattern_stats = await self.get_top_patterns()
            
            # Regime breakdown
            result = await self.db.db_query("""
                SELECT market_regime, COUNT(*), SUM(success), 
                       ROUND(AVG(pnl_pct), 2) as avg_pnl,
                       ROUND(SUM(pnl), 2) as total_pnl
                FROM position_attribution
                GROUP BY market_regime
            """)
            
            regime_stats = [
                {
                    "regime": row[0],
                    "trades": row[1],
                    "wins": row[2],
                    "win_rate": round(100 * row[2] / row[1], 1) if row[1] > 0 else 0,
                    "avg_pnl": row[3],
                    "total_pnl": row[4],
                }
                for row in (result or [])
            ]
            
            return {
                "by_agent": agent_stats[:10],
                "by_pattern": pattern_stats[:10],
                "by_regime": regime_stats,
            }
        except Exception as e:
            logger.error(f"Performance breakdown error: {e}")
            return {}
