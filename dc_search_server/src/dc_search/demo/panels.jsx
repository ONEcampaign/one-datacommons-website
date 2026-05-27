/* ============================================================
   Search Comparison — panels (v2)
   Per-method result renderers + diff summary.

   All three panels render results via a shared <VariableCard>,
   wrap long lists in <ExpandableList>, and emit the same
   "interpretation" strip up top.

   Exposes: window.ResolvePanel, DcSearchPanel, DetectPanel
            normalize{Resolve,DcSearch,Detect}
   ============================================================ */

const { useState } = React;

/* ---------------------------------------------------------- */
/* NORMALIZERS — flatten each response into [{dcid, ...}, ...] */
/* ---------------------------------------------------------- */

function normalizeResolve(resp) {
  if (!resp || !resp.entities) return [];
  const out = [];
  for (const ent of resp.entities) {
    for (const c of ent.candidates || []) {
      out.push({
        dcid: c.dcid,
        name: (c.metadata && c.metadata.sentence) || c.dcid,
        score: parseFloat((c.metadata && c.metadata.score) || "0"),
        typeOf: (c.typeOf && c.typeOf[0]) || "StatisticalVariable",
        matched_sentence: c.metadata && c.metadata.sentence,
      });
    }
  }
  return out.sort((a, b) => b.score - a.score);
}

/* /api/explore/detect — the insight_ctx returns `variables` as a flat list of
   DCIDs (statvars + topics, ranked, no scores). Names are hydrated client-side
   into `resp._names` (see app.jsx :: callExploreDetect). The other context
   fields (entities, classifications, etc.) are surfaced separately in the panel
   body — here we only flatten the comparable bit so this can sit alongside
   the resolve / dc-search variable lists.                                    */
function normalizeDetect(resp) {
  if (!resp || !Array.isArray(resp.variables)) return [];
  const names = resp._names || {};
  return resp.variables.map(dcid => ({
    dcid,
    name: names[dcid] || dcid,  // fall back to DCID if hydration missed it
    score: null,                // detect doesn't return scores
    typeOf: dcid.startsWith("dc/topic/") ? "Topic" : "StatisticalVariable",
    isTopic: dcid.startsWith("dc/topic/"),
    hydrated: !!names[dcid],
  }));
}

/* dc-search — new shape: answers[].variables[] are ResolvedVariable objects */
function normalizeDcSearch(resp) {
  if (!resp || !Array.isArray(resp.answers)) return [];
  const out = [];
  for (const ans of resp.answers) {
    for (const v of ans.variables || []) {
      out.push({
        dcid: v.dcid,
        name: v.name || ans.topic_name || v.dcid,
        score: v.score != null ? v.score : null,
        typeOf: "StatisticalVariable",
        answer_kind: ans.answer_kind,
        variable_label: ans.variable_label,
      });
    }
  }
  return out;
}

/* Format an interpretation date object into a chip-ready string.
   Shapes from the API:
     {kind:"range",  start:"2020", end:"2024"}  → "2020–2024"
     {kind:"point",  start:"2022", end:"2022"}  → "2022"
     {kind:"latest"}                            → "latest"
   No-date queries don't emit anything here.                              */
function formatDate(d) {
  if (!d) return "—";
  if (d.kind === "latest") return "latest";
  if (d.kind === "point") return String(d.start ?? d.end ?? "—");
  if (d.kind === "range") {
    const s = d.start ?? "?", e = d.end ?? "?";
    return s === e ? String(s) : `${s}–${e}`;
  }
  // Fallback for unexpected shapes
  if (d.start && d.end) return d.start === d.end ? String(d.start) : `${d.start}–${d.end}`;
  if (d.start || d.end) return String(d.start ?? d.end);
  if (d.year) return String(d.year);
  if (d.text) return String(d.text);
  return JSON.stringify(d);
}

/* ---------------------------------------------------------- */
/* Small shared widgets                                       */
/* ---------------------------------------------------------- */

function Latency({ ms, error, pending, streaming }) {
  if (streaming) return <span className="latency streaming">streaming…</span>;
  if (pending) return <span className="latency pending">— ms</span>;
  if (error) return <span className="latency error">ERR</span>;
  return <span className="latency">{Math.round(ms)} ms</span>;
}

function RawJson({ data, label = "Raw JSON" }) {
  const [open, setOpen] = useState(false);
  if (!data) return null;
  return (
    <div>
      <button className="expander" aria-expanded={open} onClick={() => setOpen(o => !o)}>
        <span className="caret">▸</span> {label}
      </button>
      {open && <pre className="raw-json">{JSON.stringify(data, null, 2)}</pre>}
    </div>
  );
}

function Skeleton() {
  return (
    <div className="skeleton">
      <div className="skeleton-line med" />
      <div className="skeleton-line short" />
      <div className="skeleton-line" />
      <div className="skeleton-line short" />
    </div>
  );
}

function PanelHead({ method, endpoint, latency, error, pending, streaming }) {
  const labels = {
    resolve:  "Resolve",
    dcsearch: "dc-search",
    detect:   "explore/detect",
  };
  const subtitles = {
    resolve:  "Indicator-embedding lookup",
    dcsearch: "LLM-assisted predicate paradigm",
    detect:   "Structured intent (places, vars, classifications)",
  };
  // Display the path only — the base URL (configurable via ?base=) would
  // otherwise wrap inconsistently across the three panel headers.
  const path = (endpoint || "").replace(/^https?:\/\/[^/]+/, "");
  return (
    <div className="panel-head">
      <div className="panel-head-l">
        <span className="panel-method-name">
          <span className="swatch" /> {labels[method]}
          {streaming && <span className="streaming-pulse" title="Streaming via SSE" />}
        </span>
        <span className="panel-method-sub">{subtitles[method]}</span>
        <span className="panel-endpoint mono" title={endpoint}>{path}</span>
      </div>
      <div className="panel-head-r">
        <Latency ms={latency} error={error} pending={pending} streaming={streaming} />
      </div>
    </div>
  );
}

function PanelLoading() {
  return (
    <div className="panel-body">
      <Skeleton /><Skeleton /><Skeleton />
    </div>
  );
}

function PanelError({ message }) {
  return (
    <div className="panel-body empty">
      <div style={{ fontWeight: 700, color: "hsl(var(--red))", marginBottom: 6 }}>
        Request failed
      </div>
      <div style={{ maxWidth: "32ch" }}>{message}</div>
      <div style={{ marginTop: 12, fontSize: 11, fontStyle: "italic" }}>
        Endpoint unreachable from this origin. Check the host is up and CORS-enabled,
        or repoint the page with <span className="mono">?base=&lt;url&gt;</span>.
      </div>
    </div>
  );
}

/* ---------------------------------------------------------- */
/* SHARED — Section header (e.g. "Top results · 12 hits")     */
/* ---------------------------------------------------------- */

function SectionHead({ children, right }) {
  return (
    <div className="section-head">
      <span className="section-head-l">{children}</span>
      {right && <span className="section-head-r">{right}</span>}
    </div>
  );
}

/* ---------------------------------------------------------- */
/* SHARED — Expandable list ("show top N + show more")        */
/* ---------------------------------------------------------- */

function ExpandableList({ items, renderItem, defaultCount = 4, expandLabel = "result", expandLabelPlural }) {
  const [expanded, setExpanded] = useState(false);
  if (!items || items.length === 0) return null;
  const visible = expanded ? items : items.slice(0, defaultCount);
  const remaining = items.length - defaultCount;
  const plural = expandLabelPlural || `${expandLabel}s`;
  return (
    <div className="expandable">
      {visible.map((it, i) => renderItem(it, i))}
      {remaining > 0 && (
        <button
          className="expand-toggle"
          onClick={() => setExpanded(e => !e)}
          aria-expanded={expanded}
        >
          {expanded
            ? <>↑ Show top {defaultCount} only</>
            : <>↓ Show {remaining} more {remaining !== 1 ? plural : expandLabel}</>}
        </button>
      )}
    </div>
  );
}

/* ---------------------------------------------------------- */
/* SHARED — VariableCard (used by all 3 panels)               */
/* ---------------------------------------------------------- */

function VariableCard({
  dcid, name, description = null, score = null,
  matchedSentence = null,
  unit = null,
  availability = null, // null | true | false
  dateRange = null,
  source = null, // e.g. "medium_ft" or "embedding"
  idx = 0,
  variant = "default", // "default" | "compact" | "dcid-only"
}) {
  const isDcidOnly = variant === "dcid-only";

  return (
    <div className={`vcard vcard-${variant}`} style={{ "--idx": idx }}>
      <div className="vcard-head">
        <span className="vcard-dcid" title={dcid}>{dcid}</span>
      </div>

      {!isDcidOnly && name && name !== dcid && (
        <div className="vcard-name">{name}</div>
      )}

      {!isDcidOnly && description && (
        <div className="vcard-desc">{description}</div>
      )}

      {!isDcidOnly && (score != null || availability != null || dateRange || unit || source) && (
        <div className="vcard-foot">
          {score != null && (
            <span className="vcard-chip vcard-score">
              <span className="vcard-chip-k">score</span> {score.toFixed(3)}
            </span>
          )}
          {availability === true && (
            <span className="vcard-chip vcard-avail-yes" title="Data exists at the resolved place">
              <span className="vcard-dot vcard-dot-yes" /> data
            </span>
          )}
          {availability === false && (
            <span className="vcard-chip vcard-avail-no" title="Place resolved but no data found">
              <span className="vcard-dot vcard-dot-no" /> no data
            </span>
          )}
          {dateRange && (dateRange.earliest || dateRange.latest) && (
            <span className="vcard-chip">
              {(dateRange.earliest || "?")}–{(dateRange.latest || "?")}
            </span>
          )}
          {unit && <span className="vcard-chip">{unit}</span>}
          {source && <span className="vcard-chip vcard-source">{source}</span>}
        </div>
      )}

      {!isDcidOnly && matchedSentence && (
        <div className="vcard-match" title="Retrieval sentence this DCID matched">
          “{matchedSentence}”
        </div>
      )}
    </div>
  );
}

/* ---------------------------------------------------------- */
/* SHARED — Interpretation strip (places, extracted phrases)  */
/* ---------------------------------------------------------- */

function InterpretationStrip({ kind, items }) {
  // items: [{label, value, kind: "place"|"variable"|"date"|"index"|"node", extra?}]
  if (!items || items.length === 0) return null;
  return (
    <div className="interp-strip">
      <div className="interp-strip-label">{kind}</div>
      <div className="interp-strip-chips">
        {items.map((it, i) => (
          <span key={i} className={`interp-chip interp-chip-${it.kind || "default"}`}>
            {it.label && <span className="interp-chip-k">{it.label}</span>}
            <span className="interp-chip-v">{it.value}</span>
            {it.extra && <span className="interp-chip-extra">{it.extra}</span>}
            {it.note && <span className="interp-chip-note">{it.note}</span>}
          </span>
        ))}
      </div>
    </div>
  );
}

/* ---------------------------------------------------------- */
/* DC-SEARCH — contained-in expansion block                  */
/* ---------------------------------------------------------- */

const CHILD_PLACE_CAP = 300;

function ContainedInExpansion({ interp, streaming }) {
  if (!interp || !interp.contained_in) return null;

  const places = interp.places || [];
  const expanded = places.filter(p => p.expanded && (p.children || []).length > 0);

  // pending — intent fired, still streaming, no expanded places yet
  if (expanded.length === 0 && streaming) {
    return (
      <div className="contained-in-pending">
        <span className="streaming-pulse" /> expanding to children…
      </div>
    );
  }

  // intent fired, no children (stream done or not streaming)
  if (expanded.length === 0) {
    return (
      <div className="contained-in-empty">
        contained-in detected — no children expanded
      </div>
    );
  }

  // expanded — one parent block per expanded place
  return (
    <div className="contained-in-block">
      <SectionHead right={`${expanded.length} parent${expanded.length !== 1 ? "s" : ""}`}>
        Contained-in expansion
      </SectionHead>
      {expanded.map((p, i) => {
        const n = p.children.length;
        const capped = n >= CHILD_PLACE_CAP;
        return (
          <div className="contained-in-parent" key={p.dcid || p.input_name || i}>
            <div className="contained-in-head">
              <span>{p.name || p.input_name}</span>
              <span> → {p.child_type} · </span>
              <span>{n} {capped ? "children (server cap)" : `child${n !== 1 ? "ren" : ""}`}</span>
            </div>
            <ExpandableList
              items={p.children}
              defaultCount={8}
              expandLabel="child"
              expandLabelPlural="children"
              renderItem={(c, j) => (
                <span className="contained-in-child-chip" key={(c.dcid || "") + j}>
                  <span className="interp-chip-v">{c.name || c.dcid}</span>
                  {c.dcid && <span className="interp-chip-extra">{c.dcid}</span>}
                </span>
              )}
            />
          </div>
        );
      })}
    </div>
  );
}

/* ============================================================
   RESOLVE PANEL
   ============================================================ */

function ResolvePanel({ state, endpoint, query }) {
  const { status, raw, latency, error } = state;
  return (
    <div className="panel method-resolve">
      <PanelHead method="resolve" endpoint={endpoint} latency={latency}
                 error={!!error} pending={status !== "ok"} />
      {status === "loading" && <PanelLoading />}
      {status === "error"   && <PanelError message={error} />}
      {status === "ok" && <ResolveBody resp={raw} query={query} />}
      <div className="panel-footer">
        <Telemetry method="resolve" state={state} />
        <RawJson data={status === "ok" ? raw : null} />
      </div>
    </div>
  );
}

function ResolveBody({ resp, query }) {
  const items = normalizeResolve(resp);
  if (!items.length) return <div className="panel-body empty">No candidates.</div>;

  return (
    <div className="panel-body">
      <InterpretationStrip
        kind="Query sent"
        items={[{ value: query, kind: "node" }]}
      />

      <SectionHead right={`${items.length} candidate${items.length !== 1 ? "s" : ""}`}>
        Indicator-embedding hits
      </SectionHead>

      <ExpandableList
        items={items}
        defaultCount={4}
        expandLabel="candidate"
        renderItem={(r, i) => (
          <VariableCard
            key={r.dcid + i}
            idx={i}
            dcid={r.dcid}
            name={r.name}
            score={r.score}
            matchedSentence={r.matched_sentence}
            source="embedding"
          />
        )}
      />
    </div>
  );
}

/* ============================================================
   DC-SEARCH PANEL (new shape)
   ============================================================ */

function DcSearchPanel({ state, endpoint, query }) {
  const { status, raw, latency, error, streaming } = state;
  const hasContent = !!raw && (status === "ok" || streaming);
  return (
    <div className="panel method-dcsearch">
      <PanelHead method="dcsearch" endpoint={endpoint} latency={latency}
                 error={!!error} pending={status !== "ok"} streaming={!!streaming} />
      {status === "error"   && <PanelError message={error} />}
      {status === "loading" && !raw && <PanelLoading />}
      {hasContent && <DcSearchBody resp={raw} streaming={!!streaming} state={state} />}
      <div className="panel-footer">
        <Telemetry method="dcsearch" state={state} />
        <RawJson data={hasContent ? raw : null} />
      </div>
    </div>
  );
}

function DcSearchBody({ resp, streaming, state }) {
  if (!resp) return <div className="panel-body empty">No response.</div>;
  const answers = resp.answers || [];
  const ask = resp.ask;
  const interp = resp.interpretation || {};
  const truncated = resp.truncated || (resp.telemetry && resp.telemetry.truncated);
  const timedOut = resp.timed_out;
  const expectedResults = (state && state.expectedResults) || answers.length;

  // Build interpretation chips
  const chips = [];
  for (const v of interp.variables || []) chips.push({ label: "var", value: v, kind: "variable" });
  // Intent pill before places so it reads "intent, then the places it applies to"
  if (interp.contained_in) chips.push({ label: "intent", value: "contained-in", kind: "intent" });
  for (const p of interp.places || []) {
    const children = p.children || [];
    const note = (p.expanded && p.child_type && children.length)
      ? `→ ${children.length} × ${p.child_type}`
      : undefined;
    chips.push({
      label: "place",
      value: p.name || p.input_name,
      extra: p.dcid,
      kind: "place",
      note,
    });
  }
  for (const d of interp.dates || [])
    chips.push({ label: "date", value: formatDate(d), kind: "date" });

  // Counts: filled vs total expected (for "x of N done" indicator while streaming)
  const filled = answers.filter(a => a != null).length;
  const totalSlots = Math.max(expectedResults || 0, answers.length);
  const totalVars = answers.reduce((s, a) => s + ((a && a.variables) || []).length, 0);

  const showStreamingHint = streaming
    && (interp.variables || []).length === 0
    && answers.length === 0
    && !interp.contained_in;

  return (
    <div className="panel-body">
      {chips.length > 0 && <InterpretationStrip kind="Understood as" items={chips} />}

      <ContainedInExpansion interp={interp} streaming={streaming} />

      {showStreamingHint && (
        <div className="interp-strip" style={{ borderLeftColor: "hsl(var(--green))" }}>
          <div className="interp-strip-label">Streaming</div>
          <div style={{ fontSize: 12, color: "hsl(var(--grey-italian))" }}>
            Waiting for interpretation event…
          </div>
        </div>
      )}

      {(truncated || timedOut) && (
        <div className="banner-warn">
          {truncated && <span>⚠ <strong>truncated</strong> — extraction returned more variables than cap. </span>}
          {timedOut && <span>⚠ <strong>timed out</strong> — some branches did not finish within budget. </span>}
        </div>
      )}

      {ask && <AskBanner ask={ask} />}

      {totalSlots > 0 && (
        <SectionHead
          right={
            streaming
              ? `${filled}/${totalSlots} branch${totalSlots !== 1 ? "es" : ""}${totalVars ? ` · ${totalVars} variable${totalVars !== 1 ? "s" : ""}` : ""}`
              : `${answers.length} branch${answers.length !== 1 ? "es" : ""} · ${totalVars} variable${totalVars !== 1 ? "s" : ""}`
          }
        >
          Resolved branches
        </SectionHead>
      )}

      {answers.map((a, i) => (
        a == null
          ? <AnswerSkeleton key={`sk-${i}`} idx={i} timedOut={!streaming && timedOut} />
          : <AnswerCollectionCard key={i} answer={a} idx={i} />
      ))}
    </div>
  );
}

function AnswerSkeleton({ idx, timedOut }) {
  return (
    <div className={`answer-collection answer-skeleton ${timedOut ? "is-timedout" : ""}`} style={{ "--idx": idx }}>
      <div className="answer-collection-head">
        <div className="answer-label-row">
          <span className="answer-tag answer-tag-pending">{timedOut ? "timed out" : "loading"}</span>
          <span className="answer-label-val sk-shimmer-text" />
        </div>
      </div>
      <div className="answer-svset-label">
        <span className="sk-shimmer-text" style={{ width: 100 }} />
      </div>
      <div className="vcard vcard-skeleton">
        <div className="skeleton-line med" />
        <div className="skeleton-line short" />
        <div className="skeleton-line" />
      </div>
      <div className="vcard vcard-skeleton">
        <div className="skeleton-line med" />
        <div className="skeleton-line short" />
      </div>
    </div>
  );
}

function AskBanner({ ask }) {
  return (
    <div className="ask-banner">
      <div className="ask-banner-head">
        <span className="ask-banner-tag">clarification</span>
        <span className="mono ask-banner-reason">{ask.reason}</span>
      </div>
      <div className="ask-banner-msg">{ask.message}</div>
      {ask.proposed_clarifications && ask.proposed_clarifications.length > 0 && (
        <ul className="ask-banner-clar">
          {ask.proposed_clarifications.map((c, i) => <li key={i}>{c}</li>)}
        </ul>
      )}
    </div>
  );
}

function AnswerCollectionCard({ answer, idx }) {
  const isTopic = answer.answer_kind === "topic";
  const isClarification = answer.outcome_kind === "clarification";
  const variables = answer.variables || [];

  return (
    <div className={`answer-collection ${isTopic ? "is-topic" : "is-variables"} ${isClarification ? "is-clarification" : ""}`}
         style={{ "--idx": idx }}>
      <div className="answer-collection-head">
        <div className="answer-label-wrap">
          <div className="answer-label-row">
            {isClarification ? (
              <span className="answer-tag answer-tag-clar">clarification</span>
            ) : (
              <span className={`answer-tag answer-tag-${isTopic ? "topic" : "vars"}`}>
                {isTopic ? "topic" : "variables"}
              </span>
            )}
            <span className="answer-label-val">{answer.variable_label || "—"}</span>
          </div>
          {isTopic && answer.topic_name && (
            <div className="topic-title" title={answer.topic_name}>{answer.topic_name}</div>
          )}
          {isTopic && answer.topic_description && (
            <div className="topic-desc">{answer.topic_description}</div>
          )}
        </div>
      </div>

      {isClarification && answer.clarification && (
        <InlineClarification clar={answer.clarification} />
      )}

      {!isClarification && variables.length > 0 && (
        <>
          <div className="answer-svset-label">
            {isTopic ? "Member variables" : "Resolved variables"}
            <span className="answer-svset-count">· {variables.length}</span>
          </div>

          <ExpandableList
            items={variables}
            defaultCount={isTopic ? 4 : 3}
            expandLabel="variable"
            renderItem={(v, i) => (
              <VariableCard
                key={v.dcid + i}
                idx={i}
                dcid={v.dcid}
                name={isTopic ? null : v.name}
                description={isTopic ? null : v.description}
                score={isTopic ? null : v.score}
                unit={isTopic ? null : v.unit}
                availability={isTopic ? null : v.available_at_place}
                dateRange={isTopic ? null : v.date_range}
                matchedSentence={isTopic ? null : v.matched_sentence}
                variant={isTopic ? "dcid-only" : "default"}
              />
            )}
          />
        </>
      )}
    </div>
  );
}

function InlineClarification({ clar }) {
  return (
    <div className="inline-clar">
      <div className="inline-clar-msg">{clar.message}</div>
      {clar.proposed_clarifications && clar.proposed_clarifications.length > 0 && (
        <ul className="inline-clar-list">
          {clar.proposed_clarifications.map((c, i) => <li key={i}>{c}</li>)}
        </ul>
      )}
      {clar.reason && (
        <div className="inline-clar-reason">reason: <span className="mono">{clar.reason}</span></div>
      )}
    </div>
  );
}

/* ============================================================
   DETECT PANEL  —  /api/explore/detect
   ============================================================ */

function DetectPanel({ state, endpoint, query }) {
  const { status, raw, latency, error } = state;
  return (
    <div className="panel method-detect">
      <PanelHead method="detect" endpoint={endpoint} latency={latency}
                 error={!!error} pending={status !== "ok"} />
      {status === "loading" && <PanelLoading />}
      {status === "error"   && <PanelError message={error} />}
      {status === "ok" && <DetectBody resp={raw} query={query} />}
      <div className="panel-footer">
        <Telemetry method="detect" state={state} />
        <RawJson data={status === "ok" ? raw : null} />
      </div>
    </div>
  );
}

/* Classifications come back from the server's hand-rolled serialiser
   (server/lib/nl/common/serialize.py :: classification_to_dict) as a FLAT
   dict, NOT NLClassifier asdict(). The shape varies by ClassificationType:
     CONTAINED_IN   → { type:4,  contained_in_place_type:"State", had_default_type:false }
     EVENT          → { type:9,  event_type:[<EventType int>, …] }
     RANKING        → { type:2,  ranking_type:[<RankingType int>, …] }
     TIME_DELTA     → { type:8,  time_delta_type:[<TimeDeltaType int>, …] }
     SUPERLATIVE    → { type:11, "":[<SuperlativeType int>, …] }   ← upstream bug, empty key
     COMPARISON     → { type:7,  comparison:true }
     CORRELATION    → { type:5,  correlation:true }
     QUANTITY       → { type:3,  quantity:{ idx, qval?:{cmp,val}, qrange?:{lower,upper} } }
     DATE           → { type:12, dates:[{prep,year,month,year_span}, …], is_single_date:bool }
     SIMPLE/OVERVIEW/PER_CAPITA/ANSWER_PLACES_REFERENCE → just { type } */
const CLASSIFICATION_TYPE_NAMES = {
  0: "OTHER", 1: "SIMPLE", 2: "RANKING", 3: "QUANTITY", 4: "CONTAINED_IN",
  5: "CORRELATION", 7: "COMPARISON", 8: "TIME_DELTA", 9: "EVENT",
  10: "OVERVIEW", 11: "SUPERLATIVE", 12: "DATE",
  13: "ANSWER_PLACES_REFERENCE", 14: "PER_CAPITA", 15: "DETAILED_ACTION",
  16: "TEMPORAL", 100: "UNKNOWN",
};
const RANKING_TYPE_NAMES   = { 0: "NONE", 1: "HIGH", 2: "LOW", 3: "BEST", 4: "WORST", 5: "EXTREME" };
const TIME_DELTA_NAMES     = { 0: "INCREASE", 1: "DECREASE", 2: "CHANGE" };
const SUPERLATIVE_NAMES    = { 0: "NONE", 1: "BIG", 2: "SMALL", 3: "RICH", 4: "POOR", 5: "LIST" };
const EVENT_TYPE_NAMES     = { 0: "COLD", 1: "CYCLONE", 2: "EARTHQUAKE", 3: "DROUGHT", 4: "FIRE", 5: "FLOOD", 6: "HEAT", 7: "WETBULB" };

function mapEnumList(arr, table) {
  if (!Array.isArray(arr)) return [];
  return arr.map(v => (typeof v === "number" ? (table[v] || `#${v}`) : String(v)));
}

function formatDateAttr(d) {
  if (!d) return "";
  const y  = d.year;
  const m  = d.month;
  const ys = d.year_span ?? d.yearSpan;
  let s = y ? String(y) : "?";
  if (m) s = `${y}-${String(m).padStart(2, "0")}`;
  if (ys && ys > 0) s = `${s} ±${ys}y`;
  return s;
}

function pick(obj, ...keys) {
  for (const k of keys) {
    if (obj && obj[k] !== undefined && obj[k] !== null) return obj[k];
  }
  return undefined;
}

function formatClassification(c) {
  if (!c) return null;
  const typeName = typeof c.type === "number"
    ? (CLASSIFICATION_TYPE_NAMES[c.type] || `TYPE_${c.type}`)
    : String(c.type || "UNKNOWN");

  let extra = "";
  switch (typeName) {
    case "DATE": {
      const dates = Array.isArray(c.dates) ? c.dates : [];
      const single = c.is_single_date ?? c.isSingleDate;
      const formatted = dates.map(formatDateAttr).filter(Boolean);
      extra = formatted.join(", ");
      if (single && extra) extra = `${extra} (single)`;
      break;
    }
    case "RANKING":
      extra = mapEnumList(pick(c, "ranking_type", "rankingType"), RANKING_TYPE_NAMES).join(",");
      break;
    case "TIME_DELTA":
      extra = mapEnumList(pick(c, "time_delta_type", "timeDeltaType"), TIME_DELTA_NAMES).join(",");
      break;
    case "SUPERLATIVE":
      // Upstream bug: serialiser writes the superlatives list to an empty-string key.
      extra = mapEnumList(c[""] ?? c.superlatives, SUPERLATIVE_NAMES).join(",");
      break;
    case "CONTAINED_IN": {
      const t = pick(c, "contained_in_place_type", "containedInPlaceType");
      const def = c.had_default_type ?? c.hadDefaultType;
      extra = t ? (def ? `${t} (default)` : t) : "";
      break;
    }
    case "EVENT":
      extra = mapEnumList(pick(c, "event_type", "eventType"), EVENT_TYPE_NAMES).join(",");
      break;
    case "QUANTITY": {
      const q = c.quantity;
      if (q && q.qval) {
        extra = `${q.qval.cmp || ""} ${q.qval.val ?? ""}`.trim();
      } else if (q && q.qrange) {
        const l = q.qrange.lower || {}, u = q.qrange.upper || {};
        extra = `[${l.cmp || "?"} ${l.val ?? "?"}, ${u.cmp || "?"} ${u.val ?? "?"}]`;
      }
      break;
    }
    case "COMPARISON":  extra = c.comparison  ? "" : ""; break;
    case "CORRELATION": extra = c.correlation ? "" : ""; break;
    // SIMPLE / OVERVIEW / PER_CAPITA / ANSWER_PLACES_REFERENCE / TEMPORAL
    // carry no extra data in the serialised dict.
    default: extra = "";
  }

  return { type: typeName, extra };
}

function DetectBody({ resp, query }) {
  if (!resp) return <div className="panel-body empty">No response.</div>;

  // The endpoint returns 200 even on "could not answer" — failure carries
  // a `failure` field. Surface that clearly.
  if (resp.failure) {
    return (
      <div className="panel-body">
        <InterpretationStrip kind="Query sent" items={[{ value: query, kind: "node" }]} />
        <div className="banner-warn">
          <strong>{resp.failure}</strong>
        </div>
      </div>
    );
  }

  const variables       = resp.variables          || [];
  const entities        = resp.entities           || [];
  const nonPlaceEnts    = resp.nonPlaceEntities   || [];
  const childType       = resp.childEntityType    || "";
  const classifications = resp.classifications    || [];
  const cmpEntities     = resp.comparisonEntities || [];
  const cmpVars         = resp.comparisonVariables|| [];
  const userMessages    = resp.userMessages       || [];
  const names           = resp._names             || {};
  const nameOf          = dcid => names[dcid] || dcid;

  // Build the "Understood as" interpretation strip.
  const chips = [];
  for (const dcid of entities) {
    chips.push({ label: "place", value: nameOf(dcid), extra: dcid !== nameOf(dcid) ? dcid : undefined, kind: "place" });
  }
  for (const dcid of nonPlaceEnts) {
    chips.push({ label: "entity", value: nameOf(dcid), extra: dcid !== nameOf(dcid) ? dcid : undefined, kind: "node" });
  }
  if (childType) {
    chips.push({ label: "child type", value: childType, kind: "date" });
  }
  for (const c of classifications) {
    const f = formatClassification(c);
    if (f) chips.push({ label: "intent", value: f.type, extra: f.extra, kind: "variable" });
  }

  const items = normalizeDetect(resp);
  const topicCount   = items.filter(i => i.isTopic).length;
  const statvarCount = items.length - topicCount;

  return (
    <div className="panel-body">
      {chips.length > 0 && <InterpretationStrip kind="Understood as" items={chips} />}

      {userMessages.length > 0 && userMessages.some(m => m && m.trim()) && (
        <div className="banner-info">
          {userMessages.filter(Boolean).map((m, i) => <div key={i}>{m}</div>)}
        </div>
      )}

      {(cmpEntities.length > 0 || cmpVars.length > 0) && (
        <div className="detect-cmp">
          <span className="detect-cmp-tag">comparison</span>
          {cmpEntities.length > 0 && (
            <span className="detect-cmp-row">
              <span className="detect-cmp-k">places</span>
              {cmpEntities.map((e, i) => (
                <code key={i} title={e}>{nameOf(e)}</code>
              ))}
            </span>
          )}
          {cmpVars.length > 0 && (
            <span className="detect-cmp-row">
              <span className="detect-cmp-k">vars</span>
              {cmpVars.map((v, i) => (
                <code key={i} title={v}>{nameOf(v)}</code>
              ))}
            </span>
          )}
        </div>
      )}

      <SectionHead
        right={
          items.length
            ? (topicCount || statvarCount
                ? [
                    topicCount   ? `${topicCount} topic${topicCount !== 1 ? "s" : ""}` : null,
                    statvarCount ? `${statvarCount} statvar${statvarCount !== 1 ? "s" : ""}` : null,
                  ].filter(Boolean).join(", ")
                : `${items.length}`)
            : "—"
        }
      >
        Detected variables
      </SectionHead>

      {items.length === 0 ? (
        <div className="panel-body empty" style={{ padding: 0, fontSize: 12 }}>
          No variables detected. The pipeline ran but found no statvars or topics matching the query.
        </div>
      ) : (
        <ExpandableList
          items={items}
          defaultCount={6}
          expandLabel="variable"
          renderItem={(v, i) => (
            <div className={`detect-vrow ${v.isTopic ? "is-topic" : ""}`} key={v.dcid + i}>
              {v.isTopic && <span className="detect-topic-tag">topic</span>}
              <VariableCard
                idx={i}
                dcid={v.dcid}
                name={v.name}
              />
            </div>
          )}
        />
      )}
    </div>
  );
}

/* ============================================================
   TELEMETRY BLOCK
   ============================================================ */

function Telemetry({ method, state }) {
  const [open, setOpen] = useState(false);
  const { raw, latency } = state;
  if (state.status !== "ok") return null;

  if (method === "resolve") {
    const ent = raw && raw.entities && raw.entities[0];
    const n = ent ? (ent.candidates || []).length : 0;
    return (
      <ExpanderShell label="Telemetry" open={open} setOpen={setOpen}>
        <TelemetryRow k="latency" v={latency != null ? `${Math.round(latency)} ms` : "—"} />
        <TelemetryRow k="candidates" v={n} />
        <TelemetryRow k="resolver" v="indicator (embedding lookup)" />
      </ExpanderShell>
    );
  }

  if (method === "detect") {
    if (!raw) return null;
    const variables       = raw.variables          || [];
    const entities        = raw.entities           || [];
    const cmpEntities     = raw.comparisonEntities || [];
    const cmpVars         = raw.comparisonVariables|| [];
    const classifications = raw.classifications    || [];
    const childType       = raw.childEntityType    || "—";
    const dc              = raw.dc                 || raw.debug?.dc || "main";
    const topicCount      = variables.filter(v => v && v.startsWith("dc/topic/")).length;
    return (
      <ExpanderShell label="Telemetry" open={open} setOpen={setOpen}>
        <TelemetryRow k="latency" v={latency != null ? `${Math.round(latency)} ms` : "—"} />
        <TelemetryRow k="topics"   v={topicCount} />
        <TelemetryRow k="statvars" v={variables.length - topicCount} />
        <TelemetryRow k="entities" v={entities.length} />
        <TelemetryRow k="comparison" v={`${cmpEntities.length} places · ${cmpVars.length} vars`} />
        <TelemetryRow k="classifications" v={classifications.length} />
        <TelemetryRow k="childEntityType" v={childType} mono />
        <TelemetryRow k="dc" v={dc} mono />
      </ExpanderShell>
    );
  }

  // dc-search
  const t = raw && raw.telemetry;
  if (!t) return null;
  const totalIn  = (t.llm_usage || []).reduce((s, u) => s + (u.input_tokens || 0), 0);
  const totalOut = (t.llm_usage || []).reduce((s, u) => s + (u.output_tokens || 0), 0);
  const terminatedBy = raw.terminated_by || t.terminated_by;
  const itp = raw.interpretation;
  const itpExp = itp ? (itp.places || []).filter(p => p.expanded && (p.children || []).length) : [];
  const itpChildTotal = itpExp.reduce((s, p) => s + p.children.length, 0);
  const itpChildTypes = [...new Set(itpExp.map(p => p.child_type).filter(Boolean))].join(", ") || "—";
  return (
    <ExpanderShell label="Telemetry" open={open} setOpen={setOpen}>
      <TelemetryRow k="elapsed_s" v={raw.elapsed_s != null ? raw.elapsed_s.toFixed(2) : "—"} />
      <TelemetryRow k="terminated_by" v={terminatedBy} />
      <TelemetryRow k="n_candidates" v={t.n_candidates} />
      <TelemetryRow k="n_shapes" v={t.n_shapes} />
      {itp && <TelemetryRow k="contained_in" v={String(!!itp.contained_in)} />}
      {itp && itp.contained_in && (
        <TelemetryRow k="children" v={`${itpChildTotal} · ${itpChildTypes}`} mono />
      )}
      {raw.truncated && (
        <TelemetryRow k="truncated" v="true" valStyle={{ color: "hsl(var(--red))" }} />
      )}
      {raw.timed_out && (
        <TelemetryRow k="timed_out" v="true" valStyle={{ color: "hsl(var(--red))" }} />
      )}

      <div className="telemetry-llm">
        <TelemetryRow k="llm tokens" v={`${totalIn} in / ${totalOut} out`} />
        {(t.llm_usage || []).map((u, i) => (
          <div className="telemetry-llm-row" key={i}>
            <span className="step">{u.step}</span>
            <span>{u.input_tokens}→{u.output_tokens} · {u.latency_s ? `${(u.latency_s * 1000).toFixed(0)} ms` : "—"}</span>
          </div>
        ))}
      </div>
    </ExpanderShell>
  );
}

function ExpanderShell({ label, open, setOpen, children }) {
  return (
    <div>
      <button className="expander" aria-expanded={open} onClick={() => setOpen(o => !o)}>
        <span className="caret">▸</span> {label}
      </button>
      {open && <div className="telemetry">{children}</div>}
    </div>
  );
}

function TelemetryRow({ k, v, mono, valStyle }) {
  return (
    <div className="telemetry-row">
      <span className="telemetry-key">{k}</span>
      <span className={`telemetry-val ${mono ? "mono" : ""}`} style={valStyle}>{v}</span>
    </div>
  );
}

/* ---------------------------------------------------------- */
/* EXPORTS                                                    */
/* ---------------------------------------------------------- */

Object.assign(window, {
  ResolvePanel, DcSearchPanel, DetectPanel,
  normalizeResolve, normalizeDcSearch, normalizeDetect,
});
