from flask import redirect
from flask import request

_HOMEPAGE_TARGET = 'https://data.one.org'
_DEFAULT_TARGET = 'https://data.one.org/tools'

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


def _target_for(path: str) -> str:
  # Root goes to the new homepage; every other redirected path lands on /tools.
  return _HOMEPAGE_TARGET if (path.rstrip('/') or '/') == '/' else _DEFAULT_TARGET


def install(app) -> None:

  @app.before_request
  def _maybe_redirect_to_data_one_org():
    if should_redirect(request.path):
      return redirect(_target_for(request.path), code=301)
    return None
