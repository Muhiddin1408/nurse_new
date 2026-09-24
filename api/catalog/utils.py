from rest_framework.request import Request


def _int_param(request: Request, name: str, *, default: int, max_value: int | None = None) -> int:
    raw = request.query_params.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    if value < 0:
        return default
    if max_value is not None:
        value = min(value, max_value)
    return value


def _bool_param(request: Request, name: str) -> bool:
    return request.query_params.get(name, "").lower() in ("1", "true", "yes")

