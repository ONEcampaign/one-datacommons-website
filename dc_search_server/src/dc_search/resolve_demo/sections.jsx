/* ============================================================
   resolvekit demo — section components
   ------------------------------------------------------------
   A faithful React 18 port of the design prototype. Each section
   is a pure renderer of the `vm` view-model that app.jsx builds
   from component state (mirroring the prototype's renderVals()).
   Styling is kept inline to match the prototype 1:1; the ONE Data
   brand tokens (Colfax, blue-dc #0B50BE, teal, mint) are baked in.

   Data now comes from the real resolvekit backend. Sections render
   a loading skeleton while fetching and an error state on failure.
   ============================================================ */

const MONO = "'Courier New', ui-monospace, monospace";
const SANS = "'Colfax', system-ui, -apple-system, sans-serif";
const WRAP = { maxWidth: 1040, margin: "0 auto", padding: "36px 28px" };

/* ---------- shared bits ---------- */
function SectionHead({ n, children, mb = 16 }) {
  return (
    <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: mb }}>
      <span style={{ fontFamily: MONO, fontSize: 13, color: "#0B50BE", fontWeight: 600 }}>{n}</span>
      <h2 style={{ fontSize: 19, fontWeight: 700, letterSpacing: "-0.02em", margin: 0 }}>{children}</h2>
    </div>
  );
}

function Rule() {
  return (
    <div style={{ maxWidth: 1040, margin: "0 auto", padding: "0 28px" }}>
      <div style={{ borderTop: "1px solid #E2E2DC" }} />
    </div>
  );
}

function GhostButton({ onClick, children, style }) {
  return (
    <button onClick={onClick} style={{ fontFamily: MONO, fontSize: 11.5, background: "#fff", border: "1px solid #C9C9C4", borderRadius: 6, padding: "6px 10px", cursor: "pointer", ...style }}>
      {children}
    </button>
  );
}

function CodeBlock({ children, maxWidth }) {
  return (
    <pre className="rk-scroll" style={{ margin: "12px 0 0", background: "#1A1A1A", color: "#ECECE6", fontFamily: MONO, fontSize: 12, lineHeight: 1.55, padding: 13, borderRadius: 6, overflowX: "auto", whiteSpace: "pre", maxWidth }}>{children}</pre>
  );
}

function ExplainBlock({ children }) {
  return (
    <pre className="rk-scroll" style={{ margin: "12px 0 0", background: "#F4F4F1", border: "1px solid #D6D6D1", fontFamily: MONO, fontSize: 11, lineHeight: 1.5, padding: 13, borderRadius: 6, overflowX: "auto", whiteSpace: "pre" }}>{children}</pre>
  );
}

/* A pill toggle button used for groups / regions / columns / competitors. */
function Pill({ active, onClick, children, style }) {
  return (
    <button onClick={onClick} style={{ fontFamily: MONO, fontSize: 12, whiteSpace: "nowrap", border: "1px solid #1A1A1A", borderRadius: 6, padding: "5px 10px", cursor: "pointer", background: active ? "#0B50BE" : "#fff", color: active ? "#fff" : "#1A1A1A", ...style }}>
      {children}
    </button>
  );
}

/* ---------- Loading skeleton ---------- */
/* Animated placeholder bar used while data is in flight. */
function SkeletonBar({ width = "100%", height = 12, style }) {
  return (
    <div style={{ width, height, background: "#EFEFEA", borderRadius: 3, animation: "rk-pulse 1.4s ease-in-out infinite", ...style }} />
  );
}

/* SkeletonRows renders N stacked skeleton bars for list placeholders. */
function SkeletonRows({ n = 3, gap = 8 }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap }}>
      {Array.from({ length: n }).map((_, i) => (
        <SkeletonBar key={i} width={i === 0 ? "80%" : i === 1 ? "65%" : "72%"} />
      ))}
    </div>
  );
}

/* ---------- Error state ---------- */
function SectionError({ msg, style }) {
  return (
    <div style={{ fontFamily: MONO, fontSize: 12, color: "#A6503A", background: "#FDF4F2", border: "1px solid #F4C7BC", borderRadius: 6, padding: "10px 13px", lineHeight: 1.5, ...style }}>
      {msg || "Something went wrong. Check the backend is running."}
    </div>
  );
}

/* ============================================================
   NAV + INTRO
   ============================================================ */
function Nav() {
  const link = { textDecoration: "none", color: "#6E6E68" };
  return (
    <nav style={{ position: "sticky", top: 0, zIndex: 50, background: "rgba(251,251,250,0.92)", backdropFilter: "blur(6px)", borderBottom: "1px solid #E2E2DC" }}>
      <div style={{ maxWidth: 1040, margin: "0 auto", padding: "11px 28px", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
          <div style={{ width: 20, height: 20, border: "1.5px solid #1A1A1A", borderRadius: 5, display: "flex", alignItems: "center", justifyContent: "center" }}>
            <div style={{ width: 7, height: 7, border: "1.5px solid #1A1A1A", borderRadius: "50%" }} />
          </div>
          <span style={{ fontFamily: MONO, fontWeight: 600, fontSize: 14 }}>resolvekit</span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 18, fontFamily: MONO, fontSize: 12.5 }}>
          <a href="#resolve" style={link}>resolve</a>
          <a href="#compare" style={link}>compare</a>
          <a href="#graph" style={link}>graph</a>
          <a href="#data" style={link}>data</a>
          <a href="#custom" style={link}>custom</a>
          <a href="#autocomplete" style={link}>suggest</a>
          <a href="#parse" style={link}>parse</a>
          <a href="https://github.com/jm-rivera/resolvekit" target="_blank" rel="noreferrer" style={{ textDecoration: "none", color: "inherit", border: "1.5px solid #1A1A1A", borderRadius: 6, padding: "5px 11px" }}>github</a>
        </div>
      </div>
    </nav>
  );
}

function Intro() {
  return (
    <header style={{ maxWidth: 1040, margin: "0 auto", padding: "40px 28px 8px" }}>
      <h1 style={{ fontSize: 26, fontWeight: 700, letterSpacing: "-0.03em", margin: "0 0 8px" }}>Entity resolution playground</h1>
      <p style={{ fontSize: 15, color: "#5E5E58", margin: "0 0 16px", maxWidth: 600, lineHeight: 1.5 }}>
        Resolve messy place names, codes and aliases to canonical IDs — offline, deterministic, with a calibrated confidence on every result.
      </p>
      <div style={{ display: "inline-flex", alignItems: "center", gap: 10, fontFamily: MONO, fontSize: 13, border: "1.5px solid #1A1A1A", borderRadius: 6, padding: "8px 13px" }}>
        <span style={{ color: "#0B50BE", fontWeight: 700 }}>$</span> uv add resolvekit
      </div>
    </header>
  );
}

/* ============================================================
   01 — RESOLVE
   ============================================================ */
function ResolveSection({ vm }) {
  const res = vm.res;
  return (
    <section id="resolve" style={WRAP}>
      <SectionHead n="01">Resolve one string</SectionHead>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1.05fr", gap: 22, alignItems: "start" }} className="rk-grid-2">
        <div>
          <input value={vm.resolveQuery} onChange={vm.onResolveChange} placeholder="type anything: a typo, a code, an alias…"
            style={{ width: "100%", fontSize: 17, fontFamily: MONO, padding: "14px 15px", border: "1.5px solid #1A1A1A", borderRadius: 6, background: "#fff" }} />
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 12 }}>
            {vm.presets.map(p => (
              <button key={p.label} onClick={p.on} style={{ fontFamily: MONO, fontSize: 12.5, background: "#fff", border: "1px solid #C9C9C4", borderRadius: 6, padding: "5px 9px", cursor: "pointer" }}>{p.label}</button>
            ))}
          </div>
          <div style={{ fontFamily: MONO, fontSize: 12, color: "#8A8A84", marginTop: 14, lineHeight: 1.6 }}>cities, orgs &amp; groups all resolve · Congo → abstains · n/a → sentinel</div>
        </div>

        <div style={{ border: "1.5px solid #1A1A1A", borderRadius: 8, background: "#fff" }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "10px 16px", borderBottom: "1px solid #E2E2DC" }}>
            <span style={{ fontFamily: MONO, fontSize: 11.5, fontWeight: 600, letterSpacing: "0.06em", color: res.statusColor }}>{res.statusLabel}</span>
            <span style={{ fontFamily: MONO, fontSize: 11, color: "#A6A6A0" }}>pack: geo</span>
          </div>
          <div style={{ padding: "18px 16px" }}>
            {vm.resLoading && (
              <div>
                <SkeletonBar width="55%" height={22} style={{ marginBottom: 8 }} />
                <SkeletonBar width="40%" height={13} style={{ marginBottom: 16 }} />
                <SkeletonBar width="100%" height={10} style={{ marginBottom: 8 }} />
                <SkeletonBar width="85%" height={10} />
              </div>
            )}
            {vm.resError && !vm.resLoading && (
              <SectionError msg={vm.resErrorMsg} />
            )}
            {!vm.resLoading && !vm.resError && vm.resResolved && (
              <div>
                <div style={{ fontFamily: MONO, fontSize: 24, fontWeight: 600 }}>{res.entity_id}</div>
                <div style={{ fontSize: 13.5, color: "#5E5E58", marginTop: 3 }}>{res.canonical_name}</div>
                <div style={{ display: "flex", alignItems: "center", gap: 12, margin: "16px 0 6px" }}>
                  <div style={{ flex: 1, height: 10, border: "1.5px solid #1A1A1A", borderRadius: 2, overflow: "hidden" }}>
                    <div style={{ height: "100%", width: res.barW, background: "#0B50BE" }} />
                  </div>
                  <span style={{ fontFamily: MONO, fontSize: 13, fontWeight: 600 }}>{res.confPct}</span>
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", fontFamily: MONO, fontSize: 11, color: "#8A8A84" }}>
                  <span>confidence (calibrated)</span><span>tier: {res.match_tier}</span>
                </div>
                <div style={{ fontFamily: MONO, fontSize: 10.5, color: "#A6A6A0", margin: "16px 0 7px" }}>PIVOT — to=</div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                  {(res.pivots || []).map((pv, i) => (
                    <div key={i} style={{ fontFamily: MONO, fontSize: 12, border: "1px solid #C9C9C4", borderRadius: 5, padding: "4px 8px" }}>
                      <span style={{ color: "#A6A6A0" }}>{pv.k}</span> {pv.v}
                    </div>
                  ))}
                </div>
              </div>
            )}
            {!vm.resLoading && !vm.resError && vm.resAmbiguous && (
              <div>
                <div style={{ fontFamily: MONO, fontSize: 12.5, color: "#5E5E58", marginBottom: 10 }}>two candidates too close — abstains, confidence = None</div>
                {(res.candidates || []).map((c, i) => (
                  <div key={i} style={{ display: "flex", alignItems: "center", gap: 12, padding: "9px 0", borderTop: "1px solid #E2E2DC" }}>
                    <div style={{ flex: 1 }}>
                      <div style={{ fontFamily: MONO, fontSize: 16, fontWeight: 600 }}>{c.id}</div>
                      <div style={{ fontSize: 12, color: "#5E5E58" }}>{c.name}</div>
                    </div>
                    <div style={{ width: 96 }}>
                      <div style={{ height: 8, border: "1.5px solid #1A1A1A", borderRadius: 2, overflow: "hidden" }}>
                        <div style={{ height: "100%", width: c.barW, background: "#B8B8B2" }} />
                      </div>
                      <div style={{ fontFamily: MONO, fontSize: 11, color: "#5E5E58", textAlign: "right", marginTop: 3 }}>{c.confPct}</div>
                    </div>
                  </div>
                ))}
              </div>
            )}
            {!vm.resLoading && !vm.resError && vm.resNoMatch && (
              <div>
                <div style={{ fontFamily: MONO, fontSize: 20, fontWeight: 600, color: "#6E6E68" }}>None</div>
                <div style={{ fontFamily: MONO, fontSize: 11.5, color: "#A6A6A0", marginTop: 4 }}>{res.reason}</div>
                <div style={{ fontSize: 12.5, color: "#5E5E58", marginTop: 10, lineHeight: 1.5 }}>{res.reason_note}</div>
              </div>
            )}
            {!vm.resLoading && !vm.resError && vm.resEmpty && (
              <div style={{ fontSize: 13, color: "#A6A6A0", padding: "12px 0", textAlign: "center" }}>type above…</div>
            )}

            <div style={{ display: "flex", gap: 8, marginTop: 18, borderTop: "1px solid #E2E2DC", paddingTop: 13 }}>
              <GhostButton onClick={vm.toggleExplain}>{vm.explainLabel}</GhostButton>
              <GhostButton onClick={vm.toggleCodeR}>{vm.codeLabelR}</GhostButton>
            </div>
            {vm.showExplain && (
              vm.explainLoading
                ? <div style={{ marginTop: 12 }}><SkeletonRows n={5} /></div>
                : vm.explainError
                  ? <SectionError msg={vm.explainError} style={{ marginTop: 12 }} />
                  : <ExplainBlock>{vm.explainText || "No explain text returned."}</ExplainBlock>
            )}
            {vm.showCodeR && <CodeBlock>{res.code}</CodeBlock>}
          </div>
        </div>
      </div>
    </section>
  );
}

/* ============================================================
   02 — AUTOCOMPLETE
   ============================================================ */
function AutocompleteSection({ vm }) {
  return (
    <section id="autocomplete" style={WRAP}>
      <SectionHead n="06">Typeahead — suggest()</SectionHead>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1.05fr", gap: 22, alignItems: "start" }} className="rk-grid-2">
        <div>
          <input value={vm.suggestQuery} onChange={vm.onSuggestChange} placeholder="start typing: germny, Paris, NATO, Toyota…"
            style={{ width: "100%", fontSize: 16, fontFamily: MONO, padding: "13px 15px", border: "1.5px solid #1A1A1A", borderRadius: 6, background: "#fff" }} />
          <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 12, flexWrap: "wrap" }}>
            <span style={{ fontFamily: MONO, fontSize: 11, color: "#8A8A84" }}>scope:</span>
            {vm.suggestScopeOptions.map(o => (
              <Pill key={o.key} active={o.active} onClick={o.on} style={{ padding: "4px 10px", fontSize: 11.5 }}>{o.label}</Pill>
            ))}
          </div>
          <div style={{ fontFamily: MONO, fontSize: 12, color: "#8A8A84", marginTop: 12 }}>ranked: exact_prefix › token_prefix › infix › fuzzy</div>
          <GhostButton onClick={vm.toggleCodeS} style={{ marginTop: 12 }}>{vm.codeLabelS}</GhostButton>
          {vm.showCodeS && <CodeBlock>{vm.suggestCode}</CodeBlock>}
        </div>
        <div style={{ border: "1.5px solid #1A1A1A", borderRadius: 8, background: "#fff", overflow: "hidden", minHeight: 110 }}>
          <div style={{ padding: "9px 15px", borderBottom: "1px solid #E2E2DC", fontFamily: MONO, fontSize: 11, color: "#8A8A84" }}>{vm.suggestHeader}</div>
          {/* Keep prior results visible during a refetch to avoid flicker:
             only show the skeleton on the first load (nothing to show yet),
             and dim the existing list while the next response is in flight. */}
          {vm.suggestLoading && vm.suggestions.length === 0 && (
            <div style={{ padding: "14px 15px" }}><SkeletonRows n={3} gap={10} /></div>
          )}
          {vm.suggestError && vm.suggestions.length === 0 && (
            <div style={{ padding: "12px 15px" }}><SectionError msg={vm.suggestErrorMsg} /></div>
          )}
          {!vm.suggestLoading && !vm.suggestError && vm.suggestEmpty && (
            <div style={{ padding: "22px 15px", textAlign: "center", color: "#A6A6A0", fontFamily: MONO, fontSize: 12 }}>[ ] no suggestions</div>
          )}
          {vm.suggestions.length > 0 && (
            <div style={{ opacity: vm.suggestLoading ? 0.5 : 1, transition: "opacity 120ms ease" }}>
              {vm.suggestions.map((s, i) => (
                <div key={i} style={{ display: "flex", alignItems: "center", gap: 12, padding: "9px 15px", borderBottom: "1px solid #EFEFEA" }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontFamily: MONO, fontSize: 14 }}>
                      <span>{s.pre}</span><span style={{ background: "#CFF3EC", fontWeight: 600 }}>{s.hl}</span><span>{s.post}</span>
                    </div>
                    <div style={{ fontFamily: MONO, fontSize: 11, color: "#8A8A84" }}>{s.entity_id}</div>
                  </div>
                  <span style={{ fontFamily: MONO, fontSize: 10.5, border: "1px solid #C9C9C4", borderRadius: 5, padding: "2px 7px" }}>{s.match_class}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

/* ============================================================
   03 — PARSE
   ============================================================ */
function ParseSection({ vm }) {
  return (
    <section id="parse" style={WRAP}>
      <SectionHead n="07" mb={14}>Extract entities from text — parse()</SectionHead>
      <div style={{ display: "flex", gap: 6, marginBottom: 11, flexWrap: "wrap" }}>
        {vm.parsePresets.map(p => (
          <button key={p.label} onClick={p.on} style={{ fontFamily: MONO, fontSize: 12, background: "#fff", border: "1px solid #C9C9C4", borderRadius: 6, padding: "5px 9px", cursor: "pointer" }}>{p.label}</button>
        ))}
      </div>
      <textarea value={vm.parseText} onChange={vm.onParseChange} rows={2}
        style={{ width: "100%", fontSize: 15, lineHeight: 1.55, fontFamily: SANS, padding: "13px 15px", border: "1.5px solid #1A1A1A", borderRadius: 6, background: "#fff", resize: "vertical", color: "#1A1A1A" }} />
      <div style={{ display: "grid", gridTemplateColumns: "1.1fr 1fr", gap: 22, alignItems: "start", marginTop: 16 }} className="rk-grid-2">
        <div style={{ border: "1.5px solid #1A1A1A", borderRadius: 8, background: "#fff", padding: 16 }}>
          <div style={{ fontFamily: MONO, fontSize: 10.5, color: "#A6A6A0", marginBottom: 9 }}>LINKED — {vm.parseCount} entities</div>
          {/* Skeleton only on the first parse; during a refetch keep the prior
             linked text on screen (dimmed) so editing doesn't flicker. */}
          {vm.parseLoading && !vm.parseHasResult && <SkeletonRows n={2} gap={8} />}
          {vm.parseError && !vm.parseHasResult && (
            <SectionError msg={vm.parseUnavailable ? "parse() requires resolvekit[parsing] — not available on this server." : vm.parseErrorMsg} />
          )}
          {vm.parseHasResult && (
            <div style={{ fontSize: 15, lineHeight: 1.95, opacity: vm.parseLoading ? 0.5 : 1, transition: "opacity 120ms ease" }}>
              {vm.parseSegments.map((seg, i) => (
                <span key={i} style={{ background: seg.bg, borderBottom: seg.underline, fontWeight: seg.weight }}>{seg.text}</span>
              ))}
            </div>
          )}
        </div>
        <div style={{ border: "1.5px solid #1A1A1A", borderRadius: 8, background: "#fff", overflow: "hidden" }}>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr auto", gap: 8, padding: "8px 15px", borderBottom: "1px solid #E2E2DC", fontFamily: MONO, fontSize: 10, color: "#A6A6A0" }}>
            <span>surface [s:e]</span><span>entity_id</span><span>conf</span>
          </div>
          {vm.parseLoading && !vm.parseHasResult && (
            <div style={{ padding: "12px 15px" }}><SkeletonRows n={3} gap={8} /></div>
          )}
          {vm.parseError && !vm.parseHasResult && (
            <div style={{ padding: "12px 15px" }}><SectionError msg={vm.parseUnavailable ? "parse() unavailable." : vm.parseErrorMsg} /></div>
          )}
          {vm.parseHasResult && (
            <div style={{ opacity: vm.parseLoading ? 0.5 : 1, transition: "opacity 120ms ease" }}>
              {vm.parseEntities.map((e, i) => (
                <div key={i} style={{ display: "grid", gridTemplateColumns: "1fr 1fr auto", gap: 8, alignItems: "center", padding: "8px 15px", borderBottom: "1px solid #EFEFEA" }}>
                  <div>
                    <div style={{ fontFamily: MONO, fontSize: 12.5 }}>{e.surface}</div>
                    <div style={{ fontFamily: MONO, fontSize: 10, color: "#A6A6A0" }}>{e.range}</div>
                  </div>
                  <span style={{ fontFamily: MONO, fontSize: 11.5, color: e.idColor }}>{e.id}</span>
                  <span style={{ fontFamily: MONO, fontSize: 11.5, color: "#5E5E58" }}>{e.confPct}</span>
                </div>
              ))}
            </div>
          )}
          <div style={{ fontFamily: MONO, padding: "9px 15px", fontSize: 11, color: "#8A8A84", lineHeight: 1.5 }}>ambiguous → entity_id=None · spans are linked left-to-right, longest-match</div>
        </div>
      </div>
      <GhostButton onClick={vm.toggleCodeP} style={{ marginTop: 14 }}>{vm.codeLabelP}</GhostButton>
      {vm.showCodeP && <CodeBlock maxWidth={640}>{vm.parseCode}</CodeBlock>}
    </section>
  );
}

/* ============================================================
   04 — GRAPH
   ============================================================ */
function GraphSection({ vm }) {
  const gd = vm.groupData || {};
  const rd = vm.regionData || {};
  return (
    <section id="graph" style={WRAP}>
      <SectionHead n="03">Query the graph — even as of a past date</SectionHead>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 22, alignItems: "start" }} className="rk-grid-2">
        {/* members_of */}
        <div style={{ border: "1.5px solid #1A1A1A", borderRadius: 8, background: "#fff", padding: 16 }}>
          <div style={{ fontFamily: MONO, fontSize: 12, color: "#5E5E58", marginBottom: 12 }}>r.members_of(<span style={{ color: "#1A1A1A" }}>group</span>, as_of=…)</div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 16 }}>
            {vm.groups.map(g => <Pill key={g.name} active={g.active} onClick={g.on}>{g.name}</Pill>)}
          </div>
          {vm.graphLoading && (
            <div>
              <SkeletonBar width="55%" height={16} style={{ marginBottom: 8 }} />
              <div style={{ display: "flex", flexWrap: "wrap", gap: 5, minHeight: 56 }}>
                {Array.from({ length: 12 }).map((_, i) => <SkeletonBar key={i} width={32} height={22} style={{ borderRadius: 5 }} />)}
              </div>
            </div>
          )}
          {vm.graphError && !vm.graphLoading && (
            <SectionError msg={vm.graphErrorMsg} style={{ marginBottom: 12 }} />
          )}
          {!vm.graphLoading && !vm.graphError && (
            <div>
              <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap", marginBottom: 10 }}>
                <span style={{ fontSize: 15, fontWeight: 600, whiteSpace: "nowrap" }}>{gd.name}</span>
                <span style={{ fontFamily: MONO, fontSize: 12, color: "#5E5E58" }}>{gd.count} · {gd.asOfLabel}</span>
              </div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 5, minHeight: 56 }}>
                {(gd.members || []).map((m, i) => (
                  <div key={i} style={{ fontFamily: MONO, fontSize: 11.5, border: "1px solid #C9C9C4", borderRadius: 5, padding: "3px 6px" }}>{m}</div>
                ))}
                {gd.more && <div style={{ fontFamily: MONO, fontSize: 11.5, color: "#A6A6A0", padding: "4px 5px" }}>{gd.moreLabel}</div>}
              </div>
            </div>
          )}
          <div style={{ marginTop: 16, paddingTop: 14, borderTop: "1px solid #E2E2DC" }}>
            <div style={{ marginBottom: 8 }}>
              <span style={{ fontFamily: MONO, fontSize: 12 }}>as_of = {vm.asOf}</span>
            </div>
            <input className="rk-range" type="range" min="2014" max="2026" step="1" value={vm.asOf} onChange={vm.onAsOf} style={{ width: "100%" }} />
            <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 13 }}>
              {/* Dynamic code example, keyed to the selected group */}
              <span style={{ fontFamily: MONO, fontSize: 11.5, color: "#5E5E58" }}>{vm.subjectInGroupCode || 'is_member("United Kingdom", group)'}</span>
              <span style={{ marginLeft: "auto", fontFamily: MONO, fontSize: 12.5, fontWeight: 600, border: "1px solid #1A1A1A", borderRadius: 5, padding: "2px 8px" }}>{vm.ukLabel}</span>
            </div>
          </div>
        </div>
        {/* within */}
        <div style={{ border: "1.5px solid #1A1A1A", borderRadius: 8, background: "#fff", padding: 16 }}>
          <div style={{ fontFamily: MONO, fontSize: 12, color: "#5E5E58", marginBottom: 12 }}>r.within(<span style={{ color: "#1A1A1A" }}>region</span>, to="iso3")</div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 16 }}>
            {vm.regions.map(rg => <Pill key={rg.name} active={rg.active} onClick={rg.on}>{rg.name}</Pill>)}
          </div>
          {vm.graphLoading && (
            <div>
              <SkeletonBar width="50%" height={16} style={{ marginBottom: 8 }} />
              <div style={{ display: "flex", flexWrap: "wrap", gap: 5, minHeight: 56 }}>
                {Array.from({ length: 8 }).map((_, i) => <SkeletonBar key={i} width={36} height={22} style={{ borderRadius: 5 }} />)}
              </div>
            </div>
          )}
          {vm.graphError && !vm.graphLoading && (
            <SectionError msg={vm.graphErrorMsg} style={{ marginBottom: 12 }} />
          )}
          {!vm.graphLoading && !vm.graphError && (
            <div>
              <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 10 }}>
                <span style={{ fontSize: 15, fontWeight: 600, whiteSpace: "nowrap" }}>{rd.name}</span>
                <span style={{ fontFamily: MONO, fontSize: 12, color: "#5E5E58" }}>{rd.count} entities</span>
              </div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 5, minHeight: 56 }}>
                {(rd.codes || []).map((c, i) => (
                  <div key={i} style={{ fontFamily: MONO, fontSize: 11.5, background: "#F4F4F1", border: "1px solid #C9C9C4", borderRadius: 5, padding: "3px 6px" }}>{c}</div>
                ))}
              </div>
              <div style={{ fontFamily: MONO, fontSize: 11, color: "#8A8A84", marginTop: 12, lineHeight: 1.5 }}>{rd.note}</div>
            </div>
          )}
        </div>
      </div>
      <GhostButton onClick={vm.toggleCodeG} style={{ marginTop: 14 }}>{vm.codeLabelG}</GhostButton>
      {vm.showCodeG && <CodeBlock maxWidth={680}>{vm.graphCode}</CodeBlock>}
    </section>
  );
}

/* ============================================================
   05 — DATA (spreadsheet)
   ============================================================ */
function DataSection({ vm }) {
  return (
    <section id="data" style={WRAP}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 6 }}>
        <span style={{ fontFamily: MONO, fontSize: 13, color: "#0B50BE", fontWeight: 600 }}>04</span>
        <h2 style={{ fontSize: 19, fontWeight: 700, letterSpacing: "-0.02em", margin: 0 }}>Clean a spreadsheet column</h2>
      </div>
      <p style={{ fontSize: 13.5, color: "#5E5E58", margin: "0 0 16px", maxWidth: 640, lineHeight: 1.5 }}>
        Drop or paste a CSV / TSV. resolvekit deduplicates internally, so a 10k-row column with 50 distinct values runs 50 resolutions — not 10k.
      </p>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1.25fr", gap: 22, alignItems: "start" }} className="rk-grid-2">
        {/* input + format */}
        <div>
          <label onDrop={vm.onByodDrop} onDragOver={vm.onByodDragOver}
            style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 6, border: "1.5px dashed #1A1A1A", borderRadius: 8, padding: "22px 16px", cursor: "pointer", background: "#fff", textAlign: "center" }}>
            <span style={{ fontFamily: MONO, fontSize: 13, fontWeight: 600 }}>drop a file or click</span>
            <span style={{ fontFamily: MONO, fontSize: 11.5, color: "#8A8A84" }}>{vm.byodFileLabel}</span>
            <input type="file" accept=".csv,.tsv,.txt" onChange={vm.onByodFile} style={{ display: "none" }} />
          </label>
          <div style={{ fontFamily: MONO, fontSize: 11, color: "#8A8A84", textAlign: "center", margin: "8px 0" }}>— or paste rows —</div>
          <textarea value={vm.byodText} onChange={vm.onByodText} rows={5} placeholder={"country,value\nBrasil,120\nCote dIvoire,88"}
            style={{ width: "100%", fontSize: 12.5, lineHeight: 1.5, fontFamily: MONO, padding: "11px 12px", border: "1.5px solid #1A1A1A", borderRadius: 6, background: "#fff", resize: "vertical", color: "#1A1A1A" }} />
          <div style={{ border: "1px solid #D6D6D1", borderRadius: 6, padding: "12px 13px", marginTop: 14, background: "#F8F8F5" }}>
            <div style={{ fontFamily: MONO, fontSize: 10.5, color: "#8A8A84", marginBottom: 7 }}>FORMAT</div>
            <div style={{ fontFamily: MONO, fontSize: 11.5, color: "#5E5E58", lineHeight: 1.7 }}>
              • first row = header<br />• one entity per cell<br />• messy names, ISO codes &amp; aliases all work<br />• pick the column to resolve →
            </div>
          </div>
        </div>
        {/* column picker + results */}
        <div style={{ border: "1.5px solid #1A1A1A", borderRadius: 8, background: "#fff", overflow: "hidden" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "11px 15px", borderBottom: "1px solid #E2E2DC", flexWrap: "wrap" }}>
            <span style={{ fontFamily: MONO, fontSize: 11, color: "#8A8A84" }}>resolve column:</span>
            {vm.byodCols.map(col => (
              <Pill key={col.name} active={col.active} onClick={col.on} style={{ fontSize: 11.5, borderRadius: 5, padding: "3px 9px" }}>{col.name}</Pill>
            ))}
            <span style={{ marginLeft: "auto", fontFamily: MONO, fontSize: 11, color: "#8A8A84" }}>{vm.byodSummary}</span>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "1.3fr 1.1fr 0.6fr 0.6fr", gap: 8, padding: "8px 15px", borderBottom: "1px solid #E2E2DC", fontFamily: MONO, fontSize: 10, color: "#A6A6A0" }}>
            <span>input</span><span>entity_id</span><span>iso3</span><span>conf</span>
          </div>
          {vm.bulkLoading && (
            <div style={{ padding: "14px 15px" }}><SkeletonRows n={4} gap={10} /></div>
          )}
          {vm.bulkError && !vm.bulkLoading && (
            <div style={{ padding: "12px 15px" }}><SectionError msg={vm.bulkErrorMsg} /></div>
          )}
          {!vm.bulkLoading && !vm.bulkError && (
            <div className="rk-scroll" style={{ maxHeight: 290, overflowY: "auto" }}>
              {vm.byodResults.map((r, i) => (
                <div key={i} style={{ display: "grid", gridTemplateColumns: "1.3fr 1.1fr 0.6fr 0.6fr", gap: 8, alignItems: "center", padding: "8px 15px", borderBottom: "1px solid #EFEFEA" }}>
                  <span style={{ fontFamily: MONO, fontSize: 12 }}>{r.input}</span>
                  {/* idColor mapped from server's id_color in app.jsx */}
                  <span style={{ fontFamily: MONO, fontSize: 11.5, color: r.idColor }}>{r.id}</span>
                  <span style={{ fontFamily: MONO, fontSize: 11.5, color: "#5E5E58" }}>{r.iso3}</span>
                  <span style={{ fontFamily: MONO, fontSize: 11.5, color: "#5E5E58" }}>{r.conf}</span>
                </div>
              ))}
            </div>
          )}
          {vm.byodMore && (
            <div style={{ fontFamily: MONO, padding: "8px 15px", fontSize: 11, color: "#A6A6A0", borderTop: "1px solid #EFEFEA" }}>{vm.byodMoreLabel}</div>
          )}
        </div>
      </div>
      <GhostButton onClick={vm.toggleCodeB} style={{ marginTop: 14 }}>{vm.codeLabelB}</GhostButton>
      {vm.showCodeB && <CodeBlock maxWidth={680}>{vm.byodCode}</CodeBlock>}
    </section>
  );
}

/* ============================================================
   05 — CUSTOM DATA (bring your own records → from_records)
   ============================================================ */
function CustomDataSection({ vm }) {
  const r = vm.byodResolution;
  const st = r ? r.status : null;
  const label = st === "resolved" ? "RESOLVED" : st === "ambiguous" ? "AMBIGUOUS" : st === "no_match" ? "NO MATCH" : "";
  const color = st === "resolved" ? "#108479" : st === "ambiguous" ? "#9A6700" : st === "no_match" ? "#8A8A84" : "#A6A6A0";
  return (
    <section id="custom" style={WRAP}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 6 }}>
        <span style={{ fontFamily: MONO, fontSize: 13, color: "#0B50BE", fontWeight: 600 }}>05</span>
        <h2 style={{ fontSize: 19, fontWeight: 700, letterSpacing: "-0.02em", margin: 0 }}>Bring your own data — resolve against your dictionary</h2>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1.05fr", gap: 22, alignItems: "start", marginTop: 16 }} className="rk-grid-2">
        {/* left: the user's records */}
        <div>
          <div style={{ fontFamily: MONO, fontSize: 10.5, color: "#A6A6A0", marginBottom: 7 }}>YOUR RECORDS — CSV (id, name, aliases, code)</div>
          <textarea value={vm.byodCustomText} onChange={vm.onByodCustomText} rows={8}
            style={{ width: "100%", fontSize: 12.5, lineHeight: 1.5, fontFamily: MONO, padding: "11px 12px", border: "1.5px solid #1A1A1A", borderRadius: 6, background: "#fff", resize: "vertical", color: "#1A1A1A" }} />
          <div style={{ fontFamily: MONO, fontSize: 11, color: "#8A8A84", marginTop: 10, lineHeight: 1.6 }}>
            {vm.byodRecordCount != null ? `${vm.byodRecordCount} records loaded` : "building…"} · aliases are ;-separated · a 'name' column is required
          </div>
          <GhostButton onClick={vm.toggleCodeC} style={{ marginTop: 12 }}>{vm.codeLabelC}</GhostButton>
          {vm.showCodeC && <CodeBlock>{vm.byodCustomCode}</CodeBlock>}
        </div>
        {/* right: query + result */}
        <div>
          <input value={vm.byodCustomQuery} onChange={vm.onByodCustomQuery} onKeyDown={vm.onByodCustomKeyDown}
            placeholder="resolve a messy reference against your records…"
            style={{ width: "100%", fontSize: 15, fontFamily: MONO, padding: "12px 14px", border: "1.5px solid #1A1A1A", borderRadius: 6, background: "#fff" }} />
          <div style={{ display: "flex", alignItems: "center", gap: 6, margin: "12px 0 14px", flexWrap: "wrap" }}>
            <span style={{ fontFamily: MONO, fontSize: 11, color: "#8A8A84" }}>try:</span>
            {vm.byodCustomExamples.map(ex => (
              <button key={ex.label} onClick={ex.on} style={{ fontFamily: MONO, fontSize: 12, background: "#fff", border: "1px solid #C9C9C4", borderRadius: 6, padding: "4px 9px", cursor: "pointer" }}>{ex.label}</button>
            ))}
          </div>
          <div style={{ border: "1.5px solid #1A1A1A", borderRadius: 8, background: "#fff", minHeight: 150 }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "10px 16px", borderBottom: "1px solid #E2E2DC" }}>
              <span style={{ fontFamily: MONO, fontSize: 11.5, fontWeight: 600, letterSpacing: "0.06em", color }}>{label}</span>
              <span style={{ fontFamily: MONO, fontSize: 11, color: "#A6A6A0" }}>pack: custom</span>
            </div>
            <div style={{ padding: "18px 16px" }}>
              {vm.byodCustomLoading && (
                <div>
                  <SkeletonBar width="55%" height={22} style={{ marginBottom: 8 }} />
                  <SkeletonBar width="40%" height={13} style={{ marginBottom: 16 }} />
                  <SkeletonBar width="100%" height={10} />
                </div>
              )}
              {vm.byodCustomError && !vm.byodCustomLoading && <SectionError msg={vm.byodCustomErrorMsg} />}
              {!vm.byodCustomLoading && !vm.byodCustomError && r && st === "resolved" && (
                <div>
                  <div style={{ fontFamily: MONO, fontSize: 22, fontWeight: 600 }}>{r.entityId}</div>
                  <div style={{ fontSize: 13.5, color: "#5E5E58", marginTop: 3 }}>{r.canonicalName}</div>
                  <div style={{ display: "flex", alignItems: "center", gap: 12, margin: "16px 0 6px" }}>
                    <div style={{ flex: 1, height: 10, border: "1.5px solid #1A1A1A", borderRadius: 2, overflow: "hidden" }}>
                      <div style={{ height: "100%", width: r.barW, background: "#0B50BE" }} />
                    </div>
                    <span style={{ fontFamily: MONO, fontSize: 13, fontWeight: 600 }}>{r.confPct}</span>
                  </div>
                  <div style={{ display: "flex", justifyContent: "space-between", fontFamily: MONO, fontSize: 11, color: "#8A8A84" }}>
                    <span>calibrated confidence</span><span>matched: {r.matchTier}</span>
                  </div>
                </div>
              )}
              {!vm.byodCustomLoading && !vm.byodCustomError && r && st === "no_match" && (
                <div>
                  <div style={{ fontFamily: MONO, fontSize: 20, fontWeight: 600, color: "#6E6E68" }}>None</div>
                  <div style={{ fontSize: 12.5, color: "#5E5E58", marginTop: 10, lineHeight: 1.5 }}>No match in your records — resolvekit abstains rather than guess.</div>
                </div>
              )}
              {!vm.byodCustomLoading && !vm.byodCustomError && r && st === "ambiguous" && (
                <div style={{ fontFamily: MONO, fontSize: 12.5, color: "#5E5E58" }}>two records too close — abstains (confidence = None)</div>
              )}
              {!vm.byodCustomLoading && !vm.byodCustomError && !r && (
                <div style={{ fontSize: 13, color: "#A6A6A0", padding: "12px 0", textAlign: "center" }}>type a query to resolve against your records…</div>
              )}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

/* ============================================================
   06 — COMPARE (latency)
   ============================================================ */
const COMPARE_TARGET_HINT = {
  name: "canonical name", iso2: "ISO 3166-1 alpha-2", iso3: "ISO 3166-1 alpha-3",
  dcid: "Data Commons ID", wikidata: "Wikidata QID",
};

function fmtMs(ms) {
  if (ms == null) return "—";
  return ms >= 100 ? `${Math.round(ms)} ms` : `${ms.toFixed(1)} ms`;
}

function CompareSection({ vm }) {
  const GRID = "1.25fr 1.5fr 0.7fr 0.8fr auto";
  return (
    <section id="compare" style={WRAP}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 6 }}>
        <span style={{ fontFamily: MONO, fontSize: 13, color: "#0B50BE", fontWeight: 600 }}>02</span>
        <h2 style={{ fontSize: 19, fontWeight: 700, letterSpacing: "-0.02em", margin: 0 }}>Resolve &amp; compare — result and latency</h2>
      </div>
      <p style={{ fontSize: 13.5, color: "#5E5E58", margin: "0 0 16px", maxWidth: 660, lineHeight: 1.5 }}>
        Resolve one query with four tools and see what each returns &mdash; and how fast. resolvekit runs in-process; only Data Commons Resolve pays a network round-trip. Pick a target below; some tools can&rsquo;t emit some targets.
      </p>

      {/* query input */}
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10, flexWrap: "wrap" }}>
        <input value={vm.compareQuery} onChange={vm.onCompareChange} onKeyDown={vm.onCompareKeyDown}
          placeholder="a place to resolve, e.g. Deutschland"
          style={{ flex: "1 1 240px", minWidth: 200, fontSize: 13.5, fontFamily: MONO, padding: "8px 12px", border: "1.5px solid #1A1A1A", borderRadius: 6, background: "#fff" }} />
        <button onClick={vm.onCompareRun}
          style={{ fontFamily: MONO, fontSize: 11.5, background: "#0B50BE", color: "#fff", border: "none", borderRadius: 6, padding: "8px 15px", cursor: "pointer" }}>
          run compare
        </button>
        <span style={{ fontFamily: MONO, fontSize: 11, color: "#A6A6A0" }}>↵ to run</span>
      </div>

      {/* curated examples — orgs, groups, cities, non-Latin scripts */}
      <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 12, flexWrap: "wrap" }}>
        <span style={{ fontFamily: MONO, fontSize: 11, color: "#8A8A84" }}>try:</span>
        {vm.compareExamples.map(ex => (
          <button key={ex.label} onClick={ex.on} style={{ fontFamily: MONO, fontSize: 12, background: "#fff", border: "1px solid #C9C9C4", borderRadius: 6, padding: "4px 9px", cursor: "pointer" }}>{ex.label}</button>
        ))}
      </div>

      {/* target (`to=`) selector */}
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 16, flexWrap: "wrap" }}>
        <span style={{ fontFamily: MONO, fontSize: 11, color: "#8A8A84" }}>resolve to:</span>
        {vm.compareToOptions.map(o => (
          <Pill key={o.key} active={o.active} onClick={o.on} style={{ padding: "4px 10px", fontSize: 11.5 }}>{o.label}</Pill>
        ))}
        <span style={{ fontFamily: MONO, fontSize: 11, color: "#A6A6A0" }}>{COMPARE_TARGET_HINT[vm.compareTo]}</span>
      </div>

      {vm.compareError && <SectionError msg={vm.compareErrorMsg} style={{ marginBottom: 16 }} />}

      {/* results + latency table */}
      <div style={{ border: "1.5px solid #1A1A1A", borderRadius: 8, background: "#fff", overflow: "hidden", marginBottom: 24 }}>
        <div style={{ display: "grid", gridTemplateColumns: GRID, gap: 10, padding: "9px 16px", borderBottom: "1px solid #E2E2DC", fontFamily: MONO, fontSize: 10, color: "#A6A6A0" }}>
          <span>tool</span><span>result &rarr; {vm.compareTo}</span><span>confidence</span><span>latency</span><span />
        </div>
        {vm.compareRows.map((r, i) => (
          <div key={i} style={{ display: "grid", gridTemplateColumns: GRID, gap: 10, alignItems: "center", padding: "11px 16px",
            borderBottom: i < vm.compareRows.length - 1 ? "1px solid #EFEFEA" : "none", background: r.hi ? "#F6FAFF" : "#fff" }}>
            <span style={{ fontFamily: MONO, fontSize: 13, fontWeight: r.hi ? 700 : 500, color: r.hi ? "#0B50BE" : "#1A1A1A" }}>{r.tool}</span>
            <div style={{ fontFamily: MONO, fontSize: 13.5, minWidth: 0 }}>
              {r.loading
                ? <SkeletonBar width="55%" height={13} />
                : !r.supported
                  ? <span style={{ color: "#A6A6A0", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", display: "block" }}>not supported{r.note ? ` · ${r.note}` : ""}</span>
                  : r.value != null
                    ? <span style={{ fontWeight: 600 }}>{r.value}</span>
                    : (r.candidates && r.candidates.length)
                      ? <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
                          <span style={{ fontSize: 10, color: "#9A6700" }}>ambiguous — top candidates</span>
                          {r.candidates.map((c, ci) => (
                            <div key={ci} style={{ fontSize: 12.5, lineHeight: 1.3, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                              <span style={{ fontWeight: 600 }}>{c.value || "—"}</span>
                              <span style={{ color: "#8A8A84" }}>{"  " + (c.name || "")}</span>
                              <span style={{ color: "#B0B0AA" }}>{"  " + (c.conf_pct || "")}</span>
                            </div>
                          ))}
                        </div>
                      : <span style={{ color: "#A6A6A0" }}>—{r.note ? ` · ${r.note}` : " no match"}</span>}
            </div>
            <span style={{ fontFamily: MONO, fontSize: 11.5, color: "#5E5E58" }}>{r.confPct || "—"}</span>
            <span style={{ fontFamily: MONO, fontSize: 11.5, color: "#5E5E58" }}>{r.loading ? "…" : fmtMs(r.latency)}</span>
            <span style={{ fontFamily: MONO, fontSize: 9.5, border: "1px solid #C9C9C4", borderRadius: 5, padding: "2px 6px", color: "#8A8A84" }}>{r.badge}</span>
          </div>
        ))}
        {!vm.compareHasData && !vm.compareLoading && !vm.compareError && (
          <div style={{ fontFamily: MONO, fontSize: 12, color: "#8A8A84", textAlign: "center", padding: "14px 0" }}>type a query and press ↵ (or hit “run compare”)</div>
        )}
      </div>

      {/* latency chart */}
      <div style={{ fontFamily: MONO, fontSize: 10.5, color: "#A6A6A0", marginBottom: 11 }}>MEASURED LATENCY PER RESOLVE — log scale, lower is better</div>
      <div style={{ border: "1.5px solid #1A1A1A", borderRadius: 8, background: "#fff", padding: 16 }}>
        {vm.compareLoading && vm.compareLatency.length === 0 && <SkeletonRows n={4} gap={10} />}
        {vm.compareLatency.map((l, i) => (
          <div key={i} style={{ display: "grid", gridTemplateColumns: "170px 1fr 84px", gap: 12, alignItems: "center", padding: "7px 0" }}>
            <span style={{ fontFamily: MONO, fontSize: 12, fontWeight: l.weight }}>{l.tool}</span>
            <div style={{ height: 14, background: "#EFEFEA", borderRadius: 3, overflow: "hidden" }}>
              <div style={{ height: "100%", width: l.w, background: l.barColor, borderRadius: 3 }} />
            </div>
            <span style={{ fontFamily: MONO, fontSize: 12, textAlign: "right", fontWeight: l.weight }}>{l.label}</span>
          </div>
        ))}
        {!vm.compareLoading && vm.compareLatency.length === 0 && (
          <div style={{ fontFamily: MONO, fontSize: 12, color: "#8A8A84", textAlign: "center", padding: "12px 0" }}>run a compare to measure latencies</div>
        )}
      </div>
    </section>
  );
}

/* ============================================================
   FOOTER
   ============================================================ */
function Footer() {
  const btn = { textDecoration: "none", color: "inherit", fontFamily: MONO, fontSize: 12, border: "1.5px solid #1A1A1A", borderRadius: 6, padding: "7px 13px" };
  return (
    <footer style={{ maxWidth: 1040, margin: "0 auto", padding: "22px 28px 56px", borderTop: "1px solid #E2E2DC" }}>
      <div style={{ display: "flex", justifyContent: "flex-end", alignItems: "center", flexWrap: "wrap", gap: 14 }}>
        <div style={{ display: "flex", gap: 9 }}>
          <a href="https://github.com/jm-rivera/resolvekit" target="_blank" rel="noreferrer" style={btn}>github</a>
          <a href="https://jm-rivera.github.io/resolvekit/" target="_blank" rel="noreferrer" style={btn}>docs</a>
        </div>
      </div>
    </footer>
  );
}

/* Expose for app.jsx */
window.RK_UI = {
  Nav, Intro, Rule,
  ResolveSection, AutocompleteSection, ParseSection, GraphSection, DataSection, CustomDataSection, CompareSection,
  Footer
};
