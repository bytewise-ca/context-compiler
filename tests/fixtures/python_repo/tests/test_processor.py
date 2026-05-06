"""Tests for PaymentProcessor."""

import pytest
from payments.processor import PaymentProcessor
from config.payment_settings import PaymentSettings


@pytest.fixture
def processor():
    return PaymentProcessor(PaymentSettings(max_retries=3))


def test_process_with_retry_succeeds(processor):
    result = processor.process_with_retry(lambda: "ok")
    assert result == "ok"


def test_reset_clears_attempts(processor):
    processor.attempts = 2
    processor.reset()
    assert processor.attempts == 0
