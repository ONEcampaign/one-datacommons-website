/* ============================================================
   resolvekit demo — main app
   ------------------------------------------------------------
   Holds page state, fires fetch() calls to the real resolvekit
   backend, and builds the `vm` view-model the section
   components render. Compare also fires a live DC Resolve call
   from the browser so its latency is client-observed.

   API base:
     Same-origin by default (served by the FastAPI app).
     ?base=<url> overrides for cross-host testing.
   ============================================================ */

const { useState, useEffect, useCallback, useRef } = React;
const UI = window.RK_UI;

/* ---------------------------------------------------------- */
/* Base URL — same-origin default; ?base= override            */
/* ---------------------------------------------------------- */

/* Default is "" so API calls use the same host as the page.
   ?base=http://localhost:7800 repoints to a local dev server,
   or any other host (CORS is open on the backend). */
const DEFAULT_BASE_URL = "";

/* Item 13: allowlist for ?base= — only localhost / 127.0.0.1 / *.one.org.
   Silently drops anything else to prevent open-redirect fetches. */
const _BASE_ALLOWLIST = /^(https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?|https?:\/\/([a-z0-9-]+\.)*one\.org)(\/|$)/i;

function resolveBaseUrl() {
  try {
    const p = new URLSearchParams(window.location.search).get("base");
    if (p && p.trim()) {
      const candidate = p.trim().replace(/\/+$/, "");
      if (_BASE_ALLOWLIST.test(candidate + "/")) return candidate;
      console.warn("[resolvekit] ?base= value rejected (not in allowlist):", candidate);
    }
  } catch (_) { /* fall back to default */ }
  return DEFAULT_BASE_URL;
}

const BASE = resolveBaseUrl();
const API_ROOT = `${BASE}/api/resolve-demo/api`;

/* ---------------------------------------------------------- */
/* Core fetch helper — two-shape error model                  */
/* ---------------------------------------------------------- */

/* Throws on transport errors (!r.ok) and on app errors
   (d.status === "error" | "unavailable"). Returns {raw, latency}. */
async function callApi(endpoint, body) {
  const url = `${API_ROOT}/${endpoint}`;
  const t0 = performance.now();
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", "Accept": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    const text = await r.text().catch(() => "");
    throw new Error(`HTTP ${r.status}${text ? ` — ${text.slice(0, 120)}` : ""}`);
  }
  const d = await r.json();
  if (d.status === "error" || d.status === "unavailable") {
    throw new Error(d.detail || `${endpoint} returned status:${d.status}`);
  }
  return { raw: d, latency: performance.now() - t0 };
}

/* ---------------------------------------------------------- */
/* DC Resolve — fired from the browser for real network timing */
/* ---------------------------------------------------------- */

/* Two-step Data Commons resolution so the DC column can emit name / iso2 /
   wikidata, not just a dcid. Step 1 resolves the name to a dcid
   (`<-description->dcid`); step 2 reads the requested property off that dcid via
   the Node API (`->name` / `->isoCode` / `->wikidataId`). The reported latency
   spans BOTH round-trips — that's the honest cost of going name→iso/name on DC.
   `dcid` is one hop; `iso3` has no DC equivalent (its isoCode is 2-letter). */
const _DC_NODE_PROP = { name: "name", iso2: "isoCode", wikidata: "wikidataId" };

async function callDcResolveTarget(query, to) {
  const base = BASE || "https://dc-staging.one.org";
  const post = async (path, body) => {
    const r = await fetch(`${base}/core/api/v2/${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Accept": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      const text = await r.text().catch(() => "");
      throw new Error(`dc ${path} HTTP ${r.status}${text ? ` — ${text.slice(0, 120)}` : ""}`);
    }
    return r.json();
  };

  if (to === "iso3") {
    /* DC has no ISO3 (its isoCode is 2-letter) — don't spend a round-trip. */
    return { raw: { supported: false, value: null, dcid: null, note: "no ISO3 (DC isoCode is 2-letter)" }, latency: 0 };
  }

  const t0 = performance.now();
  /* Step 1 — name → dcid */
  const res = await post("resolve", { nodes: [query], property: "<-description->dcid" });
  const dcid = (res.entities && res.entities[0] && res.entities[0].candidates
    && res.entities[0].candidates[0] && res.entities[0].candidates[0].dcid) || null;

  const prop = _DC_NODE_PROP[to];
  if (to === "dcid" || !prop || !dcid) {
    /* dcid is the one-hop answer; also short-circuit when step 1 found nothing. */
    return { raw: { supported: true, value: dcid, dcid }, latency: performance.now() - t0 };
  }

  /* Step 2 — dcid → requested property via the Node API */
  const node = await post("node", { nodes: [dcid], property: `->${prop}` });
  const arcs = node && node.data && node.data[dcid] && node.data[dcid].arcs;
  const valNodes = arcs && arcs[prop] && arcs[prop].nodes;
  const value = (valNodes && valNodes[0] && valNodes[0].value) || null;
  return { raw: { supported: true, value, dcid }, latency: performance.now() - t0 };
}

/* ---------------------------------------------------------- */
/* Per-section state shape                                    */
/* ---------------------------------------------------------- */

function makeIdle() {
  return { status: "idle", raw: null, latency: null, error: null };
}

function makeLoading(prev) {
  return { ...prev, status: "loading", error: null };
}

/* ---------------------------------------------------------- */
/* Debounce helper                                            */
/* ---------------------------------------------------------- */

/* Returns a ref-stable debounced version of fn.
   Works in the zero-build Babel context (no lodash). */
function useDebounced(fn, delay) {
  const timerRef = useRef(null);
  const fnRef = useRef(fn);
  fnRef.current = fn;
  return useCallback((...args) => {
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => fnRef.current(...args), delay);
  }, [delay]);
}

/* ---------------------------------------------------------- */
/* Initial state                                              */
/* ---------------------------------------------------------- */

const INITIAL = {
  resolveQuery: "Cote dIvoire",
  showExplain: false, showCodeR: false,
  /* explainQuery: the query that was resolved when explain was last fetched.
     Explain is fetched with the resolved query at that time, not the live input. */
  explainQuery: null,

  suggestQuery: "", suggestScope: "all", showCodeS: false,

  parseText: "Leaders from Kenya, Uganda and the United States met with the European Union and NATO; talks on the Congo continued.",
  showCodeP: false,

  group: "European Union", asOf: 2026, showCodeG: false,
  region: "Eastern Africa",

  byodText: "place,value\nBrasil,120\nCIV,88\nRepublic of Korea,210\nDeutschland,540\nGermny,12\nU.K.,300\nCôte d'Ivoire,77\nAfrican Union,55\nNairobi,33\nCongo,9\nn/a,0",
  byodFileName: "", byodCol: "", showCodeB: false,

  /* Bring-your-own-data: a custom record dictionary (names resolvekit has never
     seen) + a query resolved against a Resolver.from_records() built on the fly. */
  byodCustomText: `id,name,aliases,code
ACME-01,Acme Health Initiative,"AHI;Acme Health;Acme",AHI
ACME-02,Riverbend Foundation,"Riverbend;RBF",RBF
ACME-03,Sahel Resilience Program,"SRP;Sahel Program",SRP
ACME-04,Pacific Climate Fund,"PCF;Pacific Fund",PCF
ACME-05,Andean Water Trust,"AWT;Andean Water",AWT`,
  byodCustomQuery: "Riverbend", showCodeC: false,

  compareQuery: "Cote dIvoire",
  compareTo: "dcid"
};

/* ---------------------------------------------------------- */
/* App                                                        */
/* ---------------------------------------------------------- */

function App() {
  const [s, setS] = useState(INITIAL);
  const set = useCallback((patch) => setS(prev => ({ ...prev, ...patch })), []);

  /* Per-section async state */
  const [resolveState,  setResolveState]  = useState(makeIdle());
  const [explainState,  setExplainState]  = useState(makeIdle());
  const [suggestState,  setSuggestState]  = useState(makeIdle());
  const [parseState,    setParseState]    = useState(makeIdle());
  const [graphState,    setGraphState]    = useState(makeIdle());
  const [bulkState,     setBulkState]     = useState(makeIdle());
  const [compareState,  setCompareState]  = useState(makeIdle());
  const [dcResState,    setDcResState]    = useState(makeIdle());
  const [byodCustomState, setByodCustomState] = useState(makeIdle());

  /* Item 12: monotonic request-id counters per debounced section.
     A response whose id != current is a stale race and is dropped. */
  const resolveSeq  = useRef(0);
  const explainSeq  = useRef(0);
  const suggestSeq  = useRef(0);
  const parseSeq    = useRef(0);
  const graphSeq    = useRef(0);
  const bulkSeq     = useRef(0);
  const compareSeq  = useRef(0);
  const byodCustomSeq = useRef(0);

  /* ---- Resolve ---- */
  const fetchResolve = useCallback((q) => {
    if (!q.trim()) { setResolveState(makeIdle()); return; }
    const seq = ++resolveSeq.current;
    setResolveState(prev => makeLoading(prev));
    callApi("resolve", { query: q })
      .then(({ raw, latency }) => {
        if (seq !== resolveSeq.current) return; // stale — drop
        setResolveState({ status: "ok", raw, latency, error: null });
      })
      .catch(err => {
        if (seq !== resolveSeq.current) return; // stale — drop
        setResolveState({ status: "error", raw: null, latency: null, error: String(err.message || err) });
      });
  }, []);

  const fetchResolveDebounced = useDebounced(fetchResolve, 300);

  const doResolve = useCallback((q) => {
    set({ resolveQuery: q, showExplain: false, explainQuery: null });
    fetchResolveDebounced(q);
  }, [set, fetchResolveDebounced]);

  /* ---- Explain ---- */
  const fetchExplain = useCallback((q) => {
    if (!q) return;
    const seq = ++explainSeq.current;
    setExplainState(prev => makeLoading(prev));
    /* Item 11: pass q as-is (the original resolved input query, captured at
       resolve time — not canonical_name, which may differ or be null). */
    callApi("explain", { query: q })
      .then(({ raw, latency }) => {
        if (seq !== explainSeq.current) return; // stale — drop
        setExplainState({ status: "ok", raw, latency, error: null });
      })
      .catch(err => {
        if (seq !== explainSeq.current) return; // stale — drop
        setExplainState({ status: "error", raw: null, latency: null, error: String(err.message || err) });
      });
  }, []);

  /* ---- Suggest ---- */
  const fetchSuggest = useCallback((prefix, scope) => {
    if (!prefix.trim()) { setSuggestState(makeIdle()); return; }
    const seq = ++suggestSeq.current;
    setSuggestState(prev => makeLoading(prev));
    callApi("suggest", { prefix, top_k: 10, scope: scope || "all" })
      .then(({ raw, latency }) => {
        if (seq !== suggestSeq.current) return; // stale — drop
        setSuggestState({ status: "ok", raw, latency, error: null });
      })
      .catch(err => {
        if (seq !== suggestSeq.current) return; // stale — drop
        setSuggestState({ status: "error", raw: null, latency: null, error: String(err.message || err) });
      });
  }, []);

  const fetchSuggestDebounced = useDebounced(fetchSuggest, 300);

  /* ---- Parse ---- */
  const fetchParse = useCallback((text) => {
    const seq = ++parseSeq.current;
    setParseState(prev => makeLoading(prev));
    callApi("parse", { text })
      .then(({ raw, latency }) => {
        if (seq !== parseSeq.current) return; // stale — drop
        setParseState({ status: "ok", raw, latency, error: null });
      })
      .catch(err => {
        if (seq !== parseSeq.current) return; // stale — drop
        setParseState({ status: "error", raw: null, latency: null, error: String(err.message || err) });
      });
  }, []);

  const fetchParseDebounced = useDebounced(fetchParse, 300);

  /* ---- Graph ---- */
  const fetchGraph = useCallback((group, region, asOfYear) => {
    const seq = ++graphSeq.current;
    setGraphState(prev => makeLoading(prev));
    callApi("graph", { group, region, as_of_year: asOfYear })
      .then(({ raw, latency }) => {
        if (seq !== graphSeq.current) return; // stale — drop
        setGraphState({ status: "ok", raw, latency, error: null });
      })
      .catch(err => {
        if (seq !== graphSeq.current) return; // stale — drop
        setGraphState({ status: "error", raw: null, latency: null, error: String(err.message || err) });
      });
  }, []);

  /* ---- Bulk (Data section) ---- */
  const fetchBulk = useCallback((csvText, column) => {
    const seq = ++bulkSeq.current;
    setBulkState(prev => makeLoading(prev));
    callApi("bulk", { csv_text: csvText, column: column || "" })
      .then(({ raw, latency }) => {
        if (seq !== bulkSeq.current) return; // stale — drop
        setBulkState({ status: "ok", raw, latency, error: null });
      })
      .catch(err => {
        if (seq !== bulkSeq.current) return; // stale — drop
        setBulkState({ status: "error", raw: null, latency: null, error: String(err.message || err) });
      });
  }, []);

  const fetchBulkDebounced = useDebounced(fetchBulk, 400);

  /* ---- Compare ---- */
  const fetchCompare = useCallback((q, to) => {
    if (!q.trim()) { setCompareState(makeIdle()); setDcResState(makeIdle()); return; }
    const seq = ++compareSeq.current;
    /* Server-side (resolvekit/coco/hdx) */
    setCompareState(prev => makeLoading(prev));
    callApi("compare", { query: q, to: to || "iso3" })
      .then(({ raw, latency }) => {
        if (seq !== compareSeq.current) return; // stale — drop
        setCompareState({ status: "ok", raw, latency, error: null });
      })
      .catch(err => {
        if (seq !== compareSeq.current) return; // stale — drop
        setCompareState({ status: "error", raw: null, latency: null, error: String(err.message || err) });
      });
    /* Browser-side DC Resolve (+ Node) — genuinely networked, target-aware */
    setDcResState(prev => makeLoading(prev));
    callDcResolveTarget(q, to || "iso3")
      .then(({ raw, latency }) => {
        if (seq !== compareSeq.current) return; // stale — drop
        setDcResState({ status: "ok", raw, latency, error: null });
      })
      .catch(err => {
        if (seq !== compareSeq.current) return; // stale — drop
        setDcResState({ status: "error", raw: null, latency: null, error: String(err.message || err) });
      });
  }, []);

  /* ---- BYOD (custom records → from_records) ---- */
  const fetchByodCustom = useCallback((recordsCsv, query) => {
    const seq = ++byodCustomSeq.current;
    setByodCustomState(prev => makeLoading(prev));
    callApi("byod", { records_csv: recordsCsv, query })
      .then(({ raw, latency }) => {
        if (seq !== byodCustomSeq.current) return; // stale — drop
        setByodCustomState({ status: "ok", raw, latency, error: null });
      })
      .catch(err => {
        if (seq !== byodCustomSeq.current) return; // stale — drop
        setByodCustomState({ status: "error", raw: null, latency: null, error: String(err.message || err) });
      });
  }, []);

  const fetchByodCustomDebounced = useDebounced(fetchByodCustom, 350);

  /* ---- Mount-time fetches ---- */
  useEffect(() => {
    fetchResolve(INITIAL.resolveQuery);
    fetchParse(INITIAL.parseText);
    fetchGraph(INITIAL.group, INITIAL.region, INITIAL.asOf);
    fetchBulk(INITIAL.byodText, INITIAL.byodCol);
    fetchCompare(INITIAL.compareQuery, INITIAL.compareTo);
    fetchByodCustom(INITIAL.byodCustomText, INITIAL.byodCustomQuery);
  /* eslint-disable-next-line react-hooks/exhaustive-deps */
  }, []);

  /* ---- File / paste handlers (Data section) ---- */
  const loadCsv = useCallback((text, name) => {
    set({ byodText: text, byodFileName: name || "", byodCol: "" });
    fetchBulkDebounced(text, "");
  }, [set, fetchBulkDebounced]);

  const onByodFile = useCallback((e) => {
    const f = e.target.files && e.target.files[0];
    if (!f) return;
    const rd = new FileReader();
    rd.onload = () => loadCsv(String(rd.result), f.name);
    rd.readAsText(f);
  }, [loadCsv]);

  const onByodDrop = useCallback((e) => {
    e.preventDefault();
    const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    if (!f) return;
    const rd = new FileReader();
    rd.onload = () => loadCsv(String(rd.result), f.name);
    rd.readAsText(f);
  }, [loadCsv]);

  /* ---- View-model helpers ---- */

  /* Resolve result — built from fetched raw */
  const resRaw = resolveState.raw;
  const resStatus = resRaw ? resRaw.status : (resolveState.status === "loading" ? "loading" : "empty");

  /* statusLabel / statusColor — pure presentation, kept client-side */
  function resolveStatusPresentation(status) {
    if (status === "resolved")  return { statusLabel: "RESOLVED",  statusColor: "#108479" };
    if (status === "ambiguous") return { statusLabel: "AMBIGUOUS", statusColor: "#9A6700" };
    if (status === "no_match")  return { statusLabel: "NO MATCH",  statusColor: "#8A8A84" };
    return { statusLabel: "", statusColor: "#A6A6A0" };
  }

  /* Map server snake_case candidates to camelCase for sections.jsx */
  function mapCandidates(cands) {
    if (!cands) return [];
    return cands.map(c => ({
      id: c.entity_id,
      name: c.name,
      barW: c.bar_w,
      confPct: c.conf_pct,
    }));
  }

  /* Build the `res` object for sections.jsx */
  const pres = resolveStatusPresentation(resStatus);
  const res = resRaw ? {
    ...resRaw,
    /* keep camelCase surface for sections.jsx which uses res.confPct / res.barW */
    confPct: resRaw.conf_pct,
    barW: resRaw.bar_w,
    statusLabel: pres.statusLabel,
    statusColor: pres.statusColor,
    candidates: mapCandidates(resRaw.candidates),
    /* explain_text from /explain endpoint; fallback to inline if server included it */
    explainText: explainState.status === "ok" ? explainState.raw.explain_text : (resRaw.explain_text || ""),
    /* code snippet for the code block */
    code: buildResolveCode(s.resolveQuery, resRaw),
  } : { status: resStatus, statusLabel: pres.statusLabel, statusColor: pres.statusColor };

  function buildResolveCode(q, r) {
    if (!r) return "";
    if (r.status === "no_match") {
      return `import resolvekit as rk\n\nr = rk.resolve("${q}")\nr.status        # ResolutionStatus.NO_MATCH\nr.entity_id     # None\nr.reasons       # [ReasonCode.${(r.reason || "").toUpperCase()}]`;
    }
    if (r.status === "ambiguous") {
      return `import resolvekit as rk\n\nr = rk.resolve("${q}")\nr.is_ambiguous     # True\nr.confidence       # None\n[(c.entity_id, round(c.confidence, 3)) for c in r.candidates[:2]]\n\nrk.resolve_id("${q}", on_ambiguous="best")   # top candidate`;
    }
    const tier = r.match_tier || "";
    if (tier === "exact_code") {
      return `import resolvekit as rk\n\nr = rk.resolve("${q}")\nr.entity_id        # "${r.entity_id}"\nr.confidence       # ${r.confidence != null ? r.confidence.toFixed(3) : "null"}\nr.match_tier       # "${tier}"\n\nrk.resolve("${q}", to="iso3")\ndf["iso3"] = rk.bulk(values=df["country"], to="iso3")`;
    }
    return `import resolvekit as rk\n\nr = rk.resolve("${q}")\nr.entity_id        # "${r.entity_id}"\nr.confidence       # ${r.confidence != null ? r.confidence.toFixed(3) : "null"}\nr.match_tier       # "${tier}"\nr.explain(verbosity="full").as_text()   # scorecard\n\ndf["iso3"] = rk.bulk(values=df["country"], to="iso3")`;
  }

  const presets = ["Brasil", "Germny", "Côte d'Ivoire", "African Union", "AFD", "Paris", "Nairobi", "Congo", "n/a"]
    .map(p => ({ label: p, on: () => doResolve(p) }));

  /* Suggest */
  const sugRaw = suggestState.raw;
  const suggestions = sugRaw ? (sugRaw.results || []) : [];
  const suggestHeader = suggestState.status === "ok"
    ? (sugRaw.header || `suggest("${s.suggestQuery.trim()}") → ${suggestions.length}`)
    : (s.suggestQuery.trim() ? `suggest("${s.suggestQuery.trim()}")…` : "suggest(…)");

  /* Parse */
  const parseRaw = parseState.raw;
  const parseSegments = parseRaw ? (parseRaw.segments || []) : [];
  const parseEntities = parseRaw
    ? (parseRaw.entities || []).map(e => ({
        ...e,
        idColor: e.id_color,
        confPct: e.conf_pct,
      }))
    : [];
  const parseCount = parseRaw ? (parseRaw.count || 0) : 0;

  const parsePresetList = [
    { label: "Diplomacy", text: "Leaders from Kenya, Uganda and the United States met with the European Union and NATO; talks on the Congo continued." },
    { label: "Trade",     text: "Exports from Brasil and Deutschland to Japan and the Republic of Korea rose this quarter." },
    { label: "Cities", text: "The summit in Nairobi gathered delegates from Lagos, Accra and Cape Town." },
    { label: "Institutions", text: "The African Union and the Agence Française de Développement met in Paris; delegates from Kenya and España also attended." }
  ];

  /* Graph */
  const graphRaw = graphState.raw;
  /* Bridge snake_case as_of_label/more_label → camelCase for sections.jsx */
  const groupData = graphRaw ? {
    ...graphRaw.members,
    asOfLabel: graphRaw.members.as_of_label || graphRaw.members.asOfLabel || `as of ${s.asOf}`,
    moreLabel: graphRaw.members.more_label || graphRaw.members.moreLabel || "",
  } : {
    name: s.group, count: "…", asOfLabel: `as of ${s.asOf}`,
    members: [], more: false, moreLabel: ""
  };
  const regionData = graphRaw ? graphRaw.within : {
    name: s.region, count: 0, codes: [], note: ""
  };
  /* subject_in_group is keyed to the selected group */
  const ukIn = graphRaw ? graphRaw.subject_in_group : (s.asOf < 2020);

  const groups = ["European Union", "NATO", "OECD", "BRICS", "G7", "African Union"]
    .map(n => ({ name: n, active: n === s.group, on: () => {
      set({ group: n });
      fetchGraph(n, s.region, s.asOf);
    }}));
  const regions = ["Eastern Africa", "Western Africa", "Western Europe", "South America", "Southern Asia", "Africa", "World Bank Low-Income Countries"]
    .map(n => ({ name: n, active: n === s.region, on: () => {
      set({ region: n });
      fetchGraph(s.group, n, s.asOf);
    }}));

  /* Bulk (Data section) */
  const bulkRaw = bulkState.raw;
  /* Column picker: use server-returned headers + chosen column after first fetch */
  const bulkHeaders = bulkRaw ? (bulkRaw.headers || []) : [];
  const activeCol = bulkRaw ? (bulkRaw.column || "") : s.byodCol;
  const byodCols = bulkHeaders.map(h => ({
    name: h, active: h === activeCol,
    on: () => {
      set({ byodCol: h });
      fetchBulk(s.byodText, h);
    }
  }));

  const byodResults = bulkRaw ? (bulkRaw.results || []).map(r => ({
    ...r,
    idColor: r.id_color,
  })) : [];
  const byodMore = bulkRaw ? !!bulkRaw.more : false;
  const byodMoreLabel = bulkRaw ? (bulkRaw.more_label || bulkRaw.moreLabel || "") : "";
  const bulkSummary = bulkRaw
    ? (() => {
        const sm = bulkRaw.summary || {};
        return `${sm.rows || 0} rows · ${sm.unique || 0} unique · ${sm.resolved || 0} resolved`;
      })()
    : "…";

  /* Compare — one row per tool, showing the resolved RESULT (in the selected
     target) AND the measured latency. resolvekit + coco + hdx come from the
     server /compare; Data Commons Resolve is the browser-side networked call. */
  const cmpRaw = compareState.raw;

  const COMPARE_TOOLS = [
    { key: "resolvekit",           label: "resolvekit",           badge: "OFFLINE", hi: true },
    { key: "country_converter",    label: "country_converter",    badge: "OFFLINE" },
    { key: "hdx_python_country",   label: "hdx_python_country",   badge: "OFFLINE" },
    { key: "data_commons_resolve", label: "data_commons_resolve", badge: "NETWORK" },
  ];

  function toolDatum(key) {
    if (key === "data_commons_resolve") {
      if (dcResState.status === "loading") return { loading: true };
      if (dcResState.status === "error")   return { supported: true, value: null, note: "request failed", latency: null };
      /* dcResState.raw is the {supported, value, note} from the two-step call. */
      const d = dcResState.raw || {};
      const supported = d.supported !== false;
      return {
        supported,
        value: d.value != null ? d.value : null,
        note: d.note || null,
        /* No latency charted for an unsupported target (no real call made). */
        latency: supported && dcResState.raw != null ? dcResState.latency : null,
      };
    }
    if (compareState.status === "loading" && !cmpRaw) return { loading: true };
    const d = (cmpRaw && cmpRaw[key]) || {};
    const supported = d.supported !== false;
    return {
      supported,
      value: d.value != null ? d.value : null,
      /* Top candidates when the tool abstained on an ambiguous query (resolvekit). */
      candidates: d.candidates || [],
      note: d.note || null,
      /* Unsupported offline tools short-circuit (no real resolve) — show no time. */
      latency: supported && d.elapsed_ms != null ? d.elapsed_ms : null,
      confPct: d.conf_pct || null,
    };
  }

  const compareRows = COMPARE_TOOLS.map(t => ({ tool: t.label, badge: t.badge, hi: !!t.hi, ...toolDatum(t.key) }));

  const compareToOptions = ["name", "iso2", "iso3", "dcid", "wikidata"].map(k => ({
    key: k, label: k, active: k === s.compareTo,
    on: () => { set({ compareTo: k }); fetchCompare(s.compareQuery, k); },
  }));

  const compareLatency = (() => {
    const rows = compareRows.filter(r => r.latency != null).map(r => ({ tool: r.tool, ms: r.latency, hi: r.hi }));
    if (!rows.length) return [];
    const lmax = Math.log(Math.max(...rows.map(r => r.ms)) + 1);
    return rows.map(r => ({
      tool: r.tool,
      label: r.ms >= 100 ? `${Math.round(r.ms)} ms` : `${r.ms.toFixed(1)} ms`,
      weight: r.hi ? "600" : "400",
      w: lmax > 0 ? `${Math.max(3, (Math.log(r.ms + 1) / lmax) * 100).toFixed(1)}%` : "3%",
      barColor: r.hi ? "#0B50BE" : "#B8B8B2",
    }));
  })();

  /* Custom BYOD — resolution against a from_records() resolver */
  const byodCustomRaw = byodCustomState.raw;
  const byodResolution = (byodCustomRaw && byodCustomRaw.resolution) ? (() => {
    const r = byodCustomRaw.resolution;
    return {
      status: r.status,
      entityId: r.entity_id,
      canonicalName: r.canonical_name,
      confPct: r.conf_pct,
      barW: r.bar_w,
      matchTier: r.match_tier,
    };
  })() : null;
  const byodRecordCount = byodCustomRaw ? (byodCustomRaw.record_count || 0) : null;

  /* ---- Full vm ---- */
  const vm = {
    /* ---------- resolve ---------- */
    res,
    /* status booleans check the raw status field when ok, else treat as empty */
    resResolved:  resolveState.status === "ok" && resStatus === "resolved",
    resAmbiguous: resolveState.status === "ok" && resStatus === "ambiguous",
    resNoMatch:   resolveState.status === "ok" && resStatus === "no_match",
    resEmpty:     resolveState.status === "idle" || (!resolveState.raw && resolveState.status !== "loading" && resolveState.status !== "error"),
    resLoading:   resolveState.status === "loading",
    resError:     resolveState.status === "error",
    resErrorMsg:  resolveState.error,

    resolveQuery: s.resolveQuery,
    onResolveChange: (e) => doResolve(e.target.value),
    presets,

    showExplain: s.showExplain,
    toggleExplain: () => {
      const next = !s.showExplain;
      set({ showExplain: next, showCodeR: false });
      /* Pass the original query (user input), not canonical_name, since it may
         be null for unlinked cities and should reflect what was actually resolved. */
      if (next && resolveState.raw && resolveState.raw.status === "resolved") {
        const resolvedQuery = s.resolveQuery;
        set({ explainQuery: resolvedQuery });
        fetchExplain(resolvedQuery);
      }
    },
    explainLabel: s.showExplain ? "hide scorecard" : "explain →",
    explainLoading: explainState.status === "loading",
    explainText: explainState.status === "ok" ? (explainState.raw.explain_text || "") : "",
    explainError: explainState.status === "error" ? explainState.error : null,

    showCodeR: s.showCodeR,
    toggleCodeR: () => set({ showCodeR: !s.showCodeR, showExplain: false }),
    codeLabelR: s.showCodeR ? "hide code" : "</> code",

    /* ---------- autocomplete ---------- */
    suggestQuery: s.suggestQuery,
    onSuggestChange: (e) => {
      const v = e.target.value;
      set({ suggestQuery: v });
      fetchSuggestDebounced(v, s.suggestScope);
    },
    suggestScope: s.suggestScope,
    suggestScopeOptions: [
      { key: "all", label: "all" },
      { key: "countries", label: "countries" },
      { key: "cities", label: "cities" },
      { key: "orgs", label: "orgs" },
    ].map(o => ({
      ...o, active: o.key === s.suggestScope,
      on: () => { set({ suggestScope: o.key }); fetchSuggest(s.suggestQuery, o.key); },
    })),
    suggestions,
    suggestEmpty: s.suggestQuery.trim().length > 0 && suggestions.length === 0 && suggestState.status !== "loading",
    suggestHeader,
    suggestLoading: suggestState.status === "loading",
    suggestError:   suggestState.status === "error",
    suggestErrorMsg: suggestState.error,
    showCodeS: s.showCodeS,
    toggleCodeS: () => set({ showCodeS: !s.showCodeS }),
    codeLabelS: s.showCodeS ? "hide code" : "</> code",
    suggestCode: `from resolvekit import Resolver\n\nr = Resolver.auto()\n# scope="${s.suggestScope}" — all types by default; pass entity_type=/domain= to narrow\nfor s in r.suggest("${s.suggestQuery.trim() || "paris"}", top_k=10${s.suggestScope === "countries" ? ', entity_type="geo.country"' : s.suggestScope === "cities" ? ', entity_type="geo.city"' : s.suggestScope === "orgs" ? ', domain="org"' : ''}):\n    print(s.canonical_name, s.entity_id, s.match_class.value)\n\ns = r.suggest("germny")[0]\ns.canonical_name, s.highlight_ranges`,

    /* ---------- parse ---------- */
    parseText: s.parseText,
    onParseChange: (e) => {
      const v = e.target.value;
      set({ parseText: v });
      fetchParseDebounced(v);
    },
    parseSegments,
    parseEntities,
    parseCount,
    parseLoading: parseState.status === "loading",
    /* True once any parse has returned — lets the panels keep the prior result
       on screen (dimmed) during a refetch instead of flashing a skeleton. */
    parseHasResult: parseState.raw != null,
    parseError:   parseState.status === "error",
    parseErrorMsg: parseState.error,
    /* /parse returns 200 {status:"unavailable"} with detail; detect this pattern. */
    parseUnavailable: parseState.error && (
      parseState.error.includes("unavailable") || parseState.error.includes("[parsing]")
    ),
    parsePresets: parsePresetList.map(p => ({
      label: p.label,
      on: () => {
        set({ parseText: p.text });
        fetchParse(p.text);
      }
    })),
    showCodeP: s.showCodeP,
    toggleCodeP: () => set({ showCodeP: !s.showCodeP }),
    codeLabelP: s.showCodeP ? "hide code" : "</> code",
    parseCode: `import resolvekit as rk   # uv add "resolvekit[parsing]"\n\nresult = rk.parse(text)\nfor e in result:\n    if e.entity_id:\n        print(e.surface, (e.start, e.end), e.entity_id, round(e.confidence, 2))\n\nspans = rk.parse_bulk(values=df["notes"]).to_dataframe()`,

    /* ---------- graph ---------- */
    groups, groupData, region: s.region, regions, regionData,
    asOf: s.asOf,
    onAsOf: (e) => {
      const yr = +e.target.value;
      set({ asOf: yr });
      fetchGraph(s.group, s.region, yr);
    },
    ukLabel: ukIn ? "True" : "False",
    /* Dynamic code label keyed to the selected group (not hardcoded to EU) */
    subjectInGroupCode: `r.is_member("United Kingdom", "${s.group}", as_of=date(${s.asOf}, 1, 1))`,
    graphLoading: graphState.status === "loading",
    graphError:   graphState.status === "error",
    graphErrorMsg: graphState.error,
    showCodeG: s.showCodeG,
    toggleCodeG: () => set({ showCodeG: !s.showCodeG }),
    codeLabelG: s.showCodeG ? "hide code" : "</> code",
    graphCode: `from datetime import date\nr = rk.default()\n\nr.members_of("${s.group}", as_codes="iso3")\nr.within("${s.region}", entity_type="geo.country", to="iso3")\nr.is_member("United Kingdom", "${s.group}", as_of=date(${s.asOf}, 1, 1))`,

    /* ---------- data ---------- */
    byodText: s.byodText,
    onByodText: (e) => {
      const v = e.target.value;
      set({ byodText: v, byodCol: "" });
      fetchBulkDebounced(v, "");
    },
    onByodFile, onByodDrop,
    onByodDragOver: (e) => e.preventDefault(),
    byodFileLabel: s.byodFileName ? s.byodFileName : "CSV / TSV, .csv .tsv .txt",
    byodCols,
    byodSummary: bulkSummary,
    byodResults,
    byodMore,
    byodMoreLabel,
    bulkLoading: bulkState.status === "loading",
    bulkError:   bulkState.status === "error",
    bulkErrorMsg: bulkState.error,
    showCodeB: s.showCodeB,
    toggleCodeB: () => set({ showCodeB: !s.showCodeB }),
    codeLabelB: s.showCodeB ? "hide code" : "</> code",
    byodCode: `import pandas as pd, resolvekit as rk\n\ndf = pd.read_csv("places.csv")\n\n# dedupes internally — N rows, only the distinct values resolved\ndf["entity_id"] = rk.bulk(values=df["${activeCol || "country"}"])\ndf["iso3"]      = rk.bulk(values=df["${activeCol || "country"}"], to="iso3")\ndf["conf"]      = rk.bulk(values=df["${activeCol || "country"}"], to="confidence")`,

    /* ---------- compare ---------- */
    compareQuery: s.compareQuery,
    onCompareChange: (e) => set({ compareQuery: e.target.value }),
    onCompareKeyDown: (e) => { if (e.key === "Enter") { e.preventDefault(); fetchCompare(s.compareQuery, s.compareTo); } },
    onCompareRun: () => fetchCompare(s.compareQuery, s.compareTo),
    compareTo: s.compareTo,
    compareToOptions,
    /* Curated examples spanning a country, a group, an org and a city — each
       resolves where the country-only libraries can't. Clicking only swaps the
       query; the chosen target is preserved. */
    compareExamples: ["Côte d'Ivoire", "Germny", "Congo", "African Union", "AFD", "KfW", "Paris", "Nairobi"].map(q => ({
      label: q,
      on: () => { set({ compareQuery: q }); fetchCompare(q, s.compareTo); },
    })),
    compareRows,
    compareLatency,
    compareHasData: cmpRaw != null,
    compareLoading: compareState.status === "loading" || dcResState.status === "loading",
    compareError:   compareState.status === "error",
    compareErrorMsg: compareState.error,

    /* ---------- custom data (BYOD via from_records) ---------- */
    byodCustomText: s.byodCustomText,
    onByodCustomText: (e) => {
      const v = e.target.value;
      set({ byodCustomText: v });
      fetchByodCustomDebounced(v, s.byodCustomQuery);
    },
    byodCustomQuery: s.byodCustomQuery,
    onByodCustomQuery: (e) => {
      const v = e.target.value;
      set({ byodCustomQuery: v });
      fetchByodCustomDebounced(s.byodCustomText, v);
    },
    onByodCustomKeyDown: (e) => { if (e.key === "Enter") { e.preventDefault(); fetchByodCustom(s.byodCustomText, s.byodCustomQuery); } },
    byodCustomExamples: ["Riverbend", "RBF", "Pacific Fund", "SRP", "Andean Water Trust"].map(q => ({
      label: q,
      on: () => { set({ byodCustomQuery: q }); fetchByodCustom(s.byodCustomText, q); },
    })),
    byodResolution,
    byodRecordCount,
    byodCustomLoading: byodCustomState.status === "loading",
    byodCustomError:   byodCustomState.status === "error",
    byodCustomErrorMsg: byodCustomState.error,
    showCodeC: s.showCodeC,
    toggleCodeC: () => set({ showCodeC: !s.showCodeC }),
    codeLabelC: s.showCodeC ? "hide code" : "</> code",
    byodCustomCode: `import resolvekit as rk\n\n# your own records — names resolvekit has never seen\nr = rk.Resolver.from_records(\n    "partners.csv",            # list[dict], DataFrame or CSV/JSON path\n    name="name", id="id", aliases="aliases", codes=["code"],\n)\n\nr.resolve("${(s.byodCustomQuery || "Riverbend").replace(/"/g, '\\"')}")     # → custom/… with calibrated confidence\nr.suggest("riv")              # typeahead works on your data too`,
  };

  return (
    <div style={{ fontFamily: "'Colfax', system-ui, -apple-system, sans-serif", color: "#1A1A1A", minHeight: "100vh", background: "#FFFFFF" }}>
      <UI.Nav />
      <UI.Intro />
      <UI.ResolveSection vm={vm} />
      <UI.Rule />
      <UI.CompareSection vm={vm} />
      <UI.Rule />
      <UI.GraphSection vm={vm} />
      <UI.Rule />
      <UI.DataSection vm={vm} />
      <UI.Rule />
      <UI.CustomDataSection vm={vm} />
      <UI.Rule />
      <UI.AutocompleteSection vm={vm} />
      <UI.Rule />
      <UI.ParseSection vm={vm} />
      <UI.Footer />
    </div>
  );
}

const root = ReactDOM.createRoot(document.getElementById("root"));
root.render(<App />);
