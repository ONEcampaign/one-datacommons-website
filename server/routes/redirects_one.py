from flask import redirect
from flask import request

_REDIRECT_TARGET = 'https://data.one.org/tools'

_PRESERVED_PREFIXES = (
    '/api/',
    '/core/api/',
)

_PRESERVED_PATHS = frozenset([
    '/healthz',
])


def should_redirect(path: str) -> bool:
  if any(path.startswith(p) for p in _PRESERVED_PREFIXES):
    return False
  normalized = path.rstrip('/') or '/'
  return normalized not in _PRESERVED_PATHS


def install(app) -> None:

  @app.before_request
  def _maybe_redirect_to_data_one_org():
    if should_redirect(request.path):
      return redirect(_REDIRECT_TARGET, code=301)
    return None
