# QRE Phase 0 golden corpus

These are expected engine outputs for the QRE Phase 0 evaluation harness. All goldens have been grounded against the live staging graph (dc-staging.one.org) or production graph (datacommons.one.org) on 2026-06-20. The `spec_id` field is omitted pending the canonical hasher run. Counts in the coverage matrix and floor table are informational; they are not hard-asserted limits and will be recomputed by the evaluation harness at run time.

## Goldens

```yaml
- id: "df-01"
  query: "health ODA grants from USA to Ethiopia"
  entry_path: "raw_text"
  tags:
    - behaviour: definite
    - domain: development_finance
    - seam: both
    - conjunction: none
  expected_status: "definite"
  expected_shape:
    population_type_dcid: "DevelopmentFinance"
    measured_property_dcid: "DevelopmentFinanceFlow"
    stat_type_dcid: "measuredValue"
    measurement_qualifier_dcid: null
    measurement_denominator_dcid: null
  expected_slots:
    - axis: "what"
      property_dcid: "DevelopmentFinanceScheme"
      binding_kind: "value"
      value_dcid: "ODAGrants"
    - axis: "how"
      property_dcid: "DevelopmentFinancePurpose"
      binding_kind: "value"
      value_dcid: "DAC/Health"
    - axis: "where"
      property_dcid: "DevelopmentFinanceRecipient"
      binding_kind: "value"
      value_dcid: "country/ETH"
  expected_stat_vars: ["ONE/CRS_DAC/Health-ODAGrants-ETH"]
  expected_entities:
    - dcid: "country/USA"
      role_kind: "directional"
      direction: "from"
      role_dcid: "observationAbout"
    - dcid: "country/ETH"
      role_kind: "directional"
      direction: "to"
      role_dcid: "DevelopmentFinanceRecipient"
  expected_no_data_reason: null
  candidate_count: null
  status: VERIFIED_AGAINST_GRAPH
  notes: |
    Probed staging. SV ONE/CRS_DAC/Health-ODAGrants-ETH confirmed: populationType=DevelopmentFinance,
    measuredProperty=DevelopmentFinanceFlow, statType=measuredValue, DevelopmentFinanceScheme=ODAGrants,
    DevelopmentFinancePurpose=DAC/Health (rollup node "Health Total"), DevelopmentFinanceRecipient=country/ETH.
    Observation probe: country/USA as observationAbout yields 402 observations, date range 1991-2024 (18 facets).
    country/USA and country/ETH both confirmed as valid entity dcids.
    DAC/Health confirmed as a DevelopmentFinancePurposeEnum node (CRS purpose code 12000).
    ODAGrants confirmed as a Property node (Official Development Assistance Grants).
    Seam-ON (rendered above): donor country/USA is role_kind=directional direction=from, role_dcid=observationAbout (observation-sourced);
    recipient country/ETH is role_kind=directional direction=to bound via DevelopmentFinanceRecipient constraint.
    Seam-OFF rendering: both entities become role_kind=subject with direction=null and role_dcid=null;
    the offline seam replay (tests/engine/test_seam_corpus.py) asserts this role collapse against fixtures in both modes.
    [conformance: named donor is a directional 'from' role sourced from observationAbout, symmetric with the recipient's directional 'to'; supersedes the earlier Rule-6 subject treatment per place-as-constraint-seam.md]

- id: "df-03"
  query: "health ODA grants from UK to Kenya"
  entry_path: "raw_text"
  tags:
    - behaviour: definite
    - domain: development_finance
    - seam: both
    - conjunction: none
  expected_status: "definite"
  expected_shape:
    population_type_dcid: "DevelopmentFinance"
    measured_property_dcid: "DevelopmentFinanceFlow"
    stat_type_dcid: "measuredValue"
    measurement_qualifier_dcid: null
    measurement_denominator_dcid: null
  expected_slots:
    - axis: "how"
      property_dcid: "DevelopmentFinancePurpose"
      binding_kind: "value"
      value_dcid: "DAC/Health"
    - axis: "what"
      property_dcid: "DevelopmentFinanceScheme"
      binding_kind: "value"
      value_dcid: "ODAGrants"
    - axis: "where"
      property_dcid: "DevelopmentFinanceRecipient"
      binding_kind: "value"
      value_dcid: "country/KEN"
  expected_stat_vars: ["ONE/CRS_DAC/Health-ODAGrants-KEN"]
  expected_entities:
    - dcid: "country/KEN"
      role_kind: "directional"
      direction: "to"
      role_dcid: "DevelopmentFinanceRecipient"
    - dcid: "country/GBR"
      role_kind: "directional"
      direction: "from"
      role_dcid: "observationAbout"
  expected_no_data_reason: null
  candidate_count: null
  status: VERIFIED_AGAINST_GRAPH
  notes: >
    Probed ONE/CRS_DAC/Health-ODAGrants-KEN on staging: confirmed 3 constraintProperties
    (DevelopmentFinancePurpose=DAC/Health, DevelopmentFinanceScheme=ODAGrants,
    DevelopmentFinanceRecipient=country/KEN), populationType=DevelopmentFinance,
    measuredProperty=DevelopmentFinanceFlow, statType=measuredValue.
    obs probe for (variable=ONE/CRS_DAC/Health-ODAGrants-KEN, entity=country/GBR)
    returned 432 observations spanning 1988-2024 across 18 facets -- fully live.
    country/GBR (UK) and country/KEN (Kenya) both confirmed as valid entity dcids.
    Seam-ON rendering: country/GBR is role_kind=directional direction=from, role_dcid=observationAbout (observation-sourced);
    country/KEN is role_kind=directional direction=to via DevelopmentFinanceRecipient constraint.
    Seam-OFF rendering: country/KEN becomes role_kind=subject (no directional),
    country/GBR becomes role_kind=subject (no directional); the offline seam replay asserts this role collapse.
    [conformance: named donor is a directional 'from' role sourced from observationAbout, symmetric with the recipient's directional 'to'; supersedes the earlier Rule-6 subject treatment per place-as-constraint-seam.md]

- id: "df-04"
  query: "health official development assistance from Germany to Ethiopia"
  entry_path: "raw_text"
  tags:
    - behaviour: definite
    - domain: development_finance
    - seam: both
    - conjunction: none
  expected_status: "definite"
  expected_shape:
    population_type_dcid: "DevelopmentFinance"
    measured_property_dcid: "DevelopmentFinanceFlow"
    stat_type_dcid: "measuredValue"
    measurement_qualifier_dcid: null
    measurement_denominator_dcid: null
  expected_slots:
    - axis: "what"
      property_dcid: "DevelopmentFinanceScheme"
      binding_kind: "value"
      value_dcid: "OfficialDevelopmentAssistance"
    - axis: "how"
      property_dcid: "DevelopmentFinancePurpose"
      binding_kind: "value"
      value_dcid: "DAC/Health"
    - axis: "where"
      property_dcid: "DevelopmentFinanceRecipient"
      binding_kind: "value"
      value_dcid: "country/ETH"
  expected_stat_vars: ["ONE/CRS_DAC/Health-OfficialDevelopmentAssistance-ETH"]
  expected_entities:
    - dcid: "country/DEU"
      role_kind: "directional"
      direction: "from"
      role_dcid: "observationAbout"
    - dcid: "country/ETH"
      role_kind: "directional"
      direction: "to"
      role_dcid: "DevelopmentFinanceRecipient"
  expected_no_data_reason: null
  candidate_count: null
  status: VERIFIED_AGAINST_GRAPH
  notes: |
    Probed ONE/CRS_DAC/Health-OfficialDevelopmentAssistance-ETH on staging: confirmed
    populationType=DevelopmentFinance, measuredProperty=DevelopmentFinanceFlow,
    statType=measuredValue, DevelopmentFinanceScheme=OfficialDevelopmentAssistance,
    DevelopmentFinancePurpose=DAC/Health, DevelopmentFinanceRecipient=country/ETH.
    Confirmed 372 observations (1997-2024) with country/DEU as observationAbout donor.
    DAC/Health confirmed as aggregate rollup node (name "Health (Total)", isPartOf parent
    for DAC/BasicHealth etc.); binding_kind=value on the how axis because DAC/Health is
    a real aggregate node, not a set. country/ETH as subject yields 0 observations,
    confirming ETH is the constraint (DevelopmentFinanceRecipient), not the donor.
    OfficialDevelopmentAssistance confirmed as aggregate scheme (grants+loans combined).
    Seam-OFF rendering: country/ETH becomes role_kind subject with no directional role;
    this would fail to find data as ETH is a constraint value, not observationAbout.
    Seam-ON rendering shown above: country/DEU is role_kind=directional direction=from, role_dcid=observationAbout (observation-sourced);
    country/ETH is directional to/DevelopmentFinanceRecipient constraint.
    [conformance: named donor is a directional 'from' role sourced from observationAbout, symmetric with the recipient's directional 'to'; supersedes the earlier Rule-6 subject treatment per place-as-constraint-seam.md]

- id: "df-05"
  query: "HIV/AIDS ODA grants from USA to Kenya"
  entry_path: "raw_text"
  tags:
    - behaviour: definite
    - domain: development_finance
    - seam: both
    - conjunction: none
  expected_status: "definite"
  expected_shape:
    population_type_dcid: "DevelopmentFinance"
    measured_property_dcid: "DevelopmentFinanceFlow"
    stat_type_dcid: "measuredValue"
    measurement_qualifier_dcid: null
    measurement_denominator_dcid: null
  expected_slots:
    - axis: "how"
      property_dcid: "DevelopmentFinancePurpose"
      binding_kind: "value"
      value_dcid: "DAC/STDcontrolincludingHIVAIDS"
    - axis: "what"
      property_dcid: "DevelopmentFinanceScheme"
      binding_kind: "value"
      value_dcid: "ODAGrants"
    - axis: "where"
      property_dcid: "DevelopmentFinanceRecipient"
      binding_kind: "value"
      value_dcid: "country/KEN"
  expected_stat_vars:
    - "ONE/CRS_DAC/STDcontrolincludingHIVAIDS-ODAGrants-KEN"
  expected_entities:
    - dcid: "country/USA"
      role_kind: "directional"
      direction: "from"
      role_dcid: "observationAbout"
    - dcid: "country/KEN"
      role_kind: "directional"
      direction: "to"
      role_dcid: "DevelopmentFinanceRecipient"
  expected_no_data_reason: null
  candidate_count: null
  status: VERIFIED_AGAINST_GRAPH
  notes: |
    Probed ONE/CRS_DAC/STDcontrolincludingHIVAIDS-ODAGrants-KEN on dc-staging.one.org:
    constraintProperties = [DevelopmentFinanceScheme=ODAGrants, DevelopmentFinancePurpose=DAC/STDcontrolincludingHIVAIDS, DevelopmentFinanceRecipient=country/KEN];
    populationType=DevelopmentFinance, measuredProperty=DevelopmentFinanceFlow, statType=measuredValue.
    Observation probe: country/USA as observationAbout -> 378 obs across 18 facets, 1995-2024.
    country/KEN confirmed (name=Kenya); country/USA confirmed (name=United States);
    ODAGrants confirmed (name=Official Development Assistance Grants);
    DAC/STDcontrolincludingHIVAIDS confirmed (name=STD control including HIV/AIDS).
    Seam-ON (rendered above): recipient country/KEN is directional role_kind with role_dcid=DevelopmentFinanceRecipient, donor country/USA is role_kind=directional direction=from, role_dcid=observationAbout (observation-sourced).
    Seam-OFF: same spec; country/KEN becomes role_kind=subject, direction=null, role_dcid=null (treated symmetrically as an observation subject alongside country/USA). The offline seam replay asserts this role collapse in both modes.
    [conformance: named donor is a directional 'from' role sourced from observationAbout, symmetric with the recipient's directional 'to'; supersedes the earlier Rule-6 subject treatment per place-as-constraint-seam.md]

- id: "df-13"
  query: "basic health ODA grants from France to Kenya"
  entry_path: "raw_text"
  tags:
    - behaviour: definite
    - domain: development_finance
    - seam: both
    - conjunction: none
  expected_status: "definite"
  expected_shape:
    population_type_dcid: "DevelopmentFinance"
    measured_property_dcid: "DevelopmentFinanceFlow"
    stat_type_dcid: "measuredValue"
    measurement_qualifier_dcid: null
    measurement_denominator_dcid: null
  expected_slots:
    - axis: "what"
      property_dcid: "DevelopmentFinanceScheme"
      binding_kind: "value"
      value_dcid: "ODAGrants"
    - axis: "how"
      property_dcid: "DevelopmentFinancePurpose"
      binding_kind: "value"
      value_dcid: "DAC/BasicHealth"
    - axis: "where"
      property_dcid: "DevelopmentFinanceRecipient"
      binding_kind: "value"
      value_dcid: "country/KEN"
  expected_stat_vars: ["ONE/CRS_DAC/BasicHealth-ODAGrants-KEN"]
  expected_entities:
    - dcid: "country/FRA"
      role_kind: "directional"
      direction: "from"
      role_dcid: "observationAbout"
    - dcid: "country/KEN"
      role_kind: "directional"
      direction: "to"
      role_dcid: "DevelopmentFinanceRecipient"
  expected_no_data_reason: null
  candidate_count: null
  status: VERIFIED_AGAINST_GRAPH
  notes: |
    Probed on staging. ONE/CRS_DAC/BasicHealth-ODAGrants-KEN exists with constraintProperties
    DevelopmentFinanceScheme=ODAGrants, DevelopmentFinancePurpose=DAC/BasicHealth,
    DevelopmentFinanceRecipient=country/KEN. obs(country/FRA) = 258 facets, date range 2000-2024.
    DAC/BasicHealth confirmed as DevelopmentFinancePurposeEnum (name "Basic Health (Semi-Aggregate)",
    isPartOf=DAC/Health). ODAGrants confirmed as a Property node (scheme). country/FRA and
    country/KEN both exist on staging.
    Seam-ON rendering: country/FRA is role_kind=directional direction=from, role_dcid=observationAbout (observation-sourced);
    country/KEN is role_kind=directional direction=to via DevelopmentFinanceRecipient constraint.
    Seam-OFF rendering: country/KEN becomes role_kind=subject (no directional role); country/FRA
    also becomes role_kind=subject -- the offline seam replay asserts this role collapse.
    [conformance: named donor is a directional 'from' role sourced from observationAbout, symmetric with the recipient's directional 'to'; supersedes the earlier Rule-6 subject treatment per place-as-constraint-seam.md]

- id: "df-06"
  query: "health ODA grants to Ethiopia"
  entry_path: "raw_text"
  tags:
    - behaviour: definite
    - domain: development_finance
    - seam: "on"
    - conjunction: none
  expected_status: "definite"
  expected_shape:
    population_type_dcid: "DevelopmentFinance"
    measured_property_dcid: "DevelopmentFinanceFlow"
    stat_type_dcid: "measuredValue"
    measurement_qualifier_dcid: null
    measurement_denominator_dcid: null
  expected_slots:
    - axis: "what"
      property_dcid: "DevelopmentFinanceScheme"
      binding_kind: "value"
      value_dcid: "ODAGrants"
    - axis: "how"
      property_dcid: "DevelopmentFinancePurpose"
      binding_kind: "value"
      value_dcid: "DAC/Health"
    - axis: "where"
      property_dcid: "DevelopmentFinanceRecipient"
      binding_kind: "value"
      value_dcid: "country/ETH"
  expected_stat_vars: ["ONE/CRS_DAC/Health-ODAGrants-ETH"]
  expected_entities:
    - dcid: "country/ETH"
      role_kind: "directional"
      direction: "to"
      role_dcid: "DevelopmentFinanceRecipient"
  expected_no_data_reason: null
  candidate_count: null
  status: VERIFIED_AGAINST_GRAPH
  notes: |
    Probed ONE/CRS_DAC/Health-ODAGrants-ETH on staging: confirmed populationType=DevelopmentFinance,
    measuredProperty=DevelopmentFinanceFlow, statType=measuredValue. Three constraint properties on
    SV: DevelopmentFinanceScheme=ODAGrants, DevelopmentFinancePurpose=DAC/Health,
    DevelopmentFinanceRecipient=country/ETH. Confirmed country/ETH exists (Ethiopia). Confirmed
    DAC/Health exists as a DevelopmentFinancePurposeEnum rollup node named "Health (Total)".
    Confirmed ODAGrants exists as a Property node. Confirmed 402 observations (1991-2024) at
    country/USA donor. No donor named in query; the donor (observationAbout subject) is open --
    all-donor aggregate or per-donor lookup. Recipient country/ETH is the directional "to"
    constraint (seam=on). Seam-OFF rendering: same spec, country/ETH becomes role_kind: subject
    with no direction or role_dcid.

- id: "df-07"
  query: "basic health ODA grants to Kenya"
  entry_path: "raw_text"
  tags:
    - behaviour: definite
    - domain: development_finance
    - seam: "on"
    - conjunction: none
  expected_status: "definite"
  expected_shape:
    population_type_dcid: "DevelopmentFinance"
    measured_property_dcid: "DevelopmentFinanceFlow"
    stat_type_dcid: "measuredValue"
    measurement_qualifier_dcid: null
    measurement_denominator_dcid: null
  expected_slots:
    - axis: "how"
      property_dcid: "DevelopmentFinancePurpose"
      binding_kind: "value"
      value_dcid: "DAC/BasicHealth"
    - axis: "what"
      property_dcid: "DevelopmentFinanceScheme"
      binding_kind: "value"
      value_dcid: "ODAGrants"
    - axis: "where"
      property_dcid: "DevelopmentFinanceRecipient"
      binding_kind: "value"
      value_dcid: "country/KEN"
  expected_stat_vars: ["ONE/CRS_DAC/BasicHealth-ODAGrants-KEN"]
  expected_entities:
    - dcid: "country/KEN"
      role_kind: "directional"
      direction: "to"
      role_dcid: "DevelopmentFinanceRecipient"
  expected_no_data_reason: null
  candidate_count: null
  status: VERIFIED_AGAINST_GRAPH
  notes: |
    Probed ONE/CRS_DAC/BasicHealth-ODAGrants-KEN on staging: DevelopmentFinancePurpose=DAC/BasicHealth
    (name "Basic Health (Semi-Aggregate)"), DevelopmentFinanceScheme=ODAGrants, DevelopmentFinanceRecipient=country/KEN.
    populationType=DevelopmentFinance, measuredProperty=DevelopmentFinanceFlow, statType=measuredValue confirmed.
    366 observations at country/USA donor (1980-2024); 0 at country/KEN-as-subject (expected: donor is subject,
    recipient is constraint). country/KEN verified as name "Kenya". DAC/BasicHealth verified as a real rollup node
    (CrsPurposeCode=12200). Seam-OFF rendering: country/KEN as role_kind=subject (no direction/role_dcid).

- id: "df-08"
  query: "reproductive health ODA grants to Ethiopia"
  entry_path: "raw_text"
  tags:
    - behaviour: definite
    - domain: development_finance
    - seam: "on"
    - conjunction: none
  expected_status: "definite"
  expected_shape:
    population_type_dcid: "DevelopmentFinance"
    measured_property_dcid: "DevelopmentFinanceFlow"
    stat_type_dcid: "measuredValue"
    measurement_qualifier_dcid: null
    measurement_denominator_dcid: null
  expected_slots:
    - axis: "what"
      property_dcid: "DevelopmentFinanceScheme"
      binding_kind: "value"
      value_dcid: "ODAGrants"
    - axis: "how"
      property_dcid: "DevelopmentFinancePurpose"
      binding_kind: "value"
      value_dcid: "DAC/Reproductivehealthcare"
    - axis: "where"
      property_dcid: "DevelopmentFinanceRecipient"
      binding_kind: "value"
      value_dcid: "country/ETH"
  expected_stat_vars:
    - "ONE/CRS_DAC/Reproductivehealthcare-ODAGrants-ETH"
  expected_entities:
    - dcid: "country/ETH"
      role_kind: "directional"
      direction: "to"
      role_dcid: "DevelopmentFinanceRecipient"
  expected_no_data_reason: null
  candidate_count: null
  status: VERIFIED_AGAINST_GRAPH
  notes: >
    Probed ONE/CRS_DAC/Reproductivehealthcare-ODAGrants-ETH on staging: node exists with
    DevelopmentFinancePurpose=DAC/Reproductivehealthcare, DevelopmentFinanceScheme=ODAGrants,
    DevelopmentFinanceRecipient=country/ETH. Observation check at country/USA (donor as subject)
    returned 318 obs, date range 1994-2024. country/ETH confirmed live. DAC/Reproductivehealthcare
    purpose node confirmed (CRS code 13020). ODAGrants scheme node confirmed.
    No named donor in query so no subject entity is specified; the recipient Ethiopia is the
    seam-facing entity (directional, role=to). Seam-OFF rendering: same spec, country/ETH
    becomes role_kind=subject with no direction or role_dcid (treated as the observationAbout
    entity rather than a constraint filter).
    [conformance: swapped DevelopmentFinanceScheme to axis 'what' and DevelopmentFinancePurpose to axis 'how']

- id: "df-09"
  query: "health aid to Kenya"
  entry_path: "raw_text"
  tags:
    - behaviour: definite
    - domain: development_finance
    - seam: "on"
    - conjunction: none
  expected_status: "definite"
  expected_shape:
    population_type_dcid: "DevelopmentFinance"
    measured_property_dcid: "DevelopmentFinanceFlow"
    stat_type_dcid: "measuredValue"
    measurement_qualifier_dcid: null
    measurement_denominator_dcid: null
  expected_slots:
    - axis: "how"
      property_dcid: "DevelopmentFinancePurpose"
      binding_kind: "value"
      value_dcid: "DAC/Health"
    - axis: "what"
      property_dcid: "DevelopmentFinanceScheme"
      binding_kind: "unbound"
      value_dcid: null
    - axis: "where"
      property_dcid: "DevelopmentFinanceRecipient"
      binding_kind: "value"
      value_dcid: "country/KEN"
  expected_stat_vars: []
  expected_entities:
    - dcid: "country/KEN"
      role_kind: "directional"
      direction: "to"
      role_dcid: "DevelopmentFinanceRecipient"
  expected_no_data_reason: null
  candidate_count: null
  status: VERIFIED_AGAINST_GRAPH
  notes: |
    Probed staging graph directly. All 7 Health-*-KEN SVs confirmed live:
    ONE/CRS_DAC/Health-ODAGrants-KEN (462 obs via country/USA donor, 1980-2024),
    ONE/CRS_DAC/Health-OfficialDevelopmentAssistance-KEN (462 obs),
    ONE/CRS_DAC/Health-ODALoans-KEN (6 obs, 1982),
    ONE/CRS_DAC/Health-ODAPrivateSectorInstruments-KEN (6 obs, 2023),
    ONE/CRS_DAC/Health-OtherOfficialFlows-KEN (0 obs country/USA but SV node exists),
    ONE/CRS_DAC/Health-PrivateDevelopmentFinance-KEN (0 obs country/USA but SV node exists),
    ONE/CRS_DAC/Health-ODAEquityInvestment-KEN (0 obs country/USA but SV node exists).
    DAC/Health purpose node confirmed (name "Health (Total)", typeOf DevelopmentFinancePurposeEnum).
    country/KEN confirmed (typeOf Country, name Kenya).
    Five-tuple probe: all 7 SVs share identical five-tuple
    (DevelopmentFinance, DevelopmentFinanceFlow, measuredValue, null, null); they differ
    only in DevelopmentFinanceScheme constraint value. This is same-shape openness, not
    competing shapes, so the engine produces ONE definite spec with the scheme slot unbound.
    No aggregate rollup SV covering all schemes exists (ONE/CRS_DAC/Health-KEN does not
    exist on staging). Query "health aid" leaves scheme entirely open -> binding_kind=unbound.
    The purpose (DAC/Health) and recipient (country/KEN directional "to") are definite.
    Seam-OFF rendering: country/KEN role_kind=subject, direction=null, role_dcid=null.
    [conformance: reclassified candidates->definite (same-shape openness); scheme slot
    already unbound; candidate_count set to null; expected_stat_vars cleared to []]

- id: "df-11"
  query: "health ODA per capita to Ethiopia"
  entry_path: "raw_text"
  tags:
    - behaviour: no_data
    - domain: development_finance
    - seam: na
    - conjunction: none
  expected_status: "no_data"
  expected_shape: null
  expected_slots: []
  expected_stat_vars: []
  expected_entities:
    - dcid: "country/ETH"
      role_kind: "subject"
      direction: null
      role_dcid: null
  expected_no_data_reason: "denominator_not_available"
  candidate_count: null
  status: VERIFIED_AGAINST_GRAPH
  notes: >
    Probed all 16464 CRS_DAC SVs in /tmp/qre_live/staging/svs.jsonl: zero SVs contain
    '-pc', 'PerCapita', or 'per_capita' in their dcid. Confirmed ONE/CRS_DAC/Health-ODAGrants-ETH
    (the nearest scalar match) has no measurementDenominator property on the live staging graph
    (probed ->* arcs: populationType=DevelopmentFinance, measuredProperty=DevelopmentFinanceFlow,
    statType=measuredValue, constraintProperties={DevelopmentFinanceScheme, DevelopmentFinancePurpose,
    DevelopmentFinanceRecipient}, no measurementDenominator). NL detect for the query resolves
    country/ETH and returns health ODA SVs (e.g. ONE/CRS_DAC/Health-ODALoans-Earth) but none
    with per-capita semantics. The CRS_DAC schema has no denominator variant; a per-capita
    dev-finance query cannot resolve to any SV -> no_data with reason denominator_not_available.
    Verified dcids: ONE/CRS_DAC/Health-ODAGrants-ETH (staging), country/ETH (staging),
    DAC/Health (staging, DevelopmentFinancePurposeEnum), ODAGrants (staging, Property).
    [conformance: seam=na requires role_kind=subject, direction=null, role_dcid=null;
    corrected entity from directional/to/DevelopmentFinanceRecipient to subject/null/null]

- id: "who-01"
  query: "external spending on preventive care"
  entry_path: "raw_text"
  tags:
    - behaviour: definite
    - domain: who_health
    - seam: na
    - conjunction: none
  expected_status: "definite"
  expected_shape:
    population_type_dcid: "currentHealthExpenditure"
    measured_property_dcid: null
    stat_type_dcid: "measuredValue"
    measurement_qualifier_dcid: null
    measurement_denominator_dcid: null
  expected_slots:
    - axis: "how"
      property_dcid: "healthCareFunction"
      binding_kind: "value"
      value_dcid: "HC_6"
    - axis: "how"
      property_dcid: "healthFinancingSource"
      binding_kind: "value"
      value_dcid: "ExternalHealthFinancing"
  expected_stat_vars: ["ONE/who_hc6-ext"]
  expected_entities: []
  expected_no_data_reason: null
  candidate_count: null
  status: VERIFIED_AGAINST_GRAPH
  notes: "Probed ONE/who_hc6-ext on prod. constraintProperties confirmed as [healthFinancingSource, healthCareFunction]. healthCareFunction=HC_6 (Preventive care HC.6) and healthFinancingSource=ExternalHealthFinancing both present on the SV. statType=measuredValue, populationType=currentHealthExpenditure. No measuredProperty arc present (WHO SVs legitimately have null measuredProperty). Observation check: 16 obs at country/KEN (2016-2019). HC_6 and ExternalHealthFinancing enum nodes confirmed live on prod. No entity in play; seam is na. Query uniquely specifies function=preventive + source=external -> definite two-property golden. [conformance: healthFinancingSource axis corrected from 'source' to 'how'; measured_property_dcid corrected from 'measuredValue' to null]"

- id: "who-02"
  query: "current health expenditure on medical goods"
  entry_path: "raw_text"
  tags:
    - behaviour: definite
    - domain: who_health
    - seam: na
    - conjunction: none
  expected_status: "definite"
  expected_shape:
    population_type_dcid: "currentHealthExpenditure"
    measured_property_dcid: null
    stat_type_dcid: "measuredValue"
    measurement_qualifier_dcid: null
    measurement_denominator_dcid: null
  expected_slots:
    - axis: "how"
      property_dcid: "healthCareFunction"
      binding_kind: "value"
      value_dcid: "HC_5"
    - axis: "how"
      property_dcid: "healthFinancingSource"
      binding_kind: "absent"
      value_dcid: null
  expected_stat_vars: ["ONE/who_hc5"]
  expected_entities: []
  expected_no_data_reason: null
  candidate_count: null
  status: VERIFIED_AGAINST_GRAPH
  notes: "Probed ONE/who_hc5 on prod: constraintProperties=[healthCareFunction], healthCareFunction=HC_5 (Medical goods HC.5), populationType=currentHealthExpenditure, statType=measuredValue. healthFinancingSource is ABSENT (property not present on this SV at all, not merely unbound). HC_5 value node confirmed (name: Medical goods (non-specified by function) (HC.5), typeOf: HealthCareFunctionEnum). Observations confirmed: 20 at country/ETH (2016-2020), 16 at country/KEN (2016-2019). Query specifies function=medical goods with no financing-source context, so healthFinancingSource binds ABSENT, resolving to the pure function SV rather than any composed SV. [conformance: healthFinancingSource axis corrected from 'source' to 'how']"

- id: "who-10"
  query: "external spending on curative care in Kenya"
  entry_path: "raw_text"
  tags:
    - behaviour: definite
    - domain: who_health
    - seam: na
    - conjunction: none
  expected_status: "definite"
  expected_shape:
    population_type_dcid: "currentHealthExpenditure"
    measured_property_dcid: null
    stat_type_dcid: "measuredValue"
    measurement_qualifier_dcid: null
    measurement_denominator_dcid: null
  expected_slots:
    - axis: "how"
      property_dcid: "healthCareFunction"
      binding_kind: "value"
      value_dcid: "HC_1"
    - axis: "how"
      property_dcid: "healthFinancingSource"
      binding_kind: "value"
      value_dcid: "ExternalHealthFinancing"
  expected_stat_vars: ["ONE/who_hc1-ext"]
  expected_entities:
    - dcid: "country/KEN"
      role_kind: "subject"
      direction: null
      role_dcid: null
  expected_no_data_reason: null
  candidate_count: null
  status: VERIFIED_AGAINST_GRAPH
  notes: "Probed ONE/who_hc1-ext on prod: constraintProperties confirmed as [healthFinancingSource, healthCareFunction], healthCareFunction=HC_1 (Curative care HC.1), healthFinancingSource=ExternalHealthFinancing. populationType=currentHealthExpenditure, statType=measuredValue, no measurementQualifier or measurementDenominator. Obs probe: 16 observations at country/KEN (2016-2019). Both constraint axes are bound to single values -> two-property definite. country/KEN verified live on prod. No seam: WHO health expenditure has no directional entity relationship. [conformance: axis 'what' corrected to 'how' on healthCareFunction slot; axis 'source' corrected to 'how' on healthFinancingSource slot per Rule 1 (all WHO constraint properties map to how axis).]"

- id: "who-05"
  query: "maternal mortality"
  entry_path: "raw_text"
  tags:
    - behaviour: candidates
    - domain: who_health
    - seam: na
    - conjunction: none
  expected_status: "candidates"
  expected_shape: null
  expected_slots: []
  expected_stat_vars:
    - "sdg/SH_STA_MORT.SEX--F"
    - "ONE/who_dis21"
    - "Count_Death_PregnancyChildbirthThePuerperium"
    - "ONE/CRS_DAC/Reproductivehealthcare-ODAGrants-Earth"
  expected_entities: []
  expected_no_data_reason: null
  candidate_count: 4
  status: VERIFIED_AGAINST_GRAPH
  notes: |
    Four distinct population-type shapes confirmed live on staging via /api/explore/detect (59 SVs total, 45 non-topic).
    Shape 1 -- SDG: populationType=SDG_SH_STA_MORT, measuredProperty=value, constraintProperties=[sdg_sex], value sdg_sex=SDG_SexEnum_F; 21 observations for country/KEN and country/ETH (2000-2020).
    Shape 2 -- WHO/SHA spending: populationType=currentHealthExpenditure, constraintProperties=[diseaseAndCondition], value diseaseAndCondition=DIS_2_1; name "Expenditure on maternal conditions"; 12 observations for country/KEN (2017-2019).
    Shape 3 -- Standard MortalityEvent: populationType=MortalityEvent, measuredProperty=count, constraintProperties=[causeOfDeath] (and gender for the female variant); cause = Pregnancy, Childbirth And The Puerperium (ICD O00-O99); no observations found for country/KEN on staging (0 facets).
    Shape 4 -- Dev-finance CRS_DAC: populationType=DevelopmentFinance, measuredProperty=DevelopmentFinanceFlow, constraintProperties=[DevelopmentFinanceScheme, DevelopmentFinancePurpose, DevelopmentFinanceRecipient]; purpose=DAC/Reproductivehealthcare. Reproductive health care includes "safe motherhood activities" per description.
    No single shape dominates; query engine cannot prefer one without disambiguation. expected_stat_vars lists one representative SV per shape. No entity supplied so seam is na.

- id: "who-06"
  query: "domestic health spending in Kenya"
  entry_path: "raw_text"
  tags:
    - behaviour: definite
    - domain: who_health
    - seam: na
    - conjunction: none
  expected_status: "definite"
  expected_shape:
    population_type_dcid: "currentHealthExpenditure"
    measured_property_dcid: null
    stat_type_dcid: "measuredValue"
    measurement_qualifier_dcid: null
    measurement_denominator_dcid: null
  expected_slots:
    - axis: "how"
      property_dcid: "healthFinancingSource"
      binding_kind: "set"
      value_dcid:
        - "DomesticPrivateHealthFinancing"
        - "DomesticGeneralGovernmentHealthFinancing"
  expected_stat_vars:
    - "ONE/who_che-pvtd"
    - "ONE/who_che-gghed"
  expected_entities:
    - dcid: "country/KEN"
      role_kind: "subject"
      direction: null
      role_dcid: null
  expected_no_data_reason: null
  candidate_count: null
  status: VERIFIED_AGAINST_GRAPH
  notes: "Probed prod. ONE/who_che-pvtd (healthFinancingSource=DomesticPrivateHealthFinancing, 96 obs at country/KEN 2000-2023) and ONE/who_che-gghed (healthFinancingSource=DomesticGeneralGovernmentHealthFinancing, 96 obs at country/KEN 2000-2023) both confirmed live. No aggregate domestic SV exists: ONE/who_che-dom, ONE/who_dom, ONE/who_dshf all return no arcs. DomesticPrivateHealthFinancing and DomesticGeneralGovernmentHealthFinancing have no parent rollup node in HealthFinancingSourceEnum (no specialization arc, no memberOf hierarchy). Both SVs share the same five-tuple (populationType=currentHealthExpenditure, measuredProperty=null, statType=measuredValue, measurementQualifier=null, measurementDenominator=null) and differ only in healthFinancingSource constraint value -- same-shape openness, not genuine candidates. No rollup SV exists for the 'domestic' aggregate, so the slot binds as a set of two children. country/KEN confirmed via prod name arc (Kenya). seam=na: WHO health expenditure, single subject entity, no donor/recipient directionality. [conformance: reclassified from candidates to definite per Rule 2 (same five-tuple, differ only in constraint value, no rollup node -> set binding); axis 'source' corrected to 'how' on healthFinancingSource slot per Rule 1; candidate_count set to null; expected_shape populated with shared five-tuple.]"

- id: "who-07"
  query: "government health spending in Kenya"
  entry_path: "raw_text"
  tags:
    - behaviour: candidates
    - domain: who_health
    - seam: na
    - conjunction: none
  expected_status: "candidates"
  expected_shape: null
  expected_slots: []
  expected_stat_vars:
    - "ONE/who_hf11"
    - "ONE/who_hf11-pc"
    - "ONE/who_hf11-che"
    - "ONE/who_hp71"
    - "ONE/who_che-gghed"
  expected_entities:
    - dcid: "country/KEN"
      role_kind: "subject"
      direction: null
      role_dcid: null
  expected_no_data_reason: null
  candidate_count: 5
  status: VERIFIED_AGAINST_GRAPH
  notes: |
    Probed all five candidate SVs on prod (datacommons.one.org); all confirmed live with observations
    for country/KEN (Kenya). Five distinct shapes compete:
      (1) ONE/who_hf11 -- currentHealthExpenditure, healthFinancingScheme=HF_1_1, md=null (absolute; 96 obs 2000-2023)
      (2) ONE/who_hf11-pc -- same + md=Count_Person (per-capita; 120 obs 2000-2023)
      (3) ONE/who_hf11-che -- same + md=ONE/who_che (% of CHE; 24 obs 2000-2023)
      (4) ONE/who_hp71 -- currentHealthExpenditure, healthCareProvider=HP_7_1, md=null (provider axis; 16 obs 2016-2019)
      (5) ONE/who_che-gghed -- currentHealthExpenditure, healthFinancingSource=DomesticGeneralGovernmentHealthFinancing, md=null (source axis; 96 obs 2000-2023)
    Shapes (1-3) bind the healthFinancingScheme axis; shape (4) binds a different axis (healthCareProvider);
    shape (5) binds yet another axis (healthFinancingSource). All use statType=measuredValue.
    NL detect (staging) returns 10+ SVs for this query, confirming deep ambiguity.
    No seam involved -- entity is subject-only (Kenya as the observation-about country, not a directional flow endpoint).
    expected_shape is null because no single shape wins; expected_slots empty for same reason.

- id: "who-08"
  query: "external health spending on preventive care in USA"
  entry_path: "raw_text"
  tags:
    - behaviour: no_data
    - domain: who_health
    - seam: na
    - conjunction: none
  expected_status: "no_data"
  expected_shape:
    population_type_dcid: "currentHealthExpenditure"
    measured_property_dcid: null
    stat_type_dcid: "measuredValue"
    measurement_qualifier_dcid: null
    measurement_denominator_dcid: null
  expected_slots:
    - axis: "how"
      property_dcid: "healthCareFunction"
      binding_kind: "value"
      value_dcid: "HC_6"
    - axis: "how"
      property_dcid: "healthFinancingSource"
      binding_kind: "value"
      value_dcid: "ExternalHealthFinancing"
  expected_stat_vars: ["ONE/who_hc6-ext"]
  expected_entities:
    - dcid: "country/USA"
      role_kind: "subject"
      direction: null
      role_dcid: null
  expected_no_data_reason: "no_observations"
  candidate_count: null
  status: VERIFIED_AGAINST_GRAPH
  notes: "Probed ONE/who_hc6-ext on both prod and staging: SV exists with constraintProperties=[healthCareFunction=HC_6, healthFinancingSource=ExternalHealthFinancing], populationType=currentHealthExpenditure, statType=measuredValue. Observation probe country/USA on prod returned 0 facets, 0 observations (date_range [null, null]). Same result on staging. High-income countries do not report external health financing for preventive care in WHO SHA accounts. Entity country/USA confirmed as Country on prod. [conformance: axis 'what' corrected to 'how' on healthCareFunction slot; axis 'source' corrected to 'how' on healthFinancingSource slot per Rule 1 (all WHO constraint properties map to how axis).]"

- id: "who-11"
  query: "curative care health expenditure in Nauru"
  entry_path: "raw_text"
  tags:
    - behaviour: no_data
    - domain: who_health
    - seam: na
    - conjunction: none
  expected_status: "no_data"
  expected_shape:
    population_type_dcid: "currentHealthExpenditure"
    measured_property_dcid: null
    stat_type_dcid: "measuredValue"
    measurement_qualifier_dcid: null
    measurement_denominator_dcid: null
  expected_slots:
    - axis: "how"
      property_dcid: "healthCareFunction"
      binding_kind: "value"
      value_dcid: "HC_1"
    - axis: "how"
      property_dcid: "healthFinancingSource"
      binding_kind: "absent"
      value_dcid: null
  expected_stat_vars: ["ONE/who_hc1"]
  expected_entities:
    - dcid: "country/NRU"
      role_kind: "subject"
      direction: null
      role_dcid: null
  expected_no_data_reason: "no_observations"
  candidate_count: null
  status: VERIFIED_AGAINST_GRAPH
  notes: "Probed ONE/who_hc1 on prod: populationType=currentHealthExpenditure, statType=measuredValue, single constraint healthCareFunction=HC_1, no healthFinancingSource property present (absent binding). Observation probe ONE/who_hc1 @ country/NRU = 0 facets, 0 observations. country/NRU resolves to 'Nauru' (entity confirmed in graph). Cross-check: ONE/who_hf11 @ country/NRU = 96 observations across 4 facets (2000-2023), confirming Nauru has WHO health data but not WHO SHA function-level curative care (HC.1) series. [conformance: axis 'what' corrected to 'how' on healthCareFunction slot; axis 'source' corrected to 'how' on healthFinancingSource slot per Rule 1 (all WHO constraint properties map to how axis).]"

- id: "sdg-01"
  query: "maternal mortality ratio female SDG"
  entry_path: "raw_text"
  tags:
    - behaviour: definite
    - domain: sdg
    - seam: na
    - conjunction: none
  expected_status: "definite"
  expected_shape:
    population_type_dcid: "SDG_SH_STA_MORT"
    measured_property_dcid: "value"
    stat_type_dcid: "measuredValue"
    measurement_qualifier_dcid: null
    measurement_denominator_dcid: null
  expected_slots:
    - axis: "what"
      property_dcid: "sdg_sex"
      binding_kind: "value"
      value_dcid: "SDG_SexEnum_F"
  expected_stat_vars: ["sdg/SH_STA_MORT.SEX--F"]
  expected_entities: []
  expected_no_data_reason: null
  candidate_count: null
  status: VERIFIED_AGAINST_GRAPH
  notes: "Node probe on prod: sdg/SH_STA_MORT.SEX--F has populationType=SDG_SH_STA_MORT, constraintProperties=[sdg_sex], sdg_sex=SDG_SexEnum_F (Female). Inverse memberOf probe on group dc/g/SDG_3.1.1 confirmed this is the only SV in the group -- there is no male or both-sex sibling. Observation check: 21 obs at country/ETH (2000-2020) and 21 obs at country/USA (2000-2020). Single SV + explicit 'female' filter + 'SDG' context -> definite."

- id: "sdg-03"
  query: "tuberculosis incidence rate SDG 3.3.2 Ethiopia"
  entry_path: "raw_text"
  tags:
    - behaviour: definite
    - domain: sdg
    - seam: na
    - conjunction: none
  expected_status: "definite"
  expected_shape:
    population_type_dcid: "SDG_SH_TBS_INCD"
    measured_property_dcid: "value"
    stat_type_dcid: "measuredValue"
    measurement_qualifier_dcid: null
    measurement_denominator_dcid: null
  expected_slots: []
  expected_stat_vars: ["sdg/SH_TBS_INCD"]
  expected_entities:
    - dcid: "country/ETH"
      role_kind: "subject"
      direction: null
      role_dcid: null
  expected_no_data_reason: null
  candidate_count: null
  status: VERIFIED_AGAINST_GRAPH
  notes: >
    Probed prod. sdg/SH_TBS_INCD has populationType=SDG_SH_TBS_INCD,
    measuredProperty=value, statType=measuredValue, no constraintProperties
    (the arcs object has no constraintProperties key at all). memberOf
    dc/g/SDG_3.3.2 confirms indicator 3.3.2 alignment. 23 observations
    confirmed at country/ETH over 2000-2022 (1 facet). country/ETH resolves
    to Ethiopia on prod. No other SDG TB incidence SV with any constraint
    dimension exists in the prod taxonomy (6002 SVs cached), so there are
    no competing shapes and the query resolves definitively. No entity seam
    applies because this is a standard SDG entity (not a development-finance
    recipient), hence seam=na.

- id: "sdg-04"
  query: "HIV incidence rate Kenya"
  entry_path: "raw_text"
  tags:
    - behaviour: candidates
    - domain: sdg
    - seam: na
    - conjunction: none
  expected_status: "candidates"
  expected_shape: null
  expected_slots:
    - axis: "what"
      property_dcid: "sdg_sex"
      binding_kind: "unbound"
      value_dcid: null
  expected_stat_vars:
    - "sdg/SH_HIV_INCD.SEX--F"
    - "sdg/SH_HIV_INCD.SEX--M"
    - "sdg/SH_HIV_INCD"
    - "Count_MedicalConditionIncident_ConditionHIVAIDS"
    - "ONE/who_dis11-che"
    - "ONE/who_dis11-gdp"
  expected_entities:
    - dcid: "country/KEN"
      role_kind: "subject"
      direction: null
      role_dcid: null
  expected_no_data_reason: null
  candidate_count: 6
  status: VERIFIED_AGAINST_GRAPH
  notes: >
    Probed staging and prod. Three SDG shapes compete:
    sdg/SH_HIV_INCD.SEX--F (sdg_sex=SDG_SexEnum_F, 23 obs @ country/KEN, 2000-2022),
    sdg/SH_HIV_INCD.SEX--M (sdg_sex=SDG_SexEnum_M, 23 obs @ country/KEN, 2000-2022),
    sdg/SH_HIV_INCD (no constraintProperties, aggregate, 23 obs @ country/KEN, 2000-2022).
    All three share populationType=SDG_SH_HIV_INCD (an SDG_Series), measuredProperty=value, statType=measuredValue.
    The query specifies no sex -> sdg_sex slot is unbound across the SDG family, producing three competing specs.
    Two additional shapes from the NL detect: Count_MedicalConditionIncident_ConditionHIVAIDS
    (populationType=MedicalConditionIncident, measuredProperty=count, medicalCondition=HIV_AIDS; SV exists on prod
    but 0 observations at country/KEN), and ONE/who_dis11-che / ONE/who_dis11-gdp (WHO expenditure on HIV/AIDS
    as % CHE and % GDP respectively, populationType=currentHealthExpenditure, diseaseAndCondition=DIS_1_1,
    3 obs each @ country/KEN 2017-2019 on staging). Expected_slots shows the sdg_sex axis as unbound because
    the query does not specify sex; the three SDG SVs span the full sex dimension. expected_shape is null because
    no single shape is authoritative across the six candidates.
    Five-tuple probed 2026-06-20: SEX--F and SEX--M and SH_HIV_INCD all share (SDG_SH_HIV_INCD, value, measuredValue, null, null);
    Count_MedicalConditionIncident_ConditionHIVAIDS is (MedicalConditionIncident, count, measuredValue, null, null);
    ONE/who_dis11-che is (currentHealthExpenditure, null, measuredValue, null, ONE/who_che);
    ONE/who_dis11-gdp is (currentHealthExpenditure, null, measuredValue, null, Amount_EconomicActivity_GrossDomesticProduction_Nominal).
    Four distinct five-tuples across six candidates -> legit candidates, not same-shape openness.

- id: "sdg-06"
  query: "government education spending Kenya"
  entry_path: "raw_text"
  tags:
    - behaviour: candidates
    - domain: standard
    - seam: na
    - conjunction: none
  expected_status: "candidates"
  expected_shape: null
  expected_slots:
    - axis: "where"
      property_dcid: null
      binding_kind: "value"
      value_dcid: "country/KEN"
  expected_stat_vars:
    - "sdg/SG_XPD_EDUC"
    - "Amount_EconomicActivity_ExpenditureActivity_EducationExpenditure_Government_AsFractionOf_Amount_EconomicActivity_ExpenditureActivity_Government"
    - "Amount_EconomicActivity_ExpenditureActivity_EducationExpenditure_Government_AsFractionOf_Amount_EconomicActivity_GrossDomesticProduction_Nominal"
    - "Amount_EconomicActivity_ExpenditureActivity_TertiaryEducationExpenditure_Government_AsFractionOf_Amount_EconomicActivity_ExpenditureActivity_EducationExpenditure_Government"
  expected_entities:
    - dcid: "country/KEN"
      role_kind: "subject"
      direction: null
      role_dcid: null
  expected_no_data_reason: null
  candidate_count: 4
  status: VERIFIED_AGAINST_GRAPH
  notes: |
    Probed prod. Four distinct SV shapes all have observations at country/KEN:
    (1) sdg/SG_XPD_EDUC -- SDG indicator, populationType=SDG_SG_XPD_EDUC, mp=value, no constraint properties, 19 obs 2000-2021.
    (2) Amount_EconomicActivity_ExpenditureActivity_EducationExpenditure_Government_AsFractionOf_Amount_EconomicActivity_ExpenditureActivity_Government -- % of total gov expenditure, EconomicActivity/amount/measuredValue, constraintProperties=[activitySource,expenditureType,expensor], md=Amount_EconomicActivity_ExpenditureActivity_Government, 35 obs 1982-2025.
    (3) Amount_EconomicActivity_ExpenditureActivity_EducationExpenditure_Government_AsFractionOf_Amount_EconomicActivity_GrossDomesticProduction_Nominal -- same base shape as (2) but md=Amount_EconomicActivity_GrossDomesticProduction_Nominal (% of GDP), 43 obs 1971-2024.
    (4) Amount_EconomicActivity_ExpenditureActivity_TertiaryEducationExpenditure_Government_AsFractionOf_Amount_EconomicActivity_ExpenditureActivity_EducationExpenditure_Government -- tertiary edu as fraction of all edu spending, constraintProperties=[activitySource,expenditureType,remunerator], md=Amount_EconomicActivity_ExpenditureActivity_EducationExpenditure_Government, 31 obs 1971-2015.
    Candidates split on: (a) denominator -- total gov spending vs GDP vs edu spending; (b) education level -- all vs tertiary; (c) taxonomy -- standard EconomicActivity shape vs SDG series shape.
    The menu's "6 distinct shapes" count includes two topic nodes (dc/topic/EducationExpenditure, dc/topic/sdg_1.a.2) returned by detect but not actionable SVs. Raw absolute SV Amount_EconomicActivity_ExpenditureActivity_EducationExpenditure_Government exists but has zero observations at Kenya. WHO hc61 variants (IEC programmes) are health-system education/counseling -- a different concept, excluded.
    country/KEN verified as Kenya (Country node). No seam: query is purely domestic/fiscal, no donor-recipient flow.
    Five-tuple probed 2026-06-20: sdg/SG_XPD_EDUC=(SDG_SG_XPD_EDUC,value,measuredValue,null,null); SVs 2-4 all have pt=EconomicActivity, mp=amount, st=measuredValue, mq=null but differ on measurementDenominator -- four distinct five-tuples across the candidate set -> legit candidates.

- id: "std-01"
  query: "total population India"
  entry_path: "raw_text"
  tags:
    - behaviour: definite
    - domain: standard
    - seam: na
    - conjunction: none
  expected_status: "definite"
  expected_shape:
    population_type_dcid: "Person"
    measured_property_dcid: "count"
    stat_type_dcid: "measuredValue"
    measurement_qualifier_dcid: null
    measurement_denominator_dcid: null
  expected_slots: []
  expected_stat_vars: ["Count_Person"]
  expected_entities:
    - dcid: "country/IND"
      role_kind: "subject"
      direction: null
      role_dcid: null
  expected_no_data_reason: null
  candidate_count: null
  status: VERIFIED_AGAINST_GRAPH
  notes: "Probed Count_Person on prod: populationType=Person, measuredProperty=count, statType=measuredValue, no constraintProperties (pure count SV). Observation probe: 181 obs at country/IND, date range 1960-2026 (6 facets). Node probe on country/IND: name=India, typeOf=Country. Query is unambiguous -- 'total population' maps to Count_Person with no competing shapes. India is a subject place: it binds as a subject entity, not a where-axis slot (where-slots carry place-as-constraint bindings only, e.g. DevelopmentFinanceRecipient); expected_slots is therefore empty."

- id: "std-02"
  query: "GDP India"
  entry_path: "raw_text"
  tags:
    - behaviour: candidates
    - domain: standard
    - seam: na
    - conjunction: none
  expected_status: "candidates"
  expected_shape: null
  expected_slots:
    - axis: "where"
      property_dcid: null
      binding_kind: "value"
      value_dcid: "country/IND"
  expected_stat_vars:
    - "Amount_EconomicActivity_GrossDomesticProduction_Nominal"
    - "Amount_EconomicActivity_GrossDomesticProduction_Nominal_PerCapita"
    - "GrowthRate_Amount_EconomicActivity_GrossDomesticProduction"
    - "Amount_EconomicActivity_GrossDomesticProduction_RealValue"
    - "ONE/who_gge-gdp"
  expected_entities:
    - dcid: "country/IND"
      role_kind: "subject"
      direction: null
      role_dcid: null
  expected_no_data_reason: null
  candidate_count: 5
  status: VERIFIED_AGAINST_GRAPH
  notes: |
    Probed prod. Five distinct shapes all have live observations at country/IND:
    (1) Amount_EconomicActivity_GrossDomesticProduction_Nominal: 109 obs 1960-2026,
        populationType=EconomicActivity, measuredProperty=amount, statType=measuredValue,
        measurementQualifier=Nominal, constraint activitySource=GrossDomesticProduction.
    (2) Amount_EconomicActivity_GrossDomesticProduction_Nominal_PerCapita: 65 obs 1960-2024,
        same as (1) plus measurementDenominator=PerCapita.
    (3) GrowthRate_Amount_EconomicActivity_GrossDomesticProduction: 64 obs 1961-2024,
        statType=growthRate (not measuredValue) -- structurally distinct shape.
    (4) Amount_EconomicActivity_GrossDomesticProduction_RealValue: 44 obs 2005-2026,
        measurementQualifier=RealValue (vs Nominal in shape 1).
    (5) ONE/who_gge-gdp: 24 obs 2000-2023, populationType=EconomicActivity,
        statType=measuredValue, measurementDenominator=Amount_EconomicActivity_GrossDomesticProduction_Nominal,
        no measuredProperty, no constraint properties -- a ratio SV (government expenditure % of GDP).
    NAICS industry breakdown SVs (e.g. Amount_EconomicActivity_GrossDomesticProduction_NAICSFinanceInsurance_RealValue)
    return 0 observations at country/IND (US-state-level data only) -- excluded from candidates.
    InflationAdjustedGDP also returns 0 at country/IND -- excluded.
    country/IND confirmed live (name=India). NL detect returns all 5 shapes among top results.
    expected_shape is null because no single shape is pinnable; the where slot (country/IND) is the
    only unambiguous binding. The "what" axis -- nominal vs real, absolute vs per-capita, level vs
    growth rate, GDP vs expenditure-ratio -- is the source of ambiguity.
    Five-tuple probed 2026-06-20: (1)=(EconomicActivity,amount,measuredValue,Nominal,null); (2)=(EconomicActivity,amount,measuredValue,Nominal,PerCapita); (3)=(EconomicActivity,amount,growthRate,null,null); (4)=(EconomicActivity,amount,measuredValue,RealValue,null); (5)=(EconomicActivity,null,measuredValue,null,Amount_EconomicActivity_GrossDomesticProduction_Nominal). Five distinct five-tuples -> legit candidates.

- id: "std-03"
  query: "birth rate Ethiopia"
  entry_path: "raw_text"
  tags:
    - behaviour: candidates
    - domain: standard
    - seam: na
    - conjunction: none
  expected_status: "candidates"
  expected_shape: null
  expected_slots:
    - axis: "where"
      property_dcid: null
      binding_kind: "value"
      value_dcid: "country/ETH"
  expected_stat_vars:
    - "Count_BirthEvent_LiveBirth_AsFractionOf_Count_Person"
    - "FertilityRate_Person_Female"
  expected_entities:
    - dcid: "country/ETH"
      role_kind: "subject"
      direction: null
      role_dcid: null
  expected_no_data_reason: null
  candidate_count: 2
  status: VERIFIED_AGAINST_GRAPH
  notes: >
    Probed prod for all four SVs mentioned in the menu row grounding.
    Count_BirthEvent_LiveBirth_AsFractionOf_Count_Person: BirthEvent/count/measuredValue,
    measurementDenominator=Count_Person, constraintProperties=[medicalStatus], medicalStatus=LiveBirth;
    64 obs at country/ETH (1960-2023). Confirmed live.
    FertilityRate_Person_Female: Person/fertilityRate/measuredValue, constraintProperties=[gender],
    gender=Female; 64 obs at country/ETH (1960-2023). Confirmed live.
    Count_BirthEvent_AsAFractionOfCount_Person: 0 obs at country/ETH -- not a viable candidate.
    Count_BirthEvent: 0 obs at country/ETH -- not a viable candidate.
    country/ETH resolves to Ethiopia on prod.
    The two live candidates have wholly distinct shapes (different populationType and measuredProperty),
    so the query is genuinely ambiguous: crude birth rate (BirthEvent denominator-split family) vs
    total fertility rate (Person/fertilityRate). candidate_count=2.
    [conformance: replaced invalid axis='what'/property_dcid=null/binding_kind='unbound' slot with
    the entity 'where' slot (axis='where', property_dcid=null, binding_kind='value',
    value_dcid='country/ETH'); null property_dcid is reserved for the entity slot only.]

- id: "std-05"
  query: "under-5 child mortality rate"
  entry_path: "raw_text"
  tags:
    - behaviour: candidates
    - domain: standard
    - seam: na
    - conjunction: none
  expected_status: "candidates"
  expected_shape: null
  expected_slots:
    - axis: "what"
      property_dcid: "age"
      binding_kind: "value"
      value_dcid: "YearsUpto4"
  expected_stat_vars:
    - "MortalityRate_Person_Upto4Years_AsFractionOf_Count_BirthEvent_LiveBirth"
    - "Count_Death_0Years_AsFractionOf_Count_BirthEvent_LiveBirth"
    - "sdg/SH_DYN_MORT.AGE--Y0T4"
    - "worldBank/SH_DYN_MORT"
    - "WHO/CM_01"
  expected_entities: []
  expected_no_data_reason: null
  candidate_count: 5
  status: VERIFIED_AGAINST_GRAPH
  notes: |
    Five structurally distinct shapes confirmed live against prod (datacommons.one.org).
    Shape 1 (IGME standard rate): Person/mortalityRate/md=Count_BirthEvent_LiveBirth/age=YearsUpto4
      -> MortalityRate_Person_Upto4Years_AsFractionOf_Count_BirthEvent_LiveBirth; 58 obs ETH 1966-2023, 64 obs KEN 1960-2023.
    Shape 2 (infant mortality rate, age 0 only): MortalityEvent/count/md=Count_BirthEvent_LiveBirth/age=Years0
      -> Count_Death_0Years_AsFractionOf_Count_BirthEvent_LiveBirth; 58 obs ETH, 64 obs KEN.
    Shape 3 (SDG 3.2.1 under-5): SDG_SH_DYN_MORT/value/age=SDG_AgeEnum_Y0T4
      -> sdg/SH_DYN_MORT.AGE--Y0T4; 23 obs ETH 2000-2022, 23 obs KEN.
    Shape 4 (World Bank under-5 rate, no age constraint): MortalityEvent/worldBank/SH_DYN_MORT/no constraints
      -> worldBank/SH_DYN_MORT; 58 obs ETH 1966-2023, 64 obs KEN 1960-2023. Name: "Mortality rate, under-5 (per 1,000 live births)".
    Shape 5 (WHO count, not rate): Person/who/CM_01/no constraints
      -> WHO/CM_01; 0 observations at ETH, KEN, USA. Name: "Number Of Under-Five Deaths" (count SV, not a rate).
    NL detect on staging returns all five shapes in top results. Ambiguity arises from: (a) age scope - under-5 (Upto4Years) vs infant-only (Years0); (b) taxonomy provenance - IGME/standard vs SDG vs World Bank vs WHO; (c) measure type - rate vs count (WHO/CM_01).
    No seam involvement: no place entity in query.
    [conformance: removed non-null expected_shape (was set to Shape 1 as 'most semantically precise');
    Rule 2 requires expected_shape=null when candidate members span different five-tuples. All five
    SVs confirmed to have distinct five-tuples on prod.]

- id: "who-04b"
  query: "curative care health expenditure in Kenya"
  entry_path: "raw_text"
  tags:
    - behaviour: definite
    - domain: who_health
    - seam: na
    - conjunction: none
  expected_status: "definite"
  expected_shape:
    population_type_dcid: "currentHealthExpenditure"
    measured_property_dcid: null
    stat_type_dcid: "measuredValue"
    measurement_qualifier_dcid: null
    measurement_denominator_dcid: null
  expected_slots:
    - axis: "how"
      property_dcid: "healthCareFunction"
      binding_kind: "value"
      value_dcid: "HC_1"
    - axis: "how"
      property_dcid: "healthFinancingSource"
      binding_kind: "absent"
      value_dcid: null
  expected_stat_vars: ["ONE/who_hc1"]
  expected_entities:
    - dcid: "country/KEN"
      role_kind: "subject"
      direction: null
      role_dcid: null
  expected_no_data_reason: null
  candidate_count: null
  status: VERIFIED_AGAINST_GRAPH
  notes: >
    ONE/who_hc1 confirmed on prod: populationType=currentHealthExpenditure, statType=measuredValue,
    no measuredProperty arc (WHO SVs lack it). Single constraintProperties=healthCareFunction=HC_1
    (curative care). healthFinancingSource is absent (not a constraint on this SV), so the source slot
    binds absent and resolves to the pure-function SV. country/KEN resolves as a Country node with 16
    observations across 4 facets (2016-2019); it is the observationAbout subject, not a slot.
    [conformance: corrected axis for healthCareFunction from 'what' to 'how', and axis for
    healthFinancingSource from 'source' to 'how'. Rule 1: WHO constraint properties healthCareFunction
    and healthFinancingSource must both use axis 'how'; neither 'what' nor 'source' is permitted
    for node-level constraint properties.]

- id: "sdg-05b"
  query: "poverty headcount ratio below national poverty line in Ethiopia"
  entry_path: "raw_text"
  tags:
    - behaviour: definite
    - domain: sdg
    - seam: na
    - conjunction: none
  expected_status: "definite"
  expected_shape:
    population_type_dcid: "SDG_SI_POV_NAHC"
    measured_property_dcid: "value"
    stat_type_dcid: "measuredValue"
    measurement_qualifier_dcid: null
    measurement_denominator_dcid: null
  expected_slots:
    - axis: "where"
      property_dcid: null
      binding_kind: "value"
      value_dcid: "country/ETH"
    - axis: "what"
      property_dcid: "sdg_urbanisation"
      binding_kind: "absent"
      value_dcid: null
  expected_stat_vars:
    - "sdg/SI_POV_NAHC"
  expected_entities:
    - dcid: "country/ETH"
      role_kind: "subject"
      direction: null
      role_dcid: null
  expected_no_data_reason: null
  candidate_count: null
  status: VERIFIED_AGAINST_GRAPH
  notes: >
    Two SVs in the NAHC peer group on prod and staging. sdg/SI_POV_NAHC (base, no constraint) has 3
    observations for country/ETH (2004, 2015). sdg/SI_POV_NAHC.URBANISATION--U
    (sdg_urbanisation=SDG_UrbanisationEnum_U) has zero observations for Ethiopia. SDG NAHC SVs use
    populationType=SDG_SI_POV_NAHC and carry NO povertyStatus constraint; BelowPovertyLevelInThePast12Months
    is a real node (USC_PovertyStatusEnum) used only in US Census SVs, and "BelowPoverty" does not exist as
    a graph node. NL detect for this query surfaces sdg/SI_POV_DAY1 (international line) as top match, not
    the national-line SVs - the national-vs-international ambiguity this golden exercises.
    Probed on prod: sdg/SI_POV_NAHC has no constraintProperties (unconstrained all-areas aggregate);
    sdg/SI_POV_NAHC.URBANISATION--U has constraintProperties=[sdg_urbanisation]=SDG_UrbanisationEnum_U.
    Both share five-tuple SDG_SI_POV_NAHC/value/measuredValue/null/null.
    [conformance: reclassified from candidates to definite (same-shape openness: both SVs share the
    same five-tuple, diverging only on sdg_urbanisation constraint). Resolved to sdg/SI_POV_NAHC
    (the unconstrained aggregate); sdg_urbanisation binds 'absent' since the resolved SV has no
    urbanisation constraint. expected_shape filled with shared five-tuple. candidate_count set to null.]

- id: "std-04b"
  query: "number of births in Ethiopia"
  entry_path: "raw_text"
  tags:
    - behaviour: candidates
    - domain: standard
    - seam: na
    - conjunction: none
  expected_status: "candidates"
  expected_shape: null
  expected_slots:
    - axis: "where"
      property_dcid: null
      binding_kind: "value"
      value_dcid: "country/ETH"
  expected_stat_vars:
    - "Count_BirthEvent"
    - "Count_BirthEvent_AsAFractionOfCount_Person"
    - "Count_BirthEvent_LiveBirth_AsFractionOf_Count_Person"
  expected_entities:
    - dcid: "country/ETH"
      role_kind: "subject"
      direction: null
      role_dcid: null
  expected_no_data_reason: null
  candidate_count: 3
  status: VERIFIED_AGAINST_GRAPH
  notes: >
    All three SVs resolve on prod (NL detect cosine: Count_BirthEvent=1.00,
    Count_BirthEvent_AsAFractionOfCount_Person=0.88, Count_BirthEvent_LiveBirth_AsFractionOf_Count_Person=0.75).
    Distinct shapes: a raw count, a fraction-of-population, and a live-birth fraction (adds
    medicalStatus=LiveBirth). At country/ETH only the live-birth fraction SV has observations (64,
    1960-2023, World Bank crude birth rate); the other two resolve as candidates with no data at this entity.

- id: "std-06b"
  query: "fertility rate in Kenya"
  entry_path: "raw_text"
  tags:
    - behaviour: definite
    - domain: standard
    - seam: na
    - conjunction: none
  expected_status: "definite"
  expected_shape:
    population_type_dcid: "Person"
    measured_property_dcid: "fertilityRate"
    stat_type_dcid: "measuredValue"
    measurement_qualifier_dcid: null
    measurement_denominator_dcid: null
  expected_slots:
    - axis: "how"
      property_dcid: "gender"
      binding_kind: "value"
      value_dcid: "Female"
  expected_stat_vars:
    - "FertilityRate_Person_Female"
  expected_entities:
    - dcid: "country/KEN"
      role_kind: "subject"
      direction: null
      role_dcid: null
  expected_no_data_reason: null
  candidate_count: null
  status: VERIFIED_AGAINST_GRAPH
  notes: |-
    measuredProperty=fertilityRate reverse-arc on prod yields 8 SVs; FertilityRate_Person_Male is excluded (0 observations at country/KEN and absent from NL detect for this query), leaving 7. NL detect returns FertilityRate_Person_Female at cosine 1.0; only it has observations at country/KEN (65 obs from 1960). The other 6 resolve as shape-siblings (candidates) with no data at this entity.
     [2026-06-24: dominance rule resolves this definite (FertilityRate_Person_Female dominates, cosine margin 0.243 > std-01); was candidates.] [2026-06-26: completed the definite flip -- set expected_shape to Person/fertilityRate/measuredValue, behaviour tag to definite, and dropped the subject-place where-slot (Kenya binds as a subject entity, not a where-axis slot). The earlier edit left expected_shape null, which made interpretation_match unscoreable.]

- id: "nd-01"
  query: "teacher count in Nauru"
  entry_path: "raw_text"
  tags:
    - behaviour: no_data
    - domain: standard
    - seam: na
    - conjunction: none
  expected_status: "no_data"
  expected_shape:
    population_type_dcid: "Teacher"
    measured_property_dcid: "count"
    stat_type_dcid: "measuredValue"
    measurement_qualifier_dcid: null
    measurement_denominator_dcid: null
  expected_slots:
    - axis: "where"
      property_dcid: null
      binding_kind: "value"
      value_dcid: "country/NRU"
  expected_stat_vars: ["Count_Teacher"]
  expected_entities:
    - dcid: "country/NRU"
      role_kind: "subject"
      direction: null
      role_dcid: null
  expected_no_data_reason: "no_observations"
  candidate_count: null
  status: VERIFIED_AGAINST_GRAPH
  notes: >
    Count_Teacher exists on prod (populationType=Teacher, measuredProperty=count, statType=measuredValue,
    no constraints) and surfaces as a rank-2 NL-detect candidate, so the variable resolves. country/NRU
    (Nauru) resolves as a valid geo entity, but the observation probe returns n_facets=0, n_observations=0.
    Variable and entity both resolve; the data is absent -> no_observations.

- id: "nd-02"
  query: "official development assistance to the Republic of Atlantis"
  entry_path: "raw_text"
  tags:
    - behaviour: no_data
    - domain: development_finance
    - seam: na
    - conjunction: none
  expected_status: "no_data"
  expected_shape: null
  expected_slots: []
  expected_stat_vars: []
  expected_entities: []
  expected_no_data_reason: "entity_not_resolved"
  candidate_count: null
  status: VERIFIED_AGAINST_GRAPH
  notes: >
    "Republic of Atlantis" does not resolve to any graph node on staging or prod (probed country/ATL,
    country/Atlantis, country/ATLANTIS, place/Atlantis, wikidataId/Atlantis - all empty; NL detect
    place_detection returned {}). dev-finance SVs are irrelevant with no resolvable entity; no shape can be
    constructed -> entity_not_resolved.

- id: "nd-03"
  query: "left-handedness rate in France"
  entry_path: "raw_text"
  tags:
    - behaviour: no_data
    - domain: standard
    - seam: na
    - conjunction: none
  expected_status: "no_data"
  expected_shape: null
  expected_slots: []
  expected_stat_vars: []
  expected_entities:
    - dcid: "country/FRA"
      role_kind: "subject"
      direction: null
      role_dcid: null
  expected_no_data_reason: "variable_not_resolved"
  candidate_count: null
  status: VERIFIED_AGAINST_GRAPH
  notes: |
    country/FRA resolves on prod (alpha2=FR). NL detect returns ~60 SVs for "left-handedness rate in France" (closest match Percent_Person_WithArthritis, cosine ~0.42). QRE_RELEVANCE_THRESHOLD=0.5 drops all of them, leaving empty recall -> variable_not_resolved. (No actual left-handedness StatVar exists in Data Commons.)

- id: "cand-r1"
  query: "GDP of Brazil"
  entry_path: "raw_text"
  tags:
    - behaviour: candidates
    - domain: standard
    - seam: na
    - conjunction: none
  expected_status: "candidates"
  expected_shape: null
  expected_slots:
    - axis: "where"
      property_dcid: null
      binding_kind: "value"
      value_dcid: "country/BRA"
  expected_stat_vars:
    - "Amount_EconomicActivity_GrossDomesticProduction_Nominal"
    - "GrowthRate_Amount_EconomicActivity_GrossDomesticProduction"
    - "Amount_EconomicActivity_GrossDomesticProduction_Nominal_PerCapita"
    - "ONE/who_gge-gdp"
  expected_entities:
    - dcid: "country/BRA"
      role_kind: "subject"
      direction: null
      role_dcid: null
  expected_no_data_reason: null
  candidate_count: 3
  status: VERIFIED_AGAINST_GRAPH
  notes: |
    All four candidate SVs probed on prod (datacommons.one.org) for country/BRA (Brazil, confirmed Country node).
    Five-tuples confirmed distinct -- no two share the same tuple:

    (1) Amount_EconomicActivity_GrossDomesticProduction_Nominal:
        populationType=EconomicActivity, measuredProperty=amount, statType=measuredValue,
        measurementQualifier=Nominal, measurementDenominator=null.
        65 observations at country/BRA (1960-2024, 1 facet). Nominal GDP in current USD.

    (2) GrowthRate_Amount_EconomicActivity_GrossDomesticProduction:
        populationType=EconomicActivity, measuredProperty=amount, statType=growthRate,
        measurementQualifier=null, measurementDenominator=null.
        64 observations at country/BRA (1961-2024, 1 facet). Differs from (1) on statType alone.

    (3) Amount_EconomicActivity_GrossDomesticProduction_Nominal_PerCapita:
        populationType=EconomicActivity, measuredProperty=amount, statType=measuredValue,
        measurementQualifier=Nominal, measurementDenominator=PerCapita.
        65 observations at country/BRA (1960-2024, 1 facet). Same as (1) except measurementDenominator=PerCapita.

    (4) ONE/who_gge-gdp:
        populationType=EconomicActivity, measuredProperty=null, statType=measuredValue,
        measurementQualifier=null, measurementDenominator=Amount_EconomicActivity_GrossDomesticProduction_Nominal.
        24 observations at country/BRA (2000-2023, 1 facet). Government health expenditure as % of GDP;
        uses GDP as its denominator, not as the measured quantity -- structurally distinct shape.

    Ambiguity source: "GDP" alone does not specify whether the user wants the nominal level, the
    growth rate, the per-capita figure, or a ratio that uses GDP as a denominator. The where slot
    (country/BRA) is the only unambiguous binding. No seam applies -- Brazil is a standard subject
    entity, not a development-finance recipient or directional flow endpoint.
    country/BRA confirmed: name=Brazil, typeOf=Country (prod).
    Amount_EconomicActivity_GrossDomesticProduction_RealValue has 0 observations at country/BRA
    (vs 44 obs at country/IND) and is excluded from candidates.
    Engine note (2026-06-24): the resolver returns 3 candidates, not 4. SV (4) ONE/who_gge-gdp has
    measuredProperty=null, so it is dropped before the candidates decision (a null measuredProperty
    cannot form a valid Shape). candidate_count is set to 3 to match the engine.

- id: "cand-r2"
  query: "income in California"
  entry_path: "raw_text"
  tags:
    - behaviour: candidates
    - domain: standard
    - seam: na
    - conjunction: none
  expected_status: "candidates"
  expected_shape: null
  expected_slots:
    - axis: "where"
      property_dcid: null
      binding_kind: "value"
      value_dcid: "geoId/06"
  expected_stat_vars:
    - "Median_Income_Person"
    - "Median_Income_Household"
  expected_entities:
    - dcid: "geoId/06"
      role_kind: "subject"
      direction: null
      role_dcid: null
  expected_no_data_reason: null
  candidate_count: 2
  status: DEFERRED
  notes: "DEFERRED (2026-06-26): the golden is verified against the graph, but the engine cannot resolve it live -- resolve_entity filters to Country-typed entities, so 'California' (geoId/06, a US State) returns no_data/entity_not_resolved on every endpoint. This is the sub-national geo gap (see .design/phase-1-steps.md risks). Excluded from the qre-standard-main merge gate via the gate-only status filter; the offline test (test_candidates_trigger.py TestCandR2*) is skipped under the same reason. Un-defer when recall() falls back to detect's entity resolution for sub-national places.\n\nProbed both SVs on prod (datacommons.one.org) 2026-06-20.\n\nMedian_Income_Person five-tuple: populationType=Person, measuredProperty=income,\nstatType=medianValue, measurementQualifier=null, measurementDenominator=null.\nConstraintProperties=[age=Years15Onwards, incomeStatus=WithIncome].\nObservations at geoId/06: 29 observations across 2 facets, date range 2010-2024.\n\nMedian_Income_Household five-tuple: populationType=Household, measuredProperty=income,\nstatType=medianValue, measurementQualifier=null, measurementDenominator=null.\nConstraintProperties=[].\nObservations at geoId/06: 29 observations across 2 facets, date range 2010-2024.\n\nFive-tuples differ on populationType (Person vs Household). Both share measuredProperty=income\nand statType=medianValue, but the population type is structurally distinct: Person income\n(individual earner, age 15+, with income constraint) vs Household income (household-level\naggregate, no individual age/income-status constraint). The query \"income in California\"\ndoes not specify a subject unit (person vs household), producing genuine ambiguity between\nthese two shapes. geoId/06 confirmed as California (State node, containedInPlace=country/USA).\nNo seam: California is a standard geo entity (no donor/recipient directionality).\nNo conjunction: query names only one concept (income) and one place (California)."

- id: "df-14"
  query: "health ODA grants from USA to Kenya and Ethiopia"
  entry_path: "raw_text"
  tags:
    - behaviour: definite
    - domain: development_finance
    - seam: both
    - conjunction: none
  expected_status: "definite"
  expected_shape:
    population_type_dcid: "DevelopmentFinance"
    measured_property_dcid: "DevelopmentFinanceFlow"
    stat_type_dcid: "measuredValue"
    measurement_qualifier_dcid: null
    measurement_denominator_dcid: null
  expected_slots:
    - axis: "what"
      property_dcid: "DevelopmentFinanceScheme"
      binding_kind: "value"
      value_dcid: "ODAGrants"
    - axis: "how"
      property_dcid: "DevelopmentFinancePurpose"
      binding_kind: "value"
      value_dcid: "DAC/Health"
    - axis: "where"
      property_dcid: "DevelopmentFinanceRecipient"
      binding_kind: "set"
      value_dcid:
        - "country/KEN"
        - "country/ETH"
  expected_stat_vars:
    - "ONE/CRS_DAC/Health-ODAGrants-KEN"
    - "ONE/CRS_DAC/Health-ODAGrants-ETH"
  expected_entities:
    - dcid: "country/USA"
      role_kind: "directional"
      direction: "from"
      role_dcid: "observationAbout"
    - dcid: "country/KEN"
      role_kind: "directional"
      direction: "to"
      role_dcid: "DevelopmentFinanceRecipient"
    - dcid: "country/ETH"
      role_kind: "directional"
      direction: "to"
      role_dcid: "DevelopmentFinanceRecipient"
  expected_no_data_reason: null
  candidate_count: null
  status: VERIFIED_AGAINST_GRAPH
  notes: >
    Two-recipient directional dev-finance query. The phrase "to Kenya and Ethiopia"
    binds both recipients on the where-axis as binding_kind=set over country/KEN and
    country/ETH (the multi-recipient set binding). Grounded live against staging
    (dc-staging.one.org) on 2026-06-29 with gemini-3.1-flash-lite. Result is definite,
    spec_id spec_54e719956acae88f, member_count 2. Both SVs confirmed,
    ONE/CRS_DAC/Health-ODAGrants-KEN ("Health [Grants to Kenya]") and
    ONE/CRS_DAC/Health-ODAGrants-ETH ("Health [Grants to Ethiopia]"), with
    Scheme=ODAGrants and Purpose=DAC/Health. Donor country/USA is directional from
    via observationAbout. Both recipients are directional to via
    DevelopmentFinanceRecipient. No MULTI_RECIPIENT_TRUNCATED warning fires because
    the set binding carries both recipients rather than truncating to one.

- id: "spec-df-01"
  query: "spec_resubmit: health ODA grants to Ethiopia"
  entry_path: "spec_resubmit"
  shape_id: "dev_finance_crs_dac"
  slots:
    - key:
        axis: "what"
        property:
          dcid: "DevelopmentFinanceScheme"
          label: "Scheme"
        label: "scheme"
      binding:
        kind: "value"
        value:
          ref:
            dcid: "ODAGrants"
            label: "ODA Grants"
          value_kind: "enum_value"
          time_window: null
          literal: null
    - key:
        axis: "how"
        property:
          dcid: "DevelopmentFinancePurpose"
          label: "Purpose"
        label: "purpose"
      binding:
        kind: "value"
        value:
          ref:
            dcid: "DAC/Health"
            label: "Health"
          value_kind: "enum_value"
          time_window: null
          literal: null
    - key:
        axis: "where"
        property:
          dcid: "DevelopmentFinanceRecipient"
          label: "Recipient"
        label: "recipient"
      binding:
        kind: "value"
        value:
          ref:
            dcid: "country/ETH"
            label: "Ethiopia"
          value_kind: "entity"
          time_window: null
          literal: null
  tags:
    - behaviour: definite
    - domain: development_finance
    - seam: "on"
    - conjunction: none
  expected_status: "definite"
  expected_shape:
    population_type_dcid: "DevelopmentFinance"
    measured_property_dcid: "DevelopmentFinanceFlow"
    stat_type_dcid: "measuredValue"
    measurement_qualifier_dcid: null
    measurement_denominator_dcid: null
  expected_slots:
    - axis: "what"
      property_dcid: "DevelopmentFinanceScheme"
      binding_kind: "value"
      value_dcid: "ODAGrants"
    - axis: "how"
      property_dcid: "DevelopmentFinancePurpose"
      binding_kind: "value"
      value_dcid: "DAC/Health"
    - axis: "where"
      property_dcid: "DevelopmentFinanceRecipient"
      binding_kind: "value"
      value_dcid: "country/ETH"
  expected_stat_vars:
    - "ONE/CRS_DAC/Health-ODAGrants-ETH"
  expected_entities:
    - dcid: "country/ETH"
      role_kind: "directional"
      direction: "to"
      role_dcid: "DevelopmentFinanceRecipient"
  expected_no_data_reason: null
  candidate_count: null
  status: VERIFIED_AGAINST_GRAPH
  notes: >
    Spec_resubmit dev_finance golden: exercises entry_path_audit (extract_skipped=True,
    pipeline trace carries {step: extract, ran: false}). Slots carry the same bindings
    as df-01's definite interpretation -- scheme=ODAGrants, purpose=DAC/Health,
    recipient=country/ETH. No donor entity is posted; expected_entities carries only
    the recipient (directional to). seam=on because place-as-constraint is active by
    default and country/ETH is a DevelopmentFinanceRecipient constraint value.
    Coordinator must verify in H1: (1) shape_id "dev_finance_crs_dac" routes to
    DEV_FINANCE_RULE; (2) slot value DCIDs confirmed live: ODAGrants (scheme),
    DAC/Health (purpose), country/ETH (recipient); (3) stat_var
    ONE/CRS_DAC/Health-ODAGrants-ETH resolves; (4) engine echo has
    entry_path="spec_resubmit" and extract_skipped=True; (5) if the engine also
    reads a donor entity from the slots, update expected_entities. All slot DCIDs
    sourced from df-01 (VERIFIED_AGAINST_GRAPH). Labels in slots are placeholders
    (engine re-reads labels via node_labels_batch per A2 F4 re-read).

- id: "spec-std-01"
  query: "spec_resubmit: nominal GDP India (standard promote)"
  entry_path: "spec_resubmit"
  shape_id: "economicactivity_amount_measuredvalue_nominal"
  slots:
    - key:
        axis: "where"
        property: null
        label: "place"
      binding:
        kind: "value"
        value:
          ref:
            dcid: "country/IND"
            label: "India"
          value_kind: "entity"
          time_window: null
          literal: null
  stat_var_dcids:
    - "Amount_EconomicActivity_GrossDomesticProduction_Nominal"
  entity_dcids:
    - "country/IND"
  tags:
    - behaviour: definite
    - domain: standard
    - seam: na
    - conjunction: none
  expected_status: "definite"
  expected_shape:
    population_type_dcid: "EconomicActivity"
    measured_property_dcid: "amount"
    stat_type_dcid: "measuredValue"
    measurement_qualifier_dcid: "Nominal"
    measurement_denominator_dcid: null
  expected_slots:
    - axis: "how"
      property_dcid: "activitySource"
      binding_kind: "value"
      value_dcid: "GrossDomesticProduction"
  expected_stat_vars:
    - "Amount_EconomicActivity_GrossDomesticProduction_Nominal"
  expected_entities:
    - dcid: "country/IND"
      role_kind: "subject"
      direction: null
      role_dcid: null
  expected_no_data_reason: null
  candidate_count: null
  status: VERIFIED_AGAINST_GRAPH
  notes: >
    Standard promote golden: exercises A2 standard-promote path (spec_resubmit with
    stat_var_dcids + entity_dcids for a catch-all shape_id). Promotes the
    Amount_EconomicActivity_GrossDomesticProduction_Nominal candidate from std-02 to
    definite. shape_id "economicactivity_amount_measuredvalue_nominal" is the
    discover.py five-tuple form (pop_EconomicActivity_amount_measuredValue_Nominal
    lowercased). The where-slot posts country/IND as the subject entity; entity_dcids
    also carries it (A2.4 entity_dcids[0] precedence). Standard promote is
    promote-only -- refine rejected with 400 (no slot edits here). seam=na: standard
    GDP SV has no directional entity. Verified against staging: engine returns definite
    with entry_path="spec_resubmit" and extract_skipped=True; activitySource binds
    GrossDomesticProduction on the "how" axis as an enum_value (not a constraintProperties
    arc); 109 obs at country/IND. stat_var_dcid and entity_dcid sourced from std-02.

- id: "cand-r3"
  query: "GDP of Ethiopia"
  entry_path: "raw_text"
  tags:
    - behaviour: candidates
    - domain: standard
    - seam: na
    - conjunction: none
  expected_status: "candidates"
  expected_shape: null
  expected_slots:
    - axis: "where"
      property_dcid: null
      binding_kind: "value"
      value_dcid: "country/ETH"
  expected_stat_vars:
    - "Amount_EconomicActivity_GrossDomesticProduction_Nominal"
    - "GrowthRate_Amount_EconomicActivity_GrossDomesticProduction"
    - "Amount_EconomicActivity_GrossDomesticProduction_Nominal_PerCapita"
    - "ONE/who_gge-gdp"
  expected_entities:
    - dcid: "country/ETH"
      role_kind: "subject"
      direction: null
      role_dcid: null
  expected_no_data_reason: null
  candidate_count: 3
  status: VERIFIED_AGAINST_GRAPH
  notes: >
    Candidates floor golden (F13): lifts main-slice verified candidates count from
    9 to 10. Modelled on cand-r1 ("GDP of Brazil") -- same four GDP shapes, different
    entity. "GDP of Ethiopia" is ambiguous across nominal level, growth rate, per-capita,
    and government-expenditure-ratio shapes, mirroring cand-r1's four-SV pattern.
    candidate_count=3 mirrors cand-r1 (three distinct five-tuples from the four SVs;
    ONE/who_gge-gdp uses GDP as its denominator, not the measured quantity, giving a
    structurally distinct shape). seam=na: Ethiopia is a standard subject entity, not
    a development-finance recipient. Coordinator must verify in H1: (1) all four SVs
    exist with live observations at country/ETH; (2) actual candidate_count (engine
    output may differ if ONE/who_gge-gdp scores below the dominance threshold or
    if a RealValue GDP SV also exists for Ethiopia, as it does for India in std-02);
    (3) country/ETH resolves correctly as subject. country/ETH confirmed
    VERIFIED_AGAINST_GRAPH across df-01, df-04, df-06, df-08 and many others. All
    four stat_var DCIDs sourced from cand-r1 (VERIFIED_AGAINST_GRAPH for country/BRA).

- id: "std-denom-01"
  query: "GDP per capita India"
  entry_path: "raw_text"
  tags:
    - behaviour: definite
    - domain: standard
    - seam: na
    - conjunction: none
  expected_status: "definite"
  expected_shape:
    population_type_dcid: "EconomicActivity"
    measured_property_dcid: "amount"
    stat_type_dcid: "measuredValue"
    measurement_qualifier_dcid: "Nominal"
    measurement_denominator_dcid: null
  expected_slots:
    - axis: "how"
      property_dcid: "activitySource"
      binding_kind: "value"
      value_dcid: "GrossDomesticProduction"
  expected_stat_vars:
    - "Amount_EconomicActivity_GrossDomesticProduction_Nominal_PerCapita"
  expected_entities:
    - dcid: "country/IND"
      role_kind: "subject"
      direction: null
      role_dcid: null
  expected_no_data_reason: null
  candidate_count: null
  status: VERIFIED_AGAINST_GRAPH
  notes: >
    Standard per-capita golden (F13): "GDP per capita" selects the PerCapita variant of
    the Nominal GDP shape. Verified against staging: the engine models per-capita as a
    distinct shape family (shape_id economicactivity_amount_measuredvalue_nominal_per_percapita)
    and leaves measurement_denominator null rather than binding a PerCapita denominator;
    it resolves definite to Amount_EconomicActivity_GrossDomesticProduction_Nominal_PerCapita
    (65 obs 1960-2024 at country/IND). activitySource binds GrossDomesticProduction on the
    "how" axis. seam=na: India is a standard subject entity. SV and entity sourced from
    std-02. Note: no corpus golden now pins a non-null measurement_denominator, since the
    engine encodes per-capita in the shape family instead.

- id: "std-denom-02"
  query: "GDP per capita Brazil"
  entry_path: "raw_text"
  tags:
    - behaviour: definite
    - domain: standard
    - seam: na
    - conjunction: none
  expected_status: "definite"
  expected_shape:
    population_type_dcid: "EconomicActivity"
    measured_property_dcid: "amount"
    stat_type_dcid: "measuredValue"
    measurement_qualifier_dcid: "Nominal"
    measurement_denominator_dcid: null
  expected_slots:
    - axis: "how"
      property_dcid: "activitySource"
      binding_kind: "value"
      value_dcid: "GrossDomesticProduction"
  expected_stat_vars:
    - "Amount_EconomicActivity_GrossDomesticProduction_Nominal_PerCapita"
  expected_entities:
    - dcid: "country/BRA"
      role_kind: "subject"
      direction: null
      role_dcid: null
  expected_no_data_reason: null
  candidate_count: null
  status: VERIFIED_AGAINST_GRAPH
  notes: >
    Standard per-capita golden (F13): second per-capita entry, different entity (Brazil).
    Mirrors std-denom-01 using country/BRA (65 obs for
    Amount_EconomicActivity_GrossDomesticProduction_Nominal_PerCapita at country/BRA,
    1960-2024, from cand-r1). Verified against staging: engine resolves definite to the
    per-capita SV via the shape family economicactivity_amount_measuredvalue_nominal_per_percapita
    with measurement_denominator null; activitySource binds GrossDomesticProduction on the
    "how" axis. seam=na: Brazil is a standard subject entity. SV and entity sourced from
    cand-r1.
```

## Holdout slice

```yaml
- id: "df-02"
  query: "malaria control ODA grants from USA to Ethiopia"
  entry_path: "raw_text"
  tags:
    - behaviour: definite
    - domain: development_finance
    - seam: both
    - conjunction: none
  expected_status: "definite"
  expected_shape:
    population_type_dcid: "DevelopmentFinance"
    measured_property_dcid: "DevelopmentFinanceFlow"
    stat_type_dcid: "measuredValue"
    measurement_qualifier_dcid: null
    measurement_denominator_dcid: null
  expected_slots:
    - axis: "how"
      property_dcid: "DevelopmentFinancePurpose"
      binding_kind: "value"
      value_dcid: "DAC/Malariacontrol"
    - axis: "what"
      property_dcid: "DevelopmentFinanceScheme"
      binding_kind: "value"
      value_dcid: "ODAGrants"
    - axis: "where"
      property_dcid: "DevelopmentFinanceRecipient"
      binding_kind: "value"
      value_dcid: "country/ETH"
  expected_stat_vars: ["ONE/CRS_DAC/Malariacontrol-ODAGrants-ETH"]
  expected_entities:
    - dcid: "country/ETH"
      role_kind: "directional"
      direction: "to"
      role_dcid: "DevelopmentFinanceRecipient"
    - dcid: "country/USA"
      role_kind: "directional"
      direction: "from"
      role_dcid: "observationAbout"
  expected_no_data_reason: null
  candidate_count: null
  status: VERIFIED_AGAINST_GRAPH
  notes: |
    Probed ONE/CRS_DAC/Malariacontrol-ODAGrants-ETH on staging: confirmed populationType=DevelopmentFinance,
    measuredProperty=DevelopmentFinanceFlow, statType=measuredValue, DevelopmentFinanceScheme=ODAGrants,
    DevelopmentFinancePurpose=DAC/Malariacontrol, DevelopmentFinanceRecipient=country/ETH.
    270 observations (2007-2024) with observationAbout=country/USA confirmed via /tmp/qre_probe.py obs.
    DAC/Malariacontrol confirmed as leaf node (no inverse isPartOf children).
    country/USA and country/ETH both confirmed live on staging.
    Seam-ON (shown above): ETH is directional "to" via DevelopmentFinanceRecipient constraint; USA is the
    donor country, role_kind=directional direction=from, role_dcid=observationAbout (observation-sourced).
    Seam-OFF rendering: same spec, country/ETH becomes role_kind: subject (no directional
    role), and country/USA also becomes role_kind: subject -- the seam resolves directionality mechanically.
    [conformance: named donor is a directional 'from' role sourced from observationAbout, symmetric with the recipient's directional 'to'; supersedes the earlier Rule-6 subject treatment per place-as-constraint-seam.md]

- id: "df-10"
  query: "education ODA to India"
  entry_path: "raw_text"
  tags:
    - behaviour: definite
    - domain: development_finance
    - seam: "on"
    - conjunction: none
  expected_status: "definite"
  expected_shape:
    population_type_dcid: "DevelopmentFinance"
    measured_property_dcid: "DevelopmentFinanceFlow"
    stat_type_dcid: "measuredValue"
    measurement_qualifier_dcid: null
    measurement_denominator_dcid: null
  expected_slots:
    - axis: "how"
      property_dcid: "DevelopmentFinancePurpose"
      binding_kind: "set"          # no education rollup purpose exists -> the set of education sub-purposes
      value_dcid:
        - "DAC/Healtheducation"
        - "DAC/Medicaleducationtraining"
    - axis: "what"
      property_dcid: "DevelopmentFinanceScheme"
      binding_kind: "value"
      value_dcid: "OfficialDevelopmentAssistance"
    - axis: "where"
      property_dcid: "DevelopmentFinanceRecipient"
      binding_kind: "value"
      value_dcid: "country/IND"
  expected_stat_vars:
    - "ONE/CRS_DAC/Healtheducation-OfficialDevelopmentAssistance-IND"
    - "ONE/CRS_DAC/Medicaleducationtraining-OfficialDevelopmentAssistance-IND"
  expected_entities:
    - dcid: "country/IND"
      role_kind: "directional"
      direction: "to"
      role_dcid: "DevelopmentFinanceRecipient"
  expected_no_data_reason: null
  candidate_count: null
  status: VERIFIED_AGAINST_GRAPH
  notes: |
    Set binding. All dev-finance SVs share one shape, so "education" spanning multiple purposes is
    same-shape openness, which the contract resolves as ONE definite spec with a set-bound how slot
    (not candidates). Probed staging via /api/explore/detect and node API. The CRS_DAC graph has NO
    education rollup purpose (no DAC/Education); only two education-sector sub-purposes exist:
    DAC/Healtheducation (health education) and DAC/Medicaleducationtraining (medical education/training),
    both typeOf DevelopmentFinancePurposeEnum. With no parent aggregate to bind as a value, the how slot
    binds the SET of these two children; value_dcid lists the purpose constraint values
    [DAC/Healtheducation, DAC/Medicaleducationtraining] and expected_stat_vars carries the resolved SVs.
    Both SVs confirmed live for country/IND under OfficialDevelopmentAssistance with real observations
    (Healtheducation-ODA-IND 84 obs 1982-2021 donor USA; Medicaleducationtraining-ODA-IND 24 obs
    2012-2014 donor FRA). The staging dataset holds only health-sector CRS_DAC purposes (29 total).
    OfficialDevelopmentAssistance (typeOf Property), country/IND (typeOf Country) confirmed. Seam-OFF
    rendering: country/IND as role_kind subject (observationAbout).
    [conformance: set binding value_dcid was null; fixed to list [DAC/Healtheducation, DAC/Medicaleducationtraining] per Rule 3 (set must have >1 explicit member). Comment in original saying members were carried in expected_stat_vars was incorrect -- expected_stat_vars holds SV dcids, value_dcid holds the constraint property values.]

- id: "df-12"
  query: "health ODA grants from USA to Nauru"
  entry_path: "raw_text"
  tags:
    - behaviour: no_data
    - domain: development_finance
    - seam: both
    - conjunction: none
  expected_status: "no_data"
  expected_shape:
    population_type_dcid: "DevelopmentFinance"
    measured_property_dcid: "DevelopmentFinanceFlow"
    stat_type_dcid: "measuredValue"
    measurement_qualifier_dcid: null
    measurement_denominator_dcid: null
  expected_slots:
    - axis: "how"
      property_dcid: "DevelopmentFinancePurpose"
      binding_kind: "value"
      value_dcid: "DAC/Health"
    - axis: "what"
      property_dcid: "DevelopmentFinanceScheme"
      binding_kind: "value"
      value_dcid: "ODAGrants"
    - axis: "where"
      property_dcid: "DevelopmentFinanceRecipient"
      binding_kind: "value"
      value_dcid: "country/NRU"
  expected_stat_vars: ["ONE/CRS_DAC/Health-ODAGrants-NRU"]
  expected_entities:
    - dcid: "country/USA"
      role_kind: "directional"
      direction: "from"
      role_dcid: "observationAbout"
    - dcid: "country/NRU"
      role_kind: "directional"
      direction: "to"
      role_dcid: "DevelopmentFinanceRecipient"
  expected_no_data_reason: "no_observations"
  candidate_count: null
  status: VERIFIED_AGAINST_GRAPH
  notes: |
    Probed staging graph: ONE/CRS_DAC/Health-ODAGrants-NRU node confirmed with
    DevelopmentFinancePurpose=DAC/Health, DevelopmentFinanceScheme=ODAGrants,
    DevelopmentFinanceRecipient=country/NRU. country/NRU (Nauru) and country/USA
    both confirmed live. Observation probe for ONE/CRS_DAC/Health-ODAGrants-NRU @
    country/USA returned n_facets=0, n_observations=0 -- USA has no reported health
    ODA grants to Nauru in the CRS_DAC dataset. The SV is structurally valid; the
    no_data arises from missing observations, not a missing variable.
    Seam-OFF rendering: country/NRU role_kind: subject (the recipient entity becomes
    the observationAbout subject, no directional role); seam-ON rendering shown above
    (country/USA as role_kind=directional direction=from role_dcid=observationAbout (observation-sourced),
    country/NRU as directional/to via DevelopmentFinanceRecipient constraint).
    [conformance: named donor is a directional 'from' role sourced from observationAbout, symmetric with the recipient's directional 'to'; supersedes the earlier Rule-6 subject treatment per place-as-constraint-seam.md]

- id: "who-03"
  query: "external spending on administration of health financing"
  entry_path: "raw_text"
  tags:
    - behaviour: definite
    - domain: who_health
    - seam: na
    - conjunction: none
  expected_status: "definite"
  expected_shape:
    population_type_dcid: "currentHealthExpenditure"
    measured_property_dcid: null
    stat_type_dcid: "measuredValue"
    measurement_qualifier_dcid: null
    measurement_denominator_dcid: null
  expected_slots:
    - axis: "how"
      property_dcid: "healthCareFunction"
      binding_kind: "value"
      value_dcid: "HC_7_2"
    - axis: "how"
      property_dcid: "healthFinancingSource"
      binding_kind: "value"
      value_dcid: "ExternalHealthFinancing"
  expected_stat_vars: ["ONE/who_hc72-ext"]
  expected_entities: []
  expected_no_data_reason: null
  candidate_count: null
  status: VERIFIED_AGAINST_GRAPH
  notes: "Probed ONE/who_hc72-ext on prod: constraintProperties=[healthFinancingSource, healthCareFunction], healthCareFunction=HC_7_2 (Administration of health financing HC.7.2), healthFinancingSource=ExternalHealthFinancing (External sources of health financing). populationType=currentHealthExpenditure, statType=measuredValue, measuredProperty absent (WHO SVs have no measuredProperty arc -- measured_property_dcid set to null). Two-property binding: both axes bound to single values -> definite. Live data confirmed: 20 obs at country/ETH (2016-2020), 12 obs at country/KEN (2016-2018). HC_7_2 and ExternalHealthFinancing nodes probed and confirmed on prod. No seam applicable (no directional entity role in this query). [conformance: measured_property_dcid corrected from 'measuredValue' to null; healthCareFunction axis corrected from 'what' to 'how'; healthFinancingSource axis corrected from 'source' to 'how' per Rule 1 WHO constraint-property axis mapping]"

- id: "who-09"
  query: "out-of-pocket health expenditure in Kenya"
  entry_path: "raw_text"
  tags:
    - behaviour: candidates
    - domain: who_health
    - seam: na
    - conjunction: none
  expected_status: "candidates"
  expected_shape: null
  expected_slots:
    - axis: "how"
      property_dcid: "healthFinancingScheme"
      binding_kind: "value"
      value_dcid: "HF_3"
  expected_stat_vars:
    - "ONE/who_hf3"
    - "ONE/who_hf3-pc"
    - "ONE/who_hf3-che"
    - "ONE/who_hf3-gdp"
  expected_entities:
    - dcid: "country/KEN"
      role_kind: "subject"
      direction: null
      role_dcid: null
  expected_no_data_reason: null
  candidate_count: 4
  status: VERIFIED_AGAINST_GRAPH
  notes: |
    Probed all four SVs on both staging and prod. All exist with live observations for country/KEN:
    ONE/who_hf3 (absolute, 96 obs), ONE/who_hf3-pc (md=Count_Person, 120 obs),
    ONE/who_hf3-che (md=ONE/who_che, 24 obs), ONE/who_hf3-gdp
    (md=Amount_EconomicActivity_GrossDomesticProduction_Nominal, 24 obs).
    All four share populationType=currentHealthExpenditure, statType=measuredValue,
    constraintProperties=healthFinancingScheme, healthFinancingScheme=HF_3.
    The healthFinancingScheme slot is bound to value HF_3 across all candidates; what
    differs is the measurement denominator (null vs Count_Person vs ONE/who_che vs GDP nominal),
    giving four distinct five-tuples -> legitimately candidates per Rule 2.
    NL detect also surfaces ONE/who_che and non-WHO SVs but the four HF.3 variants are the
    directly competing specs. No dev-finance entity involved; seam=na. country/KEN confirmed live.
    [conformance: healthFinancingScheme axis corrected from 'source' to 'how' per Rule 1 WHO constraint-property axis mapping]
    [conformance: expected_shape set to null -- candidates span four distinct five-tuples (the denominator varies), so there is no single shape; matches every other candidates golden and Rule 2]

- id: "sdg-02"
  query: "SDG 3.3.1 HIV new infections per 1000 women"
  entry_path: "raw_text"
  tags:
    - behaviour: definite
    - domain: sdg
    - seam: na
    - conjunction: none
  expected_status: "definite"
  expected_shape:
    population_type_dcid: "SDG_SH_HIV_INCD"
    measured_property_dcid: "value"
    stat_type_dcid: "measuredValue"
    measurement_qualifier_dcid: null
    measurement_denominator_dcid: null
  expected_slots:
    - axis: "what"
      property_dcid: "sdg_sex"
      binding_kind: "value"
      value_dcid: "SDG_SexEnum_F"
  expected_stat_vars: ["sdg/SH_HIV_INCD.SEX--F"]
  expected_entities: []
  expected_no_data_reason: null
  candidate_count: null
  status: VERIFIED_AGAINST_GRAPH
  notes: "Node probe on prod: populationType=SDG_SH_HIV_INCD, measuredProperty=value, statType=measuredValue, constraintProperties=[sdg_sex], sdg_sex=SDG_SexEnum_F. 23 obs at country/KEN (2000-2022). The indicator code 3.3.1 plus 'women' (female sex) uniquely identifies this SV. No measurementDenominator or measurementQualifier arcs present. Definite: within SDG domain, female HIV incidence per 1000 uninfected population resolves to a single SV."

- id: "sdg-07"
  query: "maternal mortality in Nauru"
  entry_path: "raw_text"
  tags:
    - behaviour: no_data
    - domain: sdg
    - seam: na
    - conjunction: none
  expected_status: "no_data"
  expected_shape:
    population_type_dcid: "SDG_SH_STA_MORT"
    measured_property_dcid: "value"
    stat_type_dcid: "measuredValue"
    measurement_qualifier_dcid: null
    measurement_denominator_dcid: null
  expected_slots:
    - axis: "what"
      property_dcid: "sdg_sex"
      binding_kind: "value"
      value_dcid: "SDG_SexEnum_F"
  expected_stat_vars: ["sdg/SH_STA_MORT.SEX--F"]
  expected_entities:
    - dcid: "country/NRU"
      role_kind: "subject"
      direction: null
      role_dcid: null
  expected_no_data_reason: "no_observations"
  candidate_count: null
  status: VERIFIED_AGAINST_GRAPH
  notes: >
    Probed prod. sdg/SH_STA_MORT.SEX--F confirmed live: populationType=SDG_SH_STA_MORT,
    measuredProperty=value, statType=measuredValue, constraintProperties=[sdg_sex],
    sdg_sex=SDG_SexEnum_F. Observation probe: sdg/SH_STA_MORT.SEX--F @ country/NRU = 0
    observations (n_facets=0). Comparison probes: country/ETH = 21 observations (2000-2020),
    country/USA = 21 observations. country/NRU node resolves on prod (entity exists).
    SV is definite -- it is the only SDG maternal mortality ratio SV in the graph.
    no_data reason = no_observations (entity known but no data for it under this variable).

- id: "std-07"
  query: "infant mortality rate Ethiopia"
  entry_path: "raw_text"
  tags:
    - behaviour: candidates
    - domain: standard
    - seam: na
    - conjunction: none
  expected_status: "candidates"
  expected_shape: null
  expected_slots:
    - axis: "what"
      property_dcid: "age"
      binding_kind: "value"
      value_dcid: "Years0"
  expected_stat_vars:
    - "Count_Death_0Years_AsFractionOf_Count_BirthEvent_LiveBirth"
    - "Count_Death_0Years_Female_AsFractionOf_Count_BirthEvent_LiveBirth_Female"
    - "Count_Death_0Years_Male_AsFractionOf_Count_BirthEvent_LiveBirth_Male"
  expected_entities:
    - dcid: "country/ETH"
      role_kind: "subject"
      direction: null
      role_dcid: null
  expected_no_data_reason: null
  candidate_count: 3
  status: VERIFIED_AGAINST_GRAPH
  notes: |
    Probed prod. Three SVs all have 58 observations at country/ETH (1966-2023) and share
    populationType=MortalityEvent, measuredProperty=count, statType=measuredValue, age=Years0,
    but differ on gender constraint and measurement denominator (five-tuple differs on md):
      - Count_Death_0Years_AsFractionOf_Count_BirthEvent_LiveBirth: no gender constraint,
        md=Count_BirthEvent_LiveBirth (all-sex rate, the canonical IMR).
      - Count_Death_0Years_Female_AsFractionOf_Count_BirthEvent_LiveBirth_Female: gender=Female,
        md=Count_BirthEvent_LiveBirth_Female.
      - Count_Death_0Years_Male_AsFractionOf_Count_BirthEvent_LiveBirth_Male: gender=Male,
        md=Count_BirthEvent_LiveBirth_Male.
    Graph-probed five-tuples confirm all three measurementDenominators differ -> different five-tuples
    -> legitimately candidates per Rule 2 (not same-shape openness). candidate_count=3 matches
    len(expected_stat_vars). Absolute count variants (Count_Death_Infant, WHO/CM_02) and the
    alternate-age-band rate (Count_Death_LessThan1Year_AsAFractionOf_Count_BirthEvent) have
    0 observations at ETH and are excluded from the candidate set. The seam is not applicable:
    no dev-finance or directional entity logic involved. country/ETH confirmed as a valid entity on prod.

- id: "conj-01"
  query: "population and GDP in Brazil"
  entry_path: "raw_text"
  tags:
    - behaviour: definite
    - domain: standard
    - seam: na
    - conjunction: cross_shape
  expected_status: "definite"
  expected_shape:
    population_type_dcid: "Person"
    measured_property_dcid: "count"
    stat_type_dcid: "measuredValue"
    measurement_qualifier_dcid: null
    measurement_denominator_dcid: null
  expected_slots: []
  expected_stat_vars: ["Count_Person"]
  expected_entities:
    - dcid: "country/BRA"
      role_kind: "subject"
      direction: null
      role_dcid: null
  expected_no_data_reason: null
  candidate_count: null
  status: DEFERRED
  notes: >
    Cross-shape conjunction: population (Count_Person, five-tuple Person/count/measuredValue)
    and GDP (Amount_EconomicActivity_GrossDomesticProduction_Nominal, five-tuple
    EconomicActivity/amount/measuredValue/Nominal). Primary is population by earliest_index.
    additional_interpretations carries the GDP Spec. CONJUNCTION_CROSS_SHAPE emitted.
    Offline test fixtures: Extraction returns variables=[population, GDP], bare-variable
    detect entries added, Count_Person|country/BRA observation added. DEFERRED because
    dev-finance live baselines are staging-only and this golden exercises a new path.

- id: "conj-02"
  query: "ODA grants and ODA loans to Ethiopia"
  entry_path: "raw_text"
  tags:
    - behaviour: definite
    - domain: development_finance
    - seam: na
    - conjunction: same_shape
  expected_status: "definite"
  expected_shape:
    population_type_dcid: "DevelopmentFinance"
    measured_property_dcid: "DevelopmentFinanceFlow"
    stat_type_dcid: "measuredValue"
    measurement_qualifier_dcid: null
    measurement_denominator_dcid: null
  expected_slots:
    - axis: "what"
      property_dcid: "DevelopmentFinanceScheme"
      binding_kind: "set"
      value_dcid:
        - "ODAGrants"
        - "ODALoans"
    - axis: "how"
      property_dcid: "DevelopmentFinancePurpose"
      binding_kind: "unbound"
      value_dcid: null
    - axis: "where"
      property_dcid: "DevelopmentFinanceRecipient"
      binding_kind: "value"
      value_dcid: "country/ETH"
  expected_stat_vars:
    - "ONE/CRS_DAC/Health-ODAGrants-ETH"
    - "ONE/CRS_DAC/Health-ODALoans-ETH"
  expected_entities:
    - dcid: "country/ETH"
      role_kind: "subject"
      direction: null
      role_dcid: null
  expected_no_data_reason: null
  candidate_count: null
  status: DEFERRED
  notes: >
    Same-shape conjunction: ODA grants and ODA loans share the same five-tuple
    (DevelopmentFinance/DevelopmentFinanceFlow/measuredValue) and differ only in
    the DevelopmentFinanceScheme slot (ODAGrants vs ODALoans). The engine collapses
    them via Tier-1 same-shape merge into one Spec with binding_kind=set on the
    scheme slot. No CONJUNCTION_CROSS_SHAPE warning because both conjuncts resolve
    to the same five-tuple shape. DEFERRED: awaiting offline fixture coverage for
    the dev-finance bind step on the per-variable "ODA loans" path.
```