from app.strategies.base import Pattern

_REGISTRY: dict[str, Pattern] = {}


def register_pattern(cls):
    instance = cls()
    _REGISTRY[instance.id] = instance
    return cls


def get_all_patterns() -> list[Pattern]:
    return list(_REGISTRY.values())


def get_pattern(pattern_id: str) -> Pattern | None:
    return _REGISTRY.get(pattern_id)
