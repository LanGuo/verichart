# Research: model-free clinical relation extraction

*Compiled 2026-09-10 for the Phase 4 plan (`docs/superpowers/plans/2026-09-09-verichart-phase4-relations.md`).
Scope: linking a medication mention to its attributes (dose, strength, frequency, route, form,
duration), and secondarily a lab to its value/unit and a problem to its severity/stage/site,
**without a trained relation model** — for reproducibility, auditability, and CPU-scale operation.*

## Bottom line

- **Pure linear nearest-neighbour linking is a ~0.86 micro-F1 floor on n2c2 2018 (gold entities)**
  — a baseline, not the state of the art for rule-based systems.
- The state of the art for rule-based systems (MedEx) is an **ordered semantic grammar**. A full
  chart parser is not required: the **directional prior** (per attribute type, does it precede or
  follow the drug?) plus **type compatibility** and a **no-crossing-another-anchor** constraint
  recover most of the grammar's benefit.
- **Dependency-path features add only ~2–3 F1** — not worth a POS/dependency model dependency.
- **No existing OSS tool is worth adopting** (licensing, Java-UIMA weight, or abandonment).
- A deterministic linker is **safe for strength / form / frequency / dose / route ↔ drug, lab ↔
  value·unit, and problem ↔ severity** (rule F1 0.89–0.98). It is **not safe for duration, body
  site, stage, reason/indication, or ADE** (rule F1 0.41–0.73) — those need the LLM path.
- All published rule F1 assumes **gold entity spans**. Recognizer errors compound, so the linker
  must be evaluated **end to end**, not against gold entities.

## Existing rule / grammar-based extractors

| Tool | Linking algorithm | Lang | License | Maintained | Installable |
|---|---|---|---|---|---|
| **MedEx** (Xu et al. 2010) | semantic tagger (RxNorm + regex) → **top-down chart parser over a BNF grammar of semantic tags**; regex chunker fallback | Python/NLTK | non-standard ("free with UMLS licence") | no (→ MedEx-UIMA) | no |
| **MedEx-UIMA** (Jiang et al. 2015) | same, reimplemented for speed | Java/UIMA | custom academic; commercial via Melax | minimal | no |
| **MedXN** (Sohn et al. 2014) | **proximity windowing** + regex: attributes bound to a drug within `drug start → min(next drug, +2 sentences)`; special expansions ("IV+", parentheticals) | Java/UIMA | Apache-2.0 | no (~2016) | no |
| **medExtractR** (Weeks et al. 2020) | lexicon + regex; **targeted** (caller names the drug); attributes linked by a **character window** around it | R | **GPL-2 \| GPL-3** (copyleft) | lightly (CRAN 0.4.1, 2022) | `install.packages` |
| **CLAMP** | pattern / co-occurrence rules over sections + regex attributes | Java | proprietary (Melax) | **no** (replaced by the LLM tool "Kiwi") | no |
| **cTAKES** Drug NER | section-aware dictionary + `DrugMentionAnnotator` **window rules**; attribute slots filled from a fixed window around the drug | Java/UIMA | Apache-2.0 | yes (5.x) | no (Java) |
| **medspaCy** | **ConText only** (assertion/negation). Relation extraction is listed as future work; the separate `medspacy/relation_extraction` repo is ML (biLSTM/BERT), 1 commit, no release | Python | MIT | yes | `pip install medspacy` |

Sig parsers (`parsigs`, `parserx`) target already-structured pharmacy signature strings, not
narrative notes. Nothing rule-based has appeared since ~2020 — the field moved to
transformers/LLMs.

**Conclusion:** MedXN and cTAKES are OSS-licence-compatible but dead / heavy-Java; medExtractR
is GPL and R; MedEx-UIMA and CLAMP are non-OSS. verichart implements its own.

## What the good systems actually do

MedEx is the reference design: a BNF grammar over semantic tags —
`S → DRUGLIST`, `DRUG → DrugName SIG*`, `SIG → DOSE | FORM | RUT | FREQ | DURATION | …` — with
**alternation rules for reordering** and separate productions for single- vs multi-signature
drugs (`Midrin 2 po initial then 1 po q6h prn #5`). Multiple drugs become independent `DRUG`
sub-trees; pre-drug attributes (`500 mg metformin`) are handled by grammar rules that allow
modifiers on either side; chart parsing gives a globally consistent bracketing that greedy
nearest-neighbour cannot.

The best isolation of **grammar vs proximity** is Mahendran & McInnes (2021), who ran a pure
nearest-drug linker on n2c2 2018 with directional variants:

| relation | undirected F1 | left-only F1 |
|---|---|---|
| Route–Drug | 0.53 | **0.89** |
| Frequency–Drug | 0.48 | **0.98** |

The **directional prior is the single highest-value rule** — it is most of what the grammar buys
for the easy attributes. Coordinated list constructions
("metformin and lisinopril, 500 mg and 10 mg respectively") defeat both proximity and MedEx's
grammar; those are LLM territory.

## Dependency-path heuristics

Marginal for this task. Dligach et al. (2014) ablated an SVM linker for problem→severity and
problem→body-site: dependency-tree + dependency-path features together add **~2–3 F1**
(severity full model 0.972, −0.028/−0.018 without dep features; body-site 0.776,
−0.019/−0.021). Their conclusion: entity-type features then token features dominate; "tree
kernel features did not improve performance." An older dependency-based prescription parser
(Solt & Tikk) reached 60–95 % per field, 67.5 % all-fields-correct — not better than MedEx's
grammar. **A POS/dependency model is not worth the dependency for the ~2 % gain.**

## n2c2 2018 Track 2 / i2b2 2009 — where model-free is safe

n2c2 2018 Track 2 (Henry et al. 2020): best relation-classification F1 **0.963**, best
end-to-end **0.891**; **90.8 % of relations are intra-sentential** — so sentence scoping caps
recall at ~91 % and is otherwise fine.

Pure nearest-neighbour rule baseline, **gold entities** (Mahendran & McInnes, Table 5):

| relation | rule F1 | best neural F1 |
|---|---|---|
| Strength–Drug | **0.95** | 0.98–0.99 |
| Form–Drug | **0.98** | 0.97–0.98 |
| Frequency–Drug | **0.98** | 0.96 |
| Dosage–Drug | **0.89** | 0.97 |
| Route–Drug | **0.89** | 0.97 |
| Duration–Drug | 0.73 | 0.88–0.89 |
| ADE–Drug | 0.43 (0.64 tuned) | 0.80–0.81 |
| Reason–Drug | 0.41 (0.59 tuned) | 0.76 |
| micro avg | 0.86 | 0.94 |

i2b2 2009 shows the same shape — MedEx reported strength 94.5, route 93.9, frequency 96.0,
form 89, dose 88 on discharge summaries.

**Safe deterministically** (≥0.89, near-solved by rules): strength, form, frequency, dose,
route ↔ drug. Add the directional prior and dose/route rise toward 0.95. Lab ↔ value/unit is
similarly templated; problem ↔ severity ~0.95 (Dligach). **Not safe** (route to the LLM):
duration ↔ drug (0.73), problem ↔ body-site/stage (~0.78), reason/indication (0.41), ADE (0.43)
— these typically *follow* the drug and compete across multiple drugs in a sentence.

## How this shaped Phase 4

- The rules adapter is `RuleRelationLinker` — an **ordered-directional linker** (`_ATTR_RULES`
  config: family · directional prior · safe flag), sentence-scoped, no character gap gate, no
  chart parser, no dependency model.
- It emits **only the safe relation types** by default; the rest are `LlmRelationExtractor`'s job.
- Every emitted relation records `method` / `direction` / `token_gap` for audit.
- Attribute recognition for the deterministic tier is a regex/lexicon `AttributeRecognizer`
  (MedEx-semantic-tagger / `parsigs` lineage) so the tier is genuinely model-free end to end for
  medication statements; GLiNER-BioMed remains an option for harder attribute spans.
- Phase 9 benchmarks the linker **end to end** (recognizer + linker), not against gold entities.

## Sources

- Xu H. et al. *MedEx: a medication information extraction system for clinical narratives.* JAMIA 2010. https://pmc.ncbi.nlm.nih.gov/articles/PMC2995636/
- Jiang M. et al. *A study of MedEx-UIMA…* 2015. https://pubmed.ncbi.nlm.nih.gov/25954575/
- Sohn S. et al. *MedXN: an open source medication extraction and normalization tool.* JAMIA 2014. https://pmc.ncbi.nlm.nih.gov/articles/PMC4147619/
- Weeks H.L. et al. *medExtractR: a targeted, customizable approach to medication extraction from electronic health records.* 2020. https://www.medrxiv.org/content/10.1101/19007286 · https://cran.r-project.org/package=medExtractR
- CLAMP. https://clamp.uth.edu/
- Apache cTAKES Drug NER. https://cwiki.apache.org/confluence/display/CTAKES/cTAKES+4.0+-+Drug+Named+Entity+Recognition
- medspaCy. https://github.com/medspacy/medspacy
- Mahendran D., McInnes B.T. *Extracting Adverse Drug Events from Clinical Notes.* arXiv 2021. https://arxiv.org/pdf/2104.10791
- Henry S. et al. *2018 n2c2 shared task on adverse drug events and medication extraction.* JAMIA 27(1):3–12, 2020. https://pubmed.ncbi.nlm.nih.gov/31584655/
- Wei Q. et al. *Relation extraction from clinical narratives using pre-trained language models.* (n2c2 2018 systems review) arXiv 2107.08957. https://arxiv.org/pdf/2107.08957
- Dligach D. et al. *Discovering body site and severity modifiers in clinical texts.* JAMIA 2014. https://pmc.ncbi.nlm.nih.gov/articles/PMC3994852
