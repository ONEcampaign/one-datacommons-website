"""Engine error types.

Two categories only:

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


class EngineInfraError(Exception):
    """Infrastructure failure: LLM transport, graph non-2xx / non-JSON / timeout,
    or LLM output that fails schema validation (response.parsed is None).

    Always fail-loud: swallowing these would allow fabrication or false no_data
    to pass silently.
    """


class LLMInfraError(EngineInfraError):
    """LLM-specific infrastructure failure (transport, API error, schema validation)."""


class GraphInfraError(EngineInfraError):
    """Graph-specific infrastructure failure (transport error, non-2xx, non-JSON, timeout)."""


class GroundingMiss(Exception):
    """A dcid proposed by the LLM (or constructed by the engine) could not be confirmed
    by the graph (genuine 200 with an absent / empty node).

    This is NOT an infra failure. Callers in the grounding stage catch this and
    drop the dcid or convert the outcome to no_data as appropriate.
    """

    def __init__(self, dcid: str, message: str = ""):
        self.dcid = dcid
        super().__init__(message or f"Node absent in graph: {dcid!r}")
