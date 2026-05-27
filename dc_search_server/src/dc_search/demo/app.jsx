/* ============================================================
   Search Comparison — main app
   ============================================================ */

const { useState, useEffect, useCallback } = React;
const STRINGS = window.STRINGS;  // editable UI copy — see strings.js

/* ============================================================
   CONFIG — baked-in defaults.
   ============================================================ */

/* Base URL precedence:
     1. ?base=<url> query param — lets a teammate repoint the shared link at
        local dev or another deploy without editing this file
        (e.g. ?base=http://localhost:8090).
     2. DEFAULT_BASE_URL below.
   All endpoint callers strip a trailing slash, so either form is accepted. */
const DEFAULT_BASE_URL = "https://dc-staging.one.org";

function resolveBaseUrl() {
  try {
    const p = new URLSearchParams(window.location.search).get("base");
    if (p && p.trim()) return p.trim().replace(/\/+$/, "");
  } catch (_) { /* noop — fall back to the default */ }
  return DEFAULT_BASE_URL;
}

const CONFIG = {
  baseUrl:      resolveBaseUrl(),
  detectClient: "ui_query",      // telemetry tag for /api/explore/detect
  detectMode:   "",              // "" | "strict" | "toolformer_rag" | "toolformer_rig"
};

/* ---------------------------------------------------------- */
/* Endpoint callers                                           */
/* ---------------------------------------------------------- */

async function callResolve(baseUrl, query) {
  const url = `${baseUrl.replace(/\/+$/, "")}/core/api/v2/resolve`;
  const t0 = performance.now();
  const r = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Accept": "application/json",
    },
    body: JSON.stringify({
      nodes: query,
      property: "<-description{typeOf:StatisticalVariable}->dcid",
      resolver: "indicator",
    }),
  });
  if (!r.ok) {
    const body = await r.text().catch(() => "");
    throw new Error(`resolve HTTP ${r.status}${body ? ` — ${body.slice(0, 120)}` : ""}`);
  }
  const json = await r.json();
  return { latency: performance.now() - t0, raw: json };
}

/* /api/explore/detect — runs the same NL pipeline as detect-and-fulfill,
   but stops after detection. Returns the insight_ctx:
     { variables, entities, nonPlaceEntities, comparisonEntities,
       comparisonVariables, childEntityType, classifications, context, ... }
   …plus the standard envelope (debug, client, test, session). */
async function callExploreDetect(baseUrl, query, client, mode) {
  const params = new URLSearchParams();
  params.append("q", query);
  if (client) params.append("client", client);
  if (mode)   params.append("mode", mode);
  const url = `${baseUrl.replace(/\/+$/, "")}/api/explore/detect?${params.toString()}`;
  const t0 = performance.now();
  const r = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Accept": "application/json",
    },
    body: JSON.stringify({}),
  });
  if (!r.ok) {
    const body = await r.text().catch(() => "");
    throw new Error(`detect HTTP ${r.status}${body ? ` — ${body.slice(0, 120)}` : ""}`);
  }
  const json = await r.json();

  // Hydrate names client-side. Detect returns only DCIDs (no display names),
  // so to keep this panel useful as a comparison we fetch names from the
  // v2 node API and attach them under `_names` for the renderer to pick up.
  // The cost is folded into the reported latency.
  const dcids = collectDcidsFromDetect(json);
  let names = {};
  if (dcids.length) {
    try {
      names = await fetchNames(baseUrl, dcids);
    } catch (e) {
      // Non-fatal: panel still works without names.
      // eslint-disable-next-line no-console
      console.warn("detect name hydration failed:", e);
    }
  }
  json._names = names;

  return { latency: performance.now() - t0, raw: json };
}

function collectDcidsFromDetect(json) {
  const out = new Set();
  for (const k of [
    "variables", "entities", "nonPlaceEntities",
    "comparisonEntities", "comparisonVariables", "properties",
  ]) {
    for (const v of (json?.[k] || [])) if (typeof v === "string" && v) out.add(v);
  }
  return Array.from(out);
}

/* Fetch display names for a batch of DCIDs via the v2 node API. Returns
   { dcid: "Display Name" }. Topics with no `name` arc fall back to a
   prettified version of the DCID tail.                                   */
async function fetchNames(baseUrl, dcids) {
  if (!dcids.length) return {};
  const url = `${baseUrl.replace(/\/+$/, "")}/core/api/v2/node`;
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", "Accept": "application/json" },
    body: JSON.stringify({ nodes: dcids, property: "->name" }),
  });
  if (!r.ok) throw new Error(`node HTTP ${r.status}`);
  const json = await r.json();
  const data = json?.data || {};
  const out = {};
  for (const dcid of dcids) {
    const nodes = data?.[dcid]?.arcs?.name?.nodes || [];
    const v = nodes[0]?.value;
    out[dcid] = (typeof v === "string" && v.trim()) ? v : "";
  }
  return out;
}

/* ---------------------------------------------------------- */
/* SSE — dc-search streaming                                  */
/* ---------------------------------------------------------- */
/*
   Returns the elapsed time on completion.

   Callbacks receive (payload, elapsed_ms_so_far):
     start, interpretation, places, stage, result, done, error
   The function rejects on transport-level failure OR if the stream ends
   without ever emitting a `done` or `error` event (per the contract).
*/
async function callDcSearchSSE(baseUrl, query, callbacks) {
  const url = `${baseUrl.replace(/\/+$/, "")}/api/dc-search`;
  const t0 = performance.now();

  const r = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Accept": "text/event-stream",
    },
    body: JSON.stringify({ query }),
  });
  if (!r.ok) {
    const body = await r.text().catch(() => "");
    throw new Error(`dc-search SSE HTTP ${r.status}${body ? ` — ${body.slice(0, 120)}` : ""}`);
  }
  const ctype = r.headers.get("content-type") || "";
  if (!ctype.includes("text/event-stream")) {
    // Server didn't honor Accept — fail loudly so we don't silently degrade.
    throw new Error(`dc-search SSE: server returned ${ctype || "no content-type"} (expected text/event-stream)`);
  }
  if (!r.body) throw new Error("dc-search SSE: response has no body stream");

  const reader = r.body.pipeThrough(new TextDecoderStream()).getReader();
  let buf = "";
  let terminated = false;

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += value;

      let boundary;
      while ((boundary = buf.indexOf("\n\n")) !== -1) {
        const frame = buf.slice(0, boundary);
        buf = buf.slice(boundary + 2);
        const msg = parseSseFrame(frame);
        if (!msg) continue;
        if (msg.eventType === "done" || msg.eventType === "error") terminated = true;
        const cb = callbacks[msg.eventType];
        if (cb) {
          try { cb(msg.payload, performance.now() - t0); }
          catch (handlerErr) {
            // Surface but don't kill the stream — render bugs shouldn't drop the connection.
            console.error("SSE handler error for", msg.eventType, handlerErr);
          }
        }
      }
    }
  } finally {
    try { reader.releaseLock(); } catch (_) { /* noop */ }
  }

  if (!terminated) {
    throw new Error("dc-search SSE: stream ended without `done` or `error` event");
  }
  return performance.now() - t0;
}

function parseSseFrame(frame) {
  let eventType = null;
  const dataLines = [];
  for (const rawLine of frame.split("\n")) {
    const line = rawLine.endsWith("\r") ? rawLine.slice(0, -1) : rawLine;
    if (!line || line.startsWith(":")) continue;           // blank + comment (heartbeat)
    if (line.startsWith("event:"))      eventType = line.slice(6).trim();
    else if (line.startsWith("data:"))  dataLines.push(line.slice(5).replace(/^ /, ""));
  }
  if (!dataLines.length) return null;
  const data = dataLines.join("\n");
  let payload;
  try { payload = JSON.parse(data); } catch (_) { return null; }
  return { eventType: eventType ?? payload.type, payload };
}

/* ---------------------------------------------------------- */
/* SSE dispatcher — drives the dc-search panel state          */
/* ---------------------------------------------------------- */
/*
   Builds up the `raw` SearchResponse-shaped object progressively as events
   arrive. The DcSearchPanel can render at any time — null entries in
   `raw.answers` are rendered as skeleton slots.
*/
function runDcSearchSSE(baseUrl, query, setState) {
  // Seed an empty raw scaffold so the panel can render immediately
  setState({
    status: "loading",
    streaming: true,
    expectedResults: null,
    latency: null,
    error: null,
    raw: {
      query, mode: null,
      interpretation: null,
      answers: [],
      ask: null,
      telemetry: null,
      elapsed_s: null,
      terminated_by: null,
      truncated: false,
      timed_out: false,
    },
  });

  const callbacks = {
    start: (p) => setState(prev => ({
      ...prev,
      raw: { ...prev.raw, mode: p.mode, query: p.query },
    })),

    interpretation: (p) => setState(prev => {
      const slots = Array(Math.max(p.expected_results || 0, 0)).fill(null);
      return {
        ...prev,
        expectedResults: p.expected_results,
        raw: {
          ...prev.raw,
          interpretation: {
            variables: p.variables || [],
            // places may have already arrived (rare but allowed); keep them
            places: (prev.raw.interpretation && prev.raw.interpretation.places) || [],
            dates: p.dates || [],
            // preserve contained_in if a prior `places` event already set it
            contained_in: !!(p.contained_in || (prev.raw.interpretation && prev.raw.interpretation.contained_in)),
          },
          // pre-allocate skeleton slots
          answers: slots,
          truncated: p.truncated || prev.raw.truncated,
        },
      };
    }),

    places: (p) => setState(prev => ({
      ...prev,
      raw: {
        ...prev.raw,
        interpretation: {
          ...(prev.raw.interpretation || { variables: [], dates: [], contained_in: false }),
          places: p.places || [],
        },
      },
    })),

    stage: (p) => setState(prev => ({ ...prev, stage: p.stage })),

    result: (p) => setState(prev => {
      const answers = [...(prev.raw.answers || [])];
      // grow if needed (simple path or out-of-bound index)
      while (answers.length <= p.index) answers.push(null);

      let storedAnswer;
      if (p.outcome_kind === "answer") {
        storedAnswer = {
          ...p.answer,
          variable_label: p.variable_label || (p.answer && p.answer.variable_label) || null,
          outcome_kind: "answer",
        };
      } else {
        // clarification: p.answer is an AskClarification — store with outcome_kind so the
        // card renders an inline clarification instead of variables
        storedAnswer = {
          variable_label: p.variable_label,
          outcome_kind: "clarification",
          answer_kind: "variables",
          variables: [],
          clarification: p.answer,
        };
      }
      answers[p.index] = storedAnswer;
      return { ...prev, raw: { ...prev.raw, answers } };
    }),

    done: (p, latency) => setState(prev => {
      // Drop any leftover skeletons that never got filled (timed_out branches stay as nulls,
      // which the panel renders as "timed out" tiles when timed_out is true).
      const answers = [...(prev.raw.answers || [])];
      return {
        ...prev,
        status: "ok",
        streaming: false,
        latency,
        raw: {
          ...prev.raw,
          answers,
          telemetry: p.telemetry || null,
          elapsed_s: p.elapsed_s,
          terminated_by: p.terminated_by,
          truncated: !!(p.truncated || prev.raw.truncated),
          timed_out: !!p.timed_out,
          ask: p.ask || null,
        },
      };
    }),

    error: (p) => setState(prev => ({
      ...prev,
      status: "error",
      streaming: false,
      error: p.detail || STRINGS.common.unknownError,
    })),
  };

  callDcSearchSSE(baseUrl, query, callbacks)
    .catch(err => {
      setState(prev => {
        // If we already got `done` and `status` flipped to "ok", a late reader-close error
        // is benign — keep the success state.
        if (prev.status === "ok") return prev;
        return {
          ...prev,
          status: "error",
          streaming: false,
          error: String(err.message || err),
        };
      });
    });
}

/* ---------------------------------------------------------- */
/* Per-method state hook                                      */
/* ---------------------------------------------------------- */

function useMethodState() {
  // status: 'idle' | 'loading' | 'ok' | 'error'
  return useState({ status: "idle", raw: null, latency: null, error: null });
}

/* ---------------------------------------------------------- */
/* App                                                        */
/* ---------------------------------------------------------- */

function App() {
  const [query, setQuery] = useState(window.SAMPLE_QUERIES[0].q);
  const [customQuery, setCustomQuery] = useState("");

  const [resolveState,  setResolveState]  = useMethodState();
  const [dcSearchState, setDcSearchState] = useMethodState();
  const [detectState,   setDetectState]   = useMethodState();

  const runAll = useCallback((q) => {
    if (!q || !q.trim()) return;
    const qTrim = q.trim();

    // reset
    setResolveState({ status: "loading", raw: null, latency: null, error: null });
    setDcSearchState({
      status: "loading", raw: null, latency: null, error: null,
      streaming: true, expectedResults: null,
    });
    setDetectState({ status: "loading", raw: null, latency: null, error: null });

    const baseUrl = CONFIG.baseUrl;

    callResolve(baseUrl, qTrim)
      .then(({ raw, latency }) => setResolveState({ status: "ok", raw, latency, error: null }))
      .catch(err => setResolveState({ status: "error", raw: null, latency: null, error: String(err.message || err) }));

    runDcSearchSSE(baseUrl, qTrim, setDcSearchState);

    callExploreDetect(baseUrl, qTrim, CONFIG.detectClient, CONFIG.detectMode)
      .then(({ raw, latency }) => setDetectState({ status: "ok", raw, latency, error: null }))
      .catch(err => setDetectState({ status: "error", raw: null, latency: null, error: String(err.message || err) }));
  }, []);

  // Run on mount and whenever the selected query changes
  useEffect(() => {
    runAll(query);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query]);

  const endpoints = {
    resolve:  `${CONFIG.baseUrl}/core/api/v2/resolve`,
    dcsearch: `${CONFIG.baseUrl}/api/dc-search`,
    detect:   `${CONFIG.baseUrl}/api/explore/detect`,
  };

  return (
    <div className="app">
      {/* ---------- Header ---------- */}
      <header className="header">
        <h1 className="header-title">
          {STRINGS.app.title}
          <span className="header-title-rule" aria-hidden="true" />
        </h1>
        <p className="header-sub">{STRINGS.app.tagline}</p>
        <details className="howitworks">
          <summary className="howitworks-summary">{STRINGS.app.howItWorks.summary}</summary>
          <div className="howitworks-content">
            <p className="howitworks-body">{STRINGS.app.howItWorks.body}</p>
            <p className="howitworks-compared-label">{STRINGS.app.howItWorks.comparedLabel}</p>
            <ul className="howitworks-compared">
              {STRINGS.app.howItWorks.compared.map(c => (
                <li key={c.name}><strong>{c.name}</strong> — {c.desc}</li>
              ))}
            </ul>
          </div>
        </details>
      </header>

      {/* ---------- Query bar ---------- */}
      <QueryBar
        query={query}
        setQuery={setQuery}
        customQuery={customQuery}
        setCustomQuery={setCustomQuery}
        onRunCustom={() => {
          if (customQuery.trim()) setQuery(customQuery.trim());
        }}
      />

      {/* ---------- Results ---------- */}
      <div className="layout-side">
        <window.DcSearchPanel state={dcSearchState} endpoint={endpoints.dcsearch} query={query} />
        <window.ResolvePanel  state={resolveState}  endpoint={endpoints.resolve}  query={query} />
        <window.DetectPanel   state={detectState}   endpoint={endpoints.detect}   query={query} />
      </div>
    </div>
  );
}

/* ---------------------------------------------------------- */
/* Query Bar                                                  */
/* ---------------------------------------------------------- */

function QueryBar({ query, setQuery, customQuery, setCustomQuery, onRunCustom }) {
  return (
    <div className="querybar">
      <div className="querybar-input">
        <input
          type="text"
          placeholder={STRINGS.queryBar.placeholder}
          value={customQuery}
          onChange={e => setCustomQuery(e.target.value)}
          onKeyDown={e => { if (e.key === "Enter") onRunCustom(); }}
          aria-label={STRINGS.queryBar.inputAria}
        />
        <button
          className="btn-run"
          onClick={onRunCustom}
          disabled={!customQuery.trim()}
        >
          {STRINGS.queryBar.run}
        </button>
      </div>

      <div className="querybar-suggest" aria-label={STRINGS.queryBar.suggestAria}>
        <span className="querybar-suggest-label">{STRINGS.queryBar.tryLabel}</span>
        {window.SAMPLE_QUERIES.map((s, i) => (
          <React.Fragment key={s.q}>
            <button
              className={`suggest ${query === s.q ? "is-active" : ""}`}
              onClick={() => { setCustomQuery(s.q); setQuery(s.q); }}
            >
              {s.q}
            </button>
            {i < window.SAMPLE_QUERIES.length - 1 && <span className="suggest-sep">·</span>}
          </React.Fragment>
        ))}
      </div>

    </div>
  );
}

/* ---------------------------------------------------------- */
/* Mount                                                      */
/* ---------------------------------------------------------- */

const root = ReactDOM.createRoot(document.getElementById("root"));
root.render(<App />);
