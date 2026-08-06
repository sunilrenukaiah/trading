"""Shared bracket level validation for recommendations and trade plans."""


def is_valid_bracket_levels(buy: float, target: float, stop: float) -> bool:
    """Bracket requires stop below entry and target above entry."""
    return stop < buy < target
