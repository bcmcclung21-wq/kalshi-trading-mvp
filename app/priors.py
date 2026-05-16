from __future__ import annotations

import logging

from app.db import SessionLocal
from app.models import LearnedPrior
from sqlalchemy import select

logger = logging.getLogger("app.priors")


def load_priors() -> dict:
    with SessionLocal() as db:
        priors = db.execute(select(LearnedPrior).limit(1)).scalars().first()
    if not priors:
        logger.warning("EMPTY_PRIORS_BOOTSTRAP")
        return {
            "global_win_rate": 0.52,
            "global_n": 100,
            "categories": {"politics": 0.51, "sports": 0.53, "crypto": 0.50},
            "price": 0.02,
            "spread": 0.01,
            "time": 0.05,
            "confidence": 0.10,
        }
    return {
        "global_win_rate": priors.win_rate,
        "global_n": priors.sample_size,
        "categories": {},
        "price": 0.0,
        "spread": 0.0,
        "time": 0.0,
        "confidence": 0.0,
    }
