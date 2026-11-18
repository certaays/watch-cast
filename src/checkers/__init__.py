from src.checkers.base import BaseChecker
from src.checkers.job_feed import JobFeedChecker
from src.checkers.price_drop import PriceDropChecker

CHECKER_REGISTRY: dict[str, type[BaseChecker]] = {
    "job_feed": JobFeedChecker,
    "price_drop": PriceDropChecker,
}


def get_checker(checker_type: str) -> BaseChecker:
    """Instantiate a checker by its configured type name."""
    checker_cls = CHECKER_REGISTRY.get(checker_type)
    if checker_cls is None:
        raise ValueError(f"Unknown checker type: {checker_type!r}")
    return checker_cls()

