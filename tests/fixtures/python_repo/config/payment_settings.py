"""Payment configuration settings."""


class PaymentSettings:
    """Configuration for payment processing."""

    def __init__(self, max_retries: int = 3, timeout: float = 30.0):
        self.max_retries = max_retries
        self.timeout = timeout
