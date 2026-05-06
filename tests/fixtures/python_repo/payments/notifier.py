"""Payment event notifier."""

from payments.processor import PaymentProcessor


class PaymentNotifier:
    """Sends payment notifications with retry support."""

    def __init__(self, processor: PaymentProcessor):
        self.processor = processor

    def notify(self, event: str, payload: bytes):
        """Send a payment notification for the given event."""
        return self.processor.process_with_retry(
            lambda: self._send(event, payload)
        )

    def _send(self, event: str, payload: bytes):
        pass
