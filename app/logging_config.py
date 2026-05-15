"""Rate-limited logging to prevent Railway 500 logs/sec cap."""
import logging
import sys


class RateLimitedLogFilter(logging.Filter):
    def __init__(self, max_per_cycle=50):
        super().__init__()
        self.max_per_cycle = max_per_cycle
        self.cycle_count = 0

    def filter(self, record):
        msg = record.getMessage()
        # Block ALL score_failed logs after threshold (they flood at 1000s/sec)
        if 'score_failed' in msg:
            self.cycle_count += 1
            if self.cycle_count > self.max_per_cycle:
                return False
        elif 'cycle_complete:' in msg or 'refresh_complete' in msg:
            self.cycle_count = 0
        return True


def configure_logging():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        stream=sys.stdout,
    )
    root = logging.getLogger()
    root.addFilter(RateLimitedLogFilter(max_per_cycle=50))
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
