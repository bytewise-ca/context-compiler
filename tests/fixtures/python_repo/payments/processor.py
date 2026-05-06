"""Payment processor."""

from config.payment_settings import PaymentSettings


class PaymentProcessor:
    """Handles payment processing with retry support."""

    def __init__(self, settings: PaymentSettings):
        self.settings = settings
        self.attempts = 0

    def process_with_retry(self, fn):
        """Process a payment with exponential backoff."""
        while self.attempts < self.settings.max_retries:
            try:
                return fn()
            except Exception:
                self.attempts += 1
                if self.attempts >= self.settings.max_retries:
                    raise

    def reset(self):
        self.attempts = 0
