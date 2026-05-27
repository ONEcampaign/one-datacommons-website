/* ============================================================
   Search Comparison — UI copy
   Every human-readable string the page renders lives here, so wording can be
   tweaked without touching component logic. Loaded as a plain global
   (window.STRINGS) before the .jsx files — same pattern as sample-queries.js.

   Deliberately NOT here:
     • Raw API field names shown verbatim in the telemetry block
       (elapsed_s, n_candidates, n_shapes, terminated_by, contained_in,
       truncated, timed_out, childEntityType, dc) — they mirror the JSON, so
       they stay inline next to the values they label.
     • Enum decode tables (CLASSIFICATION_TYPE_NAMES, RANKING/EVENT/…) in
       panels.jsx — those map protobuf enum integers, so they're data, not copy.
     • Count / plural fragments like "3 branches · 5 variables" — the number,
       noun, and inflection are assembled in the component.
   ============================================================ */

window.STRINGS = {
  /* ---- App header ---- */
  app: {
    title: "Search comparison",
    // One-liner shown under the title.
    tagline:
      "The ONE Data dc-search prototype reads a query the way an analyst " +
      "would — what's measured, where, when, and how it's broken down — " +
      "mapping it to the properties and constraints that define the relevant " +
      "StatVars, so it returns the ones that match the query rather than the " +
      "nearest vector-search match.",
    // Expandable "How it works" detail, shown under the tagline and above the query bar.
    howItWorks: {
      summary: "How it works",
      body:
        "When a user sends a query, dc-search runs it through a few stages. " +
        "It first extracts the moving parts with an LLM — the indicator(s), the " +
        "places, any dates. For each metric it retrieves a " +
        "shortlist of candidate StatVars by meaning (the same vector search " +
        "the Resolve endpoint uses), and resolves place names to real Data " +
        "Commons entities. " +
        "It then groups those candidates by their underlying graph " +
        "structure and makes a second LLM call to choose the structure — the " +
        "combination of properties and constraints — that best answers the " +
        "query. Finally it expands that structure into the StatVars that " +
        "match, drops any without data for the place, and attaches their " +
        "names, units, and date ranges. Answers stream back as each metric " +
        "resolves; when a query is genuinely ambiguous, dc-search asks a " +
        "clarifying question instead of guessing.",
      comparedLabel: "Shown for comparison (Data Commons' own endpoints):",
      compared: [
        {
          name: "Resolve",
          desc:
            "an embedding lookup — matches the query text to the nearest " +
            "StatVars by vector similarity, returned with relevance scores.",
        },
        {
          name: "explore/detect",
          desc:
            "the intent extractor behind Data Commons' /explore tool — pulls " +
            "places, variables, and classifications (ranking, comparison, " +
            "dates…) out of the query, then stops at detection.",
        },
      ],
    },
  },

  /* ---- Query bar ---- */
  queryBar: {
    placeholder: "Type a query…",
    inputAria:   "Custom query",
    run:         "Search",
    suggestAria: "Sample queries",
    tryLabel:    "Try:",
  },

  /* ---- Shared across panels ---- */
  common: {
    rawJson:      "Raw JSON",
    querySent:    "Query sent",
    understoodAs: "Understood as",
    none:         "—",
    unknownError: "Unknown error",
  },

  /* ---- Panel chrome: header labels/subtitles, error state ---- */
  panels: {
    resolve:  { label: "Resolve",        subtitle: "Indicator-embedding lookup" },
    dcsearch: { label: "dc-search",      subtitle: "Schema-guided StatVar matching" },
    detect:   { label: "explore/detect", subtitle: "Structured intent (places, vars, classifications)" },
    streamingTitle: "Streaming",
    error: {
      title: "Request failed",
      // Rendered around an inline <span class="mono">?base=&lt;url&gt;</span>.
      hintLead: "Endpoint unreachable from this origin. Check the host is up and CORS-enabled, or repoint the page with",
      hintTail: ".",
    },
  },

  /* ---- VariableCard chips + tooltips ---- */
  card: {
    score:       "score",
    data:        "data",
    noData:      "no data",
    dataTitle:   "Data exists at the resolved place",
    noDataTitle: "Place resolved but no data found",
    matchTitle:  "Retrieval sentence this DCID matched",
  },

  /* ---- Resolve panel body ---- */
  resolve: {
    empty:     "No candidates.",
    heading:   "Indicator-embedding hits",
    sourceTag: "embedding",
  },

  /* ---- dc-search panel body ---- */
  dcSearch: {
    // "Understood as" interpretation chips
    chipVar:         "var",
    chipIntent:      "intent",
    chipContainedIn: "contained-in",
    chipPlace:       "place",
    chipDate:        "date",
    // streaming placeholder strip
    streamingLabel:   "Streaming",
    streamingWaiting: "Waiting for interpretation event…",
    // results
    resolvedBranches:  "Resolved shapes",
    memberVariables:   "Member variables",
    resolvedVariables: "Resolved variables",
    // answer / skeleton tags
    clarificationTag: "clarification",
    topicTag:         "topic",
    variablesTag:     "variables",
    skeletonTimedOut: "timed out",
    skeletonLoading:  "loading",
    reasonLabel:      "reason:",
    // truncated / timed-out warning banner (tag is bolded in the JSX)
    banner: {
      truncatedTag:  "truncated",
      truncatedText: "— extraction returned more variables than cap.",
      timedOutTag:   "timed out",
      timedOutText:  "— some shapes did not finish within budget.",
    },
  },

  /* ---- contained-in expansion block ---- */
  containedIn: {
    pending: "expanding to children…",
    empty:   "contained-in detected — no children expanded",
    heading: "Contained-in expansion",
  },

  /* ---- explore/detect panel body ---- */
  detect: {
    // "Understood as" interpretation chips
    chipPlace:     "place",
    chipEntity:    "entity",
    chipChildType: "child type",
    chipIntent:    "intent",
    // comparison block
    comparisonTag:    "comparison",
    comparisonPlaces: "places",
    comparisonVars:   "vars",
    // results
    heading:  "Detected indicators",
    empty:    "No indicators detected. The pipeline ran but found no StatVars or topics matching the query.",
    topicTag: "topic",
  },

  /* ---- Telemetry expander — friendly labels only ----
     (raw API field keys like elapsed_s / n_candidates stay inline in panels.jsx) */
  telemetry: {
    label:           "Telemetry",
    latency:         "latency",
    candidates:      "candidates",
    resolver:        "resolver",
    resolverValue:   "indicator (embedding lookup)",
    topics:          "topics",
    statvars:        "statvars",
    entities:        "entities",
    comparison:      "comparison",
    classifications: "classifications",
    children:        "children",
    llmTokens:       "llm tokens",
  },
};
