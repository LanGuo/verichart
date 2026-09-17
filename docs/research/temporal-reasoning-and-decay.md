# Research: temporal normalization, staleness windows, drug→diagnosis inference

*Compiled 2026-09-17 for the Phase 6 plan
(`docs/superpowers/plans/2026-09-16-verichart-phase6-reasoning.md`). Scope: (A) turning relative
date phrases into normalized dates without hand-rolled regex arithmetic, (B) whether a real
knowledge base exists for "how long is this clinical value valid," (C) whether a maintained
knowledge base exists for "this drug implies that diagnosis," and what the LLM alternative looks
like.*

## Bottom line

- **A — adopt `dateparser`** (pure Python, BSD-3, actively maintained) for date arithmetic; keep
  a small regex/lexicon only for *finding* candidate phrases, never for computing the date
  itself. Skip SUTime/HeidelTime/duckling — GPL or Haskell/JVM runtime cost with no accuracy
  payoff for this use case. Clinical-specific temporal taggers (cTAKES/THYME/ClearTK-TimeML) are
  academic artifacts with no 2026 installable form.
- **B — no universal decay-window knowledge base exists.** Shahar's KBTA/RESUME formalism is
  real but never published as a reusable table. HEDIS/eCQM give real numbers, but they're
  ~12-month *quality-reporting* windows, not point-of-care safety thresholds, and at least one
  plausible-sounding measure (a standalone LDL threshold) was **retired in 2015** — a trap for
  citing from memory. verichart ships **no numeric defaults**; the empty-table behavior (no
  rule → never stale) stays, and docs show one clearly-cited, clearly-caveated worked example.
- **C — MED-RT is alive and reachable without a UMLS license** via the RxClass API (the old
  NDF-RT REST API was decommissioned in 2019). Ship a KB-lookup rule against a locally cached
  export (consistent with Phase 3's "bring your own release, no live network calls" rule) that
  surfaces *every* candidate indication rather than picking one, plus an LLM-based rule as an
  explicit opt-in — no published benchmark says either approach is better for this task.

## A — Temporal expression normalization

| Option | License | Runtime | Maintenance | Verdict |
|---|---|---|---|---|
| **SUTime** (`sutime` PyPI) | GPLv3+ | JVM (via JPype/py4j), CoreNLP jars | last release 1.0.1, Nov 2020 | ✗ GPL conflicts with MIT; heavy runtime |
| **HeidelTime** | GPL | Java + Perl | Python wrappers are thin subprocess shims over the same aging codebase | ✗ same issues |
| **duckling** (Meta/wit.ai) | BSD | Haskell, HTTP/subprocess | no clear 2025-2026 release cadence | ✗ non-Python runtime cost, no accuracy payoff |
| **dateparser** | BSD-3 | pure Python | **1.4.3, Sept 3 2026** — live | ✅ **adopt** |
| **parsedatetime** | Apache-2.0 | pure Python | latest release 2.6, **May 2020** — dormant | possible fallback, not primary |
| **cTAKES temporal / THYME / ClearTK-TimeML** | Apache-2.0 / academic | Java/UIMA | academic artifacts from the 2012 i2b2 temporal-relations challenge era; no installable 2026 library | ✗ productized nowhere |

**medspaCy confirmed**: ships only ConText's binary flags (`is_negated` / `is_historical` /
`is_hypothetical` / `is_uncertain` / `is_family`) — no date arithmetic anywhere in the package.
verichart's Phase 2 use of it was already the right scope; Phase 6 needs a separate tool.

**`dateparser`** supports a `RELATIVE_BASE` setting that anchors "3 weeks ago" / "last year" to
an arbitrary reference date (not just `datetime.now()`) — exactly what "normalize against the
document date" needs, and it also parses most absolute formats out of the box, so a hand-rolled
`MM/DD/YYYY` / `Month D, YYYY` regex is unnecessary too.

**Design implication:** verichart still needs its own lightweight span-finder (a regex/lexicon
that flags *candidate* temporal phrases in running text — "ago," "last," month names, ISO/slash
dates, "yesterday/today," "in `<year>`" — the same kind of cue-word matching
`AttributeRecognizer` already does for dose/frequency). What changes is that the candidate
substring is handed to `dateparser.parse(candidate, settings={"RELATIVE_BASE": doc_date})` for
normalization, instead of hand-written `timedelta` arithmetic. Less code, no leap-year/month-
length bugs to get wrong, and a second pair of eyes (an actively maintained library) on the hard
part.

**LLM comparison point:** 2024-2026 work (e.g. an npj Digital Medicine 2025 piece on LLM-assisted
EHR date de-identification/normalization; a 2025 preprint on longitudinal temporal reasoning)
exists, but a 2025 clinical-pipeline paper is explicit that practitioners already converge on
*"never ask the neural model to perform calendar arithmetic"* and delegate to `dateparser`'s
relative-time grammar instead — i.e., the field's own practice already matches this
recommendation. An LLM's place, if any, is pre-parsing free text, never doing the date math.

## B — Clinical data staleness / validity windows

**Shahar's Knowledge-Based Temporal Abstraction (KBTA) / RESUME** (Stanford, 1990s) formally
defines *persistence functions* — per-parameter tables of the maximal gap that still lets two
readings be treated as one ongoing interval, varying by clinical *context* (different tolerance
during chemotherapy than at baseline). It's a rigorous formalism, but every published
instantiation built its tables by interviewing domain experts for one specific study; no
successor project distributes a general-purpose, reusable persistence-function database.

**HEDIS / CMS eCQM measures give real numbers — for a different purpose.** Comprehensive
Diabetes Care (A1c during the measurement year, ~12 months), Controlling Blood Pressure (most
recent BP during the measurement year), Kidney Health Evaluation (eGFR + uACR during the
measurement year) are all population-health **quality-reporting** windows, not point-of-care
safety thresholds — a "the most recent BP counts if it's within a year" convention says nothing
about whether it's safe to titrate a medication on that reading today. **Trap avoided by this
research:** HEDIS's standalone LDL-level measure was **retired in 2015**, replaced by a statin-
therapy process measure — there is no current HEDIS "LDL within N months" number to cite.

**No structured/machine-readable source exists** — no OMOP concept-set attribute, no FHIR US
Core profile field, encodes "clinical staleness" per concept; FHIR/OMOP model *when* an
observation happened, not *how long it stays valid*. EHR data-quality literature (temporal
dataset-shift tools, multi-source validation frameworks) treats this as a dataset-shift problem,
not a per-concept decay catalog. This confirms the gap is real, not a search failure.

**Design implication:** `DEFAULT_DECAY_RULES` ships **empty**. `is_stale()`'s existing
"no matching rule → `False`" behavior means an unconfigured verichart install never claims
staleness it can't support — the safe default. `docs/reasoning.md` shows exactly one worked,
cited, explicitly-caveated example (e.g. *"A1c: 365 days, per NCQA HEDIS MY2024 Comprehensive
Diabetes Care measurement-year convention — a quality-reporting window, not a clinical-safety
threshold; set your own after clinical review"*) so a reader sees the shape of a defensible
entry without verichart asserting authority it doesn't have.

## C — Drug → diagnosis inference

**MED-RT is current** (VA/FDA, release notes through **2026.07.06**), the confirmed successor to
NDF-RT (final NDF-RT UMLS release 2019AA), still carrying `may_treat` / `may_prevent` relations,
distributed inside the UMLS Metathesaurus.

**Access path changed and matters:** the standalone NDF-RT REST API was **decommissioned
February 22, 2019**. `may_treat` data now comes through the **RxClass API**:
`GET rxnav.nlm.nih.gov/REST/rxclass/class/byRxcui?rxcui={rxcui}&relaSource=MEDRT&relas=may_treat`.
**No UMLS license is required to call RxClass/RxNav APIs** — a free UMLS account is only needed
for bulk file downloads of the raw release. A 2018-era memory of "you need a UMLS login for this"
is out of date.

**Alternatives considered:** DrugBank (indications exist but bulk use beyond academic access is
commercially licensed), SIDER (side effects, not indications — wrong resource), MEDI (a research-
grade ensemble built from RxNorm + MedlinePlus + SIDER2 + Wikipedia). OHDSI/OMOP's "drug era →
condition era" pattern is a *cohort-definition* convention (empirical co-occurrence per dataset),
not a curated indication table.

**No head-to-head benchmark exists** comparing a fixed MED-RT `may_treat` lookup against an LLM
for "infer the most likely diagnosis from a drug name plus record context." 2024-2025 LLM-in-
medication-reconciliation work (a fine-tuned-Llama conversational reconciliation agent, proof-of-
concept medication-review studies) shows LLMs used productively *around* medication lists, but
the specific ambiguity problem here (metformin → T2DM vs. PCOS vs. prediabetes) is clinically
well known and not yet benchmarked as KB-vs-LLM.

**Design implication:** ship **both**, as alternate implementations of the same rule interface —
consistent with Phase 4 (rule linker + LLM extractor) and Phase 5 (built-in resolvers + LLM
resolver):

- **KB lookup** (default): reads a **locally cached** MED-RT/RxClass export — consistent with
  Phase 3's "bring your own release, no live network call at reasoning time" rule (a live API
  call at fact-derivation time would make the derivation non-reproducible and untraceable in the
  manifest the way every other resolver in this codebase is pinned). A small loader script
  (mirroring `load_vocab_sqlite`) builds the local table once from an RxClass API pull or a bulk
  UMLS/MED-RT export. **Never collapses ambiguity** — a drug with N `may_treat` relations yields
  up to N inferred facts, each noting the alternatives, rather than silently picking one.
- **LLM-based** (opt-in): pinned model, same pattern as `VeritractLlmResolver` — reasons over the
  patient's other facts to disambiguate, explicitly documented as *unproven* against the KB
  lookup for this task (no benchmark exists either way).

## Sources

- [`sutime` on PyPI](https://pypi.org/project/sutime/) · [python-sutime](https://github.com/FraBle/python-sutime)
- [python_heideltime](https://github.com/PhilipEHausner/python_heideltime) · [py-heideltime](https://pypi.org/project/py-heideltime)
- [duckling](https://github.com/facebook/duckling)
- [dateparser on PyPI](https://pypi.org/project/dateparser/) · [dateparser docs](https://dateparser.readthedocs.io/)
- [parsedatetime](https://github.com/bear/parsedatetime) · [PyPI](https://pypi.org/project/parsedatetime)
- [cTAKES temporal module](https://cwiki.apache.org/confluence/display/CTAKES/cTAKES+4.0+-+Temporal+Module) · [PMC review of clinical temporal NLP](https://pmc.ncbi.nlm.nih.gov/articles/PMC5657277/) · [ClearTK-TimeML](https://cleartk.github.io/cleartk/docs/module/cleartk_timeml.html)
- [medspaCy](https://github.com/medspacy/medspacy)
- LLM temporal work: [npj Digital Medicine 2025, de-id + temporal normalization](https://www.nature.com/articles/s41746-025-01921-7) · [npj Digital Medicine 2025, TIMER](https://www.nature.com/articles/s41746-025-01965-9)
- Shahar KBTA/RESUME: [persistence functions, ScienceDirect](https://www.sciencedirect.com/science/article/pii/0933365795000364) · [diabetic-monitoring application, PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC2247929/)
- HEDIS MY2024 measure descriptions: [NCQA PDF](https://wpcdn.ncqa.org/www-prod/wp-content/uploads/HEDIS-MY-2024-Measure-Description.pdf) · [Kidney Health Evaluation flyer](https://www.kidney.org/sites/default/files/profed_ked_hedis_flyer_2023.pdf) · [LDL measure retirement (2015), NLA comment](https://www.lipid.org/nla/nla-comments-proposed-retirement-hedis-2015-respect-cholesterol-management-patients-cvd)
- MED-RT: [release notes through 2026.07.06](https://evs.nci.nih.gov/ftp1/MED-RT/MEDRT_Release_Notes.txt) · [NLM MED-RT page](https://www.nlm.nih.gov/research/umls/sourcereleasedocs/current/MED-RT/index.html)
- RxClass API: [API docs](https://lhncbc.nlm.nih.gov/RxNav/APIs/RxClassAPIs.html) · [MED-RT transition notice](https://lhncbc.nlm.nih.gov/RxNav/news/MEDRT_transition.html) · [RxNorm licensing FAQ](https://www.nlm.nih.gov/research/umls/rxnorm/faq.html)
- Drug-indication KB landscape: [Oxford Bioinformatics review](https://academic.oup.com/bib/article/20/4/1308/4785946) · [OHDSI Drug_Era](https://github.com/OHDSI/OMOP-Queries/blob/master/md/Drug_Era.md)
- LLM medication reconciliation: [proof-of-concept study, PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC11385755/) · [AMREC agent, medRxiv](https://www.medrxiv.org/content/10.1101/2025.06.16.25329719v1.full)
