"""Advisory checks on a proposed purchase: warnings only, they never write or block anything."""
from ledgerline.advisory.checks import review_advice

__all__ = ["review_advice"]
