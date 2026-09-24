import logging

logger = logging.getLogger(__name__)


class PaymentError(Exception):
    pass


class AlreadyProcessed(PaymentError):
    """Bu to'lov allaqachon yakuniy holatda — qayta ishlov berish shart emas."""
