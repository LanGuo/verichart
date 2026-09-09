"""Terminology resolution: map a ClinicalFact's ``value`` to a standard code.

Phase 3 of verichart. A ``ConceptResolver`` runs *after* facts exist (from ``to_facts``
or ``extract_entities``): ``resolve_concepts`` routes each fact to resolvers by its
``label``, sets ``concept_code`` / ``concept_system`` / ``concept_display`` /
``terminology_version``, and recomputes ``fact_id`` — a resolved fact has a
concept-anchored identity, which is what makes Phase 5 cross-source dedup possible.

verichart ships resolver *adapters* and a loader, never vocabulary content. Bring your
own release (see ``docs/terminology.md``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterable, Protocol, runtime_checkable

from typing_extensions import TypedDict

from verichart.facts import compute_fact_id

if TYPE_CHECKING:
    from verichart.facts import ClinicalFact


class ConceptMatch(TypedDict):
    code: str
    display: str
    system: str  # "SNOMED-CT" | "RxNorm" | "LOINC" | "ICD-10-CM" | "UMLS" | "MeSH" | ...
    version: str  # release identifier, e.g. "2024AB" / "2.77" / "scispacy-umls-2020AA"
    score: float  # 0-1


@runtime_checkable
class ConceptResolver(Protocol):
    system: str
    version: str

    def resolve(self, mention: str, context: str | None = None) -> ConceptMatch | None: ...


# label -> ordered systems to try. First match at/above min_score wins.
# "UMLS" is the fallback because scispaCy's umls / rxnorm / mesh linkers all
# identify concepts by UMLS CUI (see ScispacyResolver).
DEFAULT_ROUTING: dict[str, tuple[str, ...]] = {
    "PROBLEM": ("SNOMED-CT", "ICD-10-CM", "UMLS"),
    "MEDICATION": ("RxNorm", "UMLS"),
    "LAB": ("LOINC", "UMLS"),
    "PROCEDURE": ("SNOMED-CT", "UMLS"),
    "VITAL": ("LOINC", "UMLS"),
}


def terminology_versions(resolvers: list[ConceptResolver]) -> dict[str, str]:
    """``{system: version}`` for every resolver.

    Pass to ``veritract.build_manifest(extra={"terminology_versions": ...})`` so a
    point-in-time replay reproduces the same codes.
    """
    return {r.system: r.version for r in resolvers}


class MockResolver:
    """Deterministic resolver stub for tests. Registers terms by lowercased key."""

    def __init__(self, *, system: str, version: str):
        self.system = system
        self.version = version
        self._by_term: dict[str, tuple[str, str, float]] = {}  # term -> (code, display, score)

    def register(self, term: str, *, code: str, display: str, score: float = 1.0) -> None:
        self._by_term[term.strip().lower()] = (code, display, score)

    def resolve(self, mention: str, context: str | None = None) -> ConceptMatch | None:
        hit = self._by_term.get(mention.strip().lower())
        if hit is None:
            return None
        code, display, score = hit
        return ConceptMatch(
            code=code, display=display, system=self.system,
            version=self.version, score=score,
        )


# ----------------------------------------------------------------- resolve_concepts


def resolve_concepts(
    facts: list["ClinicalFact"],
    resolvers: list[ConceptResolver],
    *,
    routing: dict[str, tuple[str, ...]] | None = None,
    documents: dict[str, str] | None = None,
    context_chars: int = 120,
    min_score: float = 0.0,
    skip_resolved: bool = True,
) -> list["ClinicalFact"]:
    """Resolve each fact's ``value`` to a terminology code.

    Pure projection — returns new fact dicts, does not mutate ``facts``, makes no
    network call of its own.

    Routing: for a fact with ``label`` L, try resolvers whose ``.system`` is in
    ``routing.get(L)`` (default ``DEFAULT_ROUTING``), in that order; the first
    ``ConceptMatch`` with ``score >= min_score`` wins. A fact whose ``label`` is not
    in the routing map falls back to trying every resolver in the order passed.

    On a match: sets ``concept_code`` / ``concept_system`` / ``concept_display`` /
    ``terminology_version`` and **recomputes ``fact_id``** (it keys on the concept
    fields). ``skip_resolved`` leaves an already-coded fact untouched (idempotent).

    ``documents`` (``{doc_id: full text}``) enables a ±``context_chars`` window around
    each fact's span to be passed to ``resolver.resolve(value, context)``.
    """
    routing = DEFAULT_ROUTING if routing is None else routing
    by_system: dict[str, list[ConceptResolver]] = {}
    for r in resolvers:
        by_system.setdefault(r.system, []).append(r)

    out: list["ClinicalFact"] = []
    for src in facts:
        fact = dict(src)  # shallow copy
        if not (skip_resolved and fact.get("concept_code")):
            match = _resolve_one(fact, resolvers, by_system, routing, documents,
                                 context_chars, min_score)
            if match is not None:
                fact["concept_code"] = match["code"]
                fact["concept_system"] = match["system"]
                fact["concept_display"] = match["display"]
                fact["terminology_version"] = match["version"]
                fact["fact_id"] = compute_fact_id(
                    label=fact["label"],
                    value=fact["value"],
                    concept_code=fact["concept_code"],
                    concept_system=fact["concept_system"],
                    span=fact["span"],
                    manifest_id=fact["manifest_id"],
                )
        out.append(fact)  # type: ignore[arg-type]
    return out


def _resolve_one(fact, resolvers, by_system, routing, documents, context_chars, min_score):
    context = None
    span = fact.get("span")
    if documents is not None and span is not None:
        doc = documents.get(span["doc_id"])
        if doc is not None:
            lo = max(0, span["char_start"] - context_chars)
            hi = min(len(doc), span["char_end"] + context_chars)
            context = doc[lo:hi]

    systems = routing.get(fact["label"])
    ordered: list[ConceptResolver]
    if systems:
        ordered = [r for s in systems for r in by_system.get(s, [])]
    else:
        ordered = list(resolvers)  # unknown label: try everything, in order

    for resolver in ordered:
        m = resolver.resolve(fact["value"], context)
        if m is not None and m["score"] >= min_score:
            return m
    return None


# --------------------------------------------------------- SqliteLookupResolver


_SCHEMA = """
CREATE TABLE IF NOT EXISTS concepts (
    code TEXT NOT NULL,
    term TEXT NOT NULL,
    is_preferred INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_concepts_term ON concepts (term COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS ix_concepts_code ON concepts (code);
"""


def _norm_ws(s: str) -> str:
    return " ".join(s.split())


def load_vocab_sqlite(
    rows: Iterable[tuple[str, str, bool]],
    db_path: str,
    *,
    replace: bool = True,
) -> int:
    """Build a ``SqliteLookupResolver`` DB from ``(code, term, is_preferred)`` rows.

    Returns the number of rows written. ``replace=True`` drops any existing table.
    """
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        if replace:
            conn.execute("DROP TABLE IF EXISTS concepts")
        conn.executescript(_SCHEMA)
        n = 0
        batch = []
        for code, term, is_pref in rows:
            batch.append((str(code), str(term), 1 if is_pref else 0))
            if len(batch) >= 10_000:
                conn.executemany(
                    "INSERT INTO concepts (code, term, is_preferred) VALUES (?, ?, ?)", batch
                )
                n += len(batch)
                batch.clear()
        if batch:
            conn.executemany(
                "INSERT INTO concepts (code, term, is_preferred) VALUES (?, ?, ?)", batch
            )
            n += len(batch)
        conn.commit()
        return n
    finally:
        conn.close()


class SqliteLookupResolver:
    """Deterministic lexical resolver over a local vocabulary DB.

    Exact match (case-insensitive, whitespace-normalized) scores 1.0; otherwise a
    rapidfuzz ``token_sort_ratio`` over candidates sharing the leading token, gated by
    ``fuzzy_threshold`` (0-100). Ignores ``context``. Build the DB with
    ``load_vocab_sqlite`` or the ``python -m verichart.clinical.terminology load`` CLI.
    """

    def __init__(
        self,
        db_path: str,
        *,
        system: str,
        version: str,
        fuzzy_threshold: int = 88,
        fuzzy_candidates: int = 200,
    ):
        import sqlite3

        self.system = system
        self.version = version
        self._threshold = fuzzy_threshold
        self._cap = fuzzy_candidates
        self._conn = sqlite3.connect(
            f"file:{db_path}?mode=ro", uri=True, check_same_thread=False
        )

    def _preferred_display(self, code: str, fallback: str) -> str:
        row = self._conn.execute(
            "SELECT term FROM concepts WHERE code = ? ORDER BY is_preferred DESC LIMIT 1", (code,)
        ).fetchone()
        return row[0] if row else fallback

    def resolve(self, mention: str, context: str | None = None) -> ConceptMatch | None:
        q = _norm_ws(mention)
        if not q:
            return None

        # 1. exact / whitespace-normalized (COLLATE NOCASE index)
        row = self._conn.execute(
            "SELECT code, term FROM concepts WHERE term = ? COLLATE NOCASE "
            "ORDER BY is_preferred DESC LIMIT 1",
            (q,),
        ).fetchone()
        if row is None and q != mention:
            row = self._conn.execute(
                "SELECT code, term FROM concepts WHERE term = ? COLLATE NOCASE "
                "ORDER BY is_preferred DESC LIMIT 1",
                (mention,),
            ).fetchone()
        if row is not None:
            code = row[0]
            return ConceptMatch(
                code=code, display=self._preferred_display(code, row[1]),
                system=self.system, version=self.version, score=1.0,
            )

        # 2. fuzzy over candidates sharing the leading token
        from rapidfuzz import fuzz, process

        head = q.split(" ", 1)[0]
        cands = self._conn.execute(
            "SELECT code, term FROM concepts WHERE term LIKE ? COLLATE NOCASE LIMIT ?",
            (head + "%", self._cap),
        ).fetchall()
        if not cands:
            return None
        terms = [t for _, t in cands]
        best = process.extractOne(q, terms, scorer=fuzz.token_sort_ratio)
        if best is None or best[1] < self._threshold:
            return None
        code = cands[best[2]][0]
        return ConceptMatch(
            code=code, display=self._preferred_display(code, best[0]),
            system=self.system, version=self.version, score=best[1] / 100.0,
        )


# --------------------------------------------------------------- ScispacyResolver


class ScispacyResolver:
    """Resolver backed by a scispaCy entity linker (UMLS / RxNorm / MeSH / GO / HPO).

    Needs ``verichart[clinical]``. The knowledge base is a frozen snapshot scispaCy
    redistributes (UMLS 2020AA); ``.version`` records the scispaCy version + linker.

    scispaCy's ``umls`` / ``rxnorm`` / ``mesh`` linkers all identify concepts by **UMLS
    CUI** — the ``linker_name`` only scopes which concepts are candidates (use ``rxnorm``
    for drugs, ``mesh`` for MeSH-covered topics). So ``.system`` is ``"UMLS"`` for all
    three; ``go`` / ``hpo`` keep their own prefixed ids. For SNOMED / LOINC / ICD-10-CM
    *codes*, use ``SqliteLookupResolver`` over your own release. ``context`` is accepted
    but unused in this version.
    """

    _SYSTEMS = {"rxnorm": "UMLS", "umls": "UMLS", "mesh": "UMLS", "go": "GO", "hpo": "HPO"}

    def __init__(self, linker_name: str = "rxnorm", *, k: int = 10, threshold: float = 0.7):
        if linker_name not in self._SYSTEMS:
            raise ValueError(f"linker_name must be one of {sorted(self._SYSTEMS)}")
        try:
            import scispacy  # noqa: F401
            from scispacy.candidate_generation import CandidateGenerator
        except ImportError as e:  # pragma: no cover
            raise ImportError(
                "ScispacyResolver needs the clinical extra: pip install 'verichart[clinical]'"
            ) from e

        import scispacy as _sci

        self.system = self._SYSTEMS[linker_name]
        self.version = f"scispacy{_sci.__version__}-{linker_name}"
        self._cg = CandidateGenerator(name=linker_name)
        self._k = k
        self._threshold = threshold

    def resolve(self, mention: str, context: str | None = None) -> ConceptMatch | None:
        cands = self._cg([mention], self._k)[0]
        if not cands:
            return None
        best = max(cands, key=lambda c: max(c.similarities, default=0.0))
        score = max(best.similarities, default=0.0)
        if score < self._threshold:
            return None
        ent = self._cg.kb.cui_to_entity.get(best.concept_id)
        display = (
            ent.canonical_name if ent is not None
            else (best.aliases[0] if best.aliases else best.concept_id)
        )
        return ConceptMatch(
            code=best.concept_id, display=display,
            system=self.system, version=self.version, score=float(score),
        )


# ------------------------------------------------------------------------ CLI


def _cli(argv: list[str] | None = None) -> int:
    import argparse
    import csv

    parser = argparse.ArgumentParser(prog="python -m verichart.clinical.terminology")
    sub = parser.add_subparsers(dest="cmd", required=True)
    load = sub.add_parser("load", help="build a SqliteLookupResolver DB from a CSV/TSV")
    load.add_argument("--csv", required=True)
    load.add_argument("--db", required=True)
    load.add_argument("--code-col", required=True)
    load.add_argument("--term-col", required=True)
    load.add_argument("--preferred-col", default=None,
                      help="column whose truthy value marks the display term")
    load.add_argument("--delimiter", default=",")
    args = parser.parse_args(argv)

    with open(args.csv, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh, delimiter=args.delimiter)

        def _rows():
            for r in reader:
                code = r[args.code_col].strip()
                term = r[args.term_col].strip()
                if not code or not term:
                    continue
                pref = bool(r.get(args.preferred_col, "").strip()) if args.preferred_col else False
                if args.preferred_col:
                    pref = r[args.preferred_col].strip() not in ("", "0", "false", "False", "N")
                yield code, term, pref

        n = load_vocab_sqlite(_rows(), args.db)
    print(f"wrote {n} rows to {args.db}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
