"""
Proactive Risk Management
==========================
ENHANCEMENT #5: Prevent drawdown before it happens

Reactive risk: İşi kapatıp zarar kes (geç)
Proactive risk: Riski düşür, position'ları kapat, limitleri al (erken)

System drawdown %X'e yaklaşırsa:
1. En düşük confidence positions'ı kapat
2. Position size'ları kısalt
3. Komşu correlate coin'lerde gemi alma
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
import asyncio

logger = logging.getLogger(__name__)


class ProactiveRiskManager:
    """
    Drawdown'a proactively cevap ver
    Stop loss tetiklemeden position'ları kapat
    """
    
    def __init__(self, db_connection):
        self.db = db_connection
        self.max_drawdown_pct = 2.0
        self.warning_threshold_pct = 0.5  # Alert at 50% of max drawdown
        self.position_close_threshold_pct = 1.0  # Close positions at 100% of warning threshold
        
        self.last_check = None
        self.drawdown_alerts = []
    
    async def check_and_prevent_drawdown(self) -> Optional[Dict[str, Any]]:
        """
        Drawdown'u check et, proactive actions al
        """
        
        try:
            # Calculate current drawdown
            drawdown_info = await self._calculate_system_drawdown()
            
            if not drawdown_info:
                return None
            
            current_drawdown = drawdown_info["current_drawdown_pct"]
            headroom = self.max_drawdown_pct - current_drawdown
            
            logger.debug(f"📊 Drawdown: {current_drawdown:.2f}% / {self.max_drawdown_pct}% "
                        f"(headroom: {headroom:.2f}%)")
            
            # If in danger zone, take action
            if headroom <= self.position_close_threshold_pct:
                logger.critical(f"🚨 CRITICAL ALERT: {headroom:.2f}% headroom left!")
                return await self._execute_drawdown_prevention(drawdown_info)
            
            elif headroom <= self.warning_threshold_pct:
                logger.warning(f"⚠️ WARNING: {headroom:.2f}% headroom left")
                return await self._execute_drawdown_warning(drawdown_info)
            
            return None
            
        except Exception as e:
            logger.error(f"❌ Drawdown check error: {e}")
            return None
    
    async def _calculate_system_drawdown(self) -> Optional[Dict[str, Any]]:
        """
        Calculate peak-to-trough drawdown
        """
        try:
            # Get all closed simulation PnLs
            result = await self.db.db_query("""
                SELECT 
                    SUM(pnl) as total_pnl,
                    COUNT(*) as trade_count,
                    MAX(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                    MAX(CASE WHEN pnl < 0 THEN pnl ELSE 0 END) as max_loss,
                    AVG(pnl) as avg_pnl
                FROM simulations
                WHERE status = 'closed'
                AND timestamp > DATE_SUB(NOW(), INTERVAL 7 DAY)
            """)
            
            if not result or not result[0][0]:
                return None
            
            row = result[0]
            total_pnl = float(row[0] or 0)
            trade_count = int(row[1] or 0)
            wins = int(row[2] or 0)
            max_loss = float(row[3] or 0)
            
            if total_pnl <= 0:
                # We're down, potential drawdown
                drawdown_pct = abs(max_loss) / max(abs(total_pnl), 1.0) * 100.0
            else:
                drawdown_pct = 0
            
            return {
                "current_drawdown_pct": drawdown_pct,
                "total_pnl": total_pnl,
                "trade_count": trade_count,
                "win_rate": (wins / trade_count * 100) if trade_count > 0 else 0,
                "max_loss": max_loss,
            }
        
        except Exception as e:
            logger.error(f"❌ Drawdown calc error: {e}")
            return None
    
    async def _execute_drawdown_warning(self, drawdown_info: Dict) -> Dict[str, Any]:
        """
        Warning level: Reduce new position size, increase stop loss tightness
        """
        
        logger.warning("⚠️ EXECUTING DRAWDOWN WARNING PROTOCOL")
        
        actions = []
        
        # 1. Reduce new position size
        await self.db.db_execute("""
            UPDATE strategy_parameters
            SET position_size_mult = position_size_mult * 0.8
            WHERE updated_at > DATE_SUB(NOW(), INTERVAL 1 HOUR)
        """)
        actions.append("📿 Position size × 0.8")
        logger.warning("  → Position size reduced 20%")
        
        # 2. Tighten stop loss
        await self.db.db_execute("""
            UPDATE strategy_parameters
            SET stop_loss_pct = stop_loss_pct * 0.7
            WHERE updated_at > DATE_SUB(NOW(), INTERVAL 1 HOUR)
        """)
        actions.append("📿 Stop loss × 0.7 (tighter)")
        logger.warning("  → Stop loss tightened 30%")
        
        # 3. Alert to Gemma
        alert_msg = (f"⚠️ RISK WARNING: System drawdown at {drawdown_info['current_drawdown_pct']:.1f}%. "
                    f"Position sizing reduced, stops tightened.")
        actions.append(f"🤖 Gemma alert: {alert_msg}")
        
        return {
            "level": "WARNING",
            "drawdown": drawdown_info['current_drawdown_pct'],
            "actions": actions,
            "timestamp": datetime.utcnow(),
        }
    
    async def _execute_drawdown_prevention(self, drawdown_info: Dict) -> Dict[str, Any]:
        """
        Critical level: Close low-confidence positions, halt new trades
        """
        
        logger.critical("🚨 EXECUTING CRITICAL DRAWDOWN PREVENTION")
        
        actions = []
        
        # 1. Get open positions sorted by risk
        open_positions = await self._get_open_positions_by_risk()
        
        if open_positions:
            # Close bottom 50% by confidence
            positions_to_close = open_positions[:max(1, len(open_positions) // 2)]
            
            closed_count = 0
            for pos in positions_to_close:
                try:
                    current_price = await self._get_current_price(pos['symbol'])
                    pnl = (current_price - pos['entry_price']) * pos['size']
                    
                    await self.db.db_execute("""
                        UPDATE simulations
                        SET status = 'closed', exit_price = %s, pnl = %s
                        WHERE id = %s
                    """, (current_price, pnl, pos['id']))
                    
                    closed_count += 1
                    logger.warning(f"  🔴 Closed: {pos['symbol']} @{current_price:.2f} (PnL: {pnl:.2f})")
                
                except Exception as e:
                    logger.error(f"  ❌ Could not close {pos['symbol']}: {e}")
            
            actions.append(f"🛑 Closed {closed_count} low-confidence positions")
        
        # 2. Halt new trades
        await self.db.db_execute("""
            INSERT INTO system_status (status_key, status_value, updated_at)
            VALUES ('TRADING_HALTED', 'true', NOW())
            ON DUPLICATE KEY UPDATE status_value = 'true', updated_at = NOW()
        """)
        actions.append("⛔ NEW TRADES HALTED")
        logger.critical("  → Trading system halted")
        
        # 3. Critical alert
        alert_msg = (f"🚨 CRITICAL: System drawdown {drawdown_info['current_drawdown_pct']:.1f}%. "
                    f"{len(positions_to_close) if open_positions else 0} positions closed, trading halted.")
        actions.append(f"🤖 Gemma CRITICAL: {alert_msg}")
        
        self.drawdown_alerts.append({
            "timestamp": datetime.utcnow(),
            "drawdown": drawdown_info['current_drawdown_pct'],
            "actions_taken": len(actions),
        })
        
        return {
            "level": "CRITICAL",
            "drawdown": drawdown_info['current_drawdown_pct'],
            "actions": actions,
            "timestamp": datetime.utcnow(),
        }
    
    async def _get_open_positions_by_risk(self) -> List[Dict]:
        """
        Açık positions'ı risk sırasıyla getir (düşük confidence önce)
        """
        try:
            result = await self.db.db_query("""
                SELECT id, symbol, entry_price, position_size, confidence_score
                FROM simulations
                WHERE status = 'open'
                ORDER BY confidence_score ASC
                LIMIT 50
            """)
            
            return [
                {
                    "id": row[0],
                    "symbol": row[1],
                    "entry_price": float(row[2]),
                    "size": float(row[3]),
                    "confidence": float(row[4]),
                }
                for row in (result or [])
            ]
        except:
            return []
    
    async def _get_current_price(self, symbol: str) -> float:
        """Get latest price for symbol"""
        try:
            result = await self.db.db_query("""
                SELECT price FROM trades
                WHERE symbol = %s
                ORDER BY timestamp DESC
                LIMIT 1
            """, (symbol,))
            
            if result:
                return float(result[0][0])
        except:
            pass
        
        return 0.0
    
    def get_drawdown_health(self) -> Dict[str, Any]:
        """Get current drawdown health status"""
        return {
            "alerts_today": len([a for a in self.drawdown_alerts 
                               if (datetime.utcnow() - a["timestamp"]).days < 1]),
            "recent_alerts": self.drawdown_alerts[-10:],
            "max_allowed_pct": self.max_drawdown_pct,
            "warning_threshold_pct": self.warning_threshold_pct,
        }
