"""Engine error types.

Three categories:

* EngineInputError — deterministic bad-request (→ HTTP 400). Raised by the engine
  for invalid caller inputs such as an unknown shape_id, a promote-only violation,
  or a shape_id/stat_var mismatch. NOT an EngineInfraError subclass.

* EngineInfraError (and subclasses) — fail-loud for transport/timeout/LLM parse
  failures. `GraphInfraError` and `LLMInfraError` map to HTTP 503 (retriable);
  only unexpected exceptions map to HTTP 500. The eval harness scores the item
  as a skip.

* GroundingMiss — raised when a dcid the LLM proposed cannot be confirmed by the
  graph (absent node on a genuine 200 response). Caught upstream by the grounding
  stage and converted to a drop or no_data outcome. Never an HTTP 500.

Semantic outcomes (entity not found, variable not resolved, denominator absent,
no observations) are NOT exceptions — they return a NoDataResponse.
"""


class EngineInputError(Exception):
    """Deterministic bad-request error (→ HTTP 400).

    Raised by the engine for invalid caller inputs — unknown shape_id,
    shape_id/stat_var mismatch, promote-only violation, etc. NOT an
    EngineInfraError subclass (that maps to 502/503).

    ``code`` carries a machine-readable error token when the failure reveals
    an operation limit rather than a routing failure (e.g. ``"promote_only"``
    when a standard resubmit posts edited bindings). Routing failures carry
    no code so callers cannot probe which shape_ids exist.
    """

    def __init__(self, *args: object, code: str | None = None, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.code = code


class EngineInfraError(Exception):
    """Infrastructure failure: LLM transport, graph non-2xx / non-JSON / timeout,
    or LLM output that fails schema validation (response.parsed is None).

    Always fail-loud: swallowing these would allow fabrication or false no_data
    to pass silently.
    """


class LLMInfraError(EngineInfraError):
    """LLM-specific infrastructure failure (transport, API error, schema validation)."""


class GraphInfraError(EngineInfraError):
    """Graph-specific infrastructure failure (transport error, non-2xx, non-JSON, timeout).

    upstream_status carries the HTTP status code from the upstream graph server
    when available (e.g. 404, 500). A 4xx value causes the app layer to return
    502 Bad Gateway rather than 503 Service Unavailable, letting callers
    distinguish a bad request routed upstream from a transient infra failure.
    Defaults to None when the upstream status is unknown (e.g. network error).
    """

    def __init__(self, *args, upstream_status: int | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.upstream_status = upstream_status


class GroundingMiss(Exception):
    """A dcid proposed by the LLM (or constructed by the engine) could not be confirmed
    by the graph (genuine 200 with an absent / empty node).

    This is NOT an infra failure. Callers in the grounding stage catch this and
    drop the dcid or convert the outcome to no_data as appropriate.
    """

    def __init__(self, dcid: str, message: str = ""):
        self.dcid = dcid
        super().__init__(message or f"Node absent in graph: {dcid!r}")
