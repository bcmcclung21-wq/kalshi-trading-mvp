"""Rate-limited logging to prevent Railway 500 logs/sec cap."""
import logging
import sys


class RateLimitedLogFilter(logging.Filter):
    def __init__(self, max_per_cycle=100):
        super().__init__()
        self.max_per_cycle = max_per_cycle
        self.cycle_count = 0

    def filter(self, record):
        msg = record.getMessage()
        if 'candidate_model ticker=' in msg and 'REJECTED' not in msg:
            self.cycle_count += 1
            if self.cycle_count > self.max_per_cycle:
                return False
        elif 'cycle_complete:' in msg:
            self.cycle_count = 0
        return True


def configure_logging():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        stream=sys.stdout,
    )
    root = logging.getLogger()
    root.addFilter(RateLimitedLogFilter(max_per_cycle=100))
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
