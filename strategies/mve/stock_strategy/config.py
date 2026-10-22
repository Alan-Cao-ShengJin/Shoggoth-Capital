"""Active stock settings and the retained research parameters."""
from dataclasses import dataclass
from pathlib import Path
import json
from . import risk


@dataclass(frozen=True)
class StockConfig:
    pool_size: int = 100
    review_months: int = 3
    momentum_window: int = 231
    momentum_skip: int = 21
    recent_window: int = 63
    min_price: float = 5.0
    max_liquidity_rank: int = 500
    fund: str = "SGOV"
    fund_target: float = 1.0
    fund_refill_deficit: float = .01
    execution_cash_buffer: float = .001
    data_age_days: int = 7
    minimum_order_usd: float = 1.0
    daily_history_days: int = 450
    hourly_history_days: int = 180
    market_data_delay_seconds: int = 16*60  # Supports a 15-minute-delayed feed plus a completion minute.
    paper_only: bool = True


CONFIG = StockConfig()
PARAMETERS = json.loads(Path(__file__).with_name("research_parameters.json").read_text())
for name, value in PARAMETERS["portfolio"].items():
    if hasattr(risk, name):
        setattr(risk, name, value)
risk.check_settings()
