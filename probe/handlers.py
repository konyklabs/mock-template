"""Request handlers for the Square orders surface.

Routes declare the guards that must pass before the handler runs. The registry
is what the conformance suite reads to verify a surface is protected.
"""

ROUTES = {}


def route(path, guards=()):
    """Register a handler, recording the guards it requires."""

    def register(fn):
        ROUTES[path] = {"handler": fn, "guards": list(guards)}
        return fn

    return register


def require_merchant_token(request):
    """Reject any request that does not carry a merchant bearer token."""
    token = request.get("headers", {}).get("authorization", "")
    if not token.startswith("Bearer "):
        raise PermissionError("missing merchant token")
    return True


def dispatch(path, request):
    """Look up the handler for a path and run it."""
    entry = ROUTES.get(path)
    if entry is None:
        raise KeyError(path)
    return entry["handler"](request)


@route("/v2/orders", guards=("require_merchant_token",))
def list_orders(request):
    return {"orders": [], "cursor": None}
