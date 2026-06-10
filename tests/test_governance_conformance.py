"""AIP Governance Conformance Suite — enforces the AIP Governance Contract.

This single file is dropped UNCHANGED into the tests/ directory of any AIP
component (aip_brain, aip_loom, aip_codeforge).  It autodetects which repo it
is running in and applies that repo's conformance profile.  One contract,
conformed to by three repos: that is the platform thesis made executable.

DESIGN RULE (this suite obeys the contract it enforces):

  * AIP-G-02 forbids fake success.  So this suite NEVER asserts True to look
    green.  A check that cannot yet be performed mechanically is marked
    pytest.skip(...) with a precise reason telling the DEFINER exactly what to
    wire.  A skip is honest; a hollow pass is a lie.
  * Symmetrically, it avoids fake FAILURE: a structural check whose profile
    key is empty for this repo skips (we genuinely have not declared the rule)
    rather than failing.
  * AIP-G-11 is self-referential: test_g11 parses THIS file and fails if any
    invariant AIP-G-01..G-11 has no test, so an invariant can never be
    silently dropped from the suite.

USAGE:
    pytest tests/test_governance_conformance.py -v

The structural tier (G-02, G-06, G-07, G-09, G-10, G-11, plus structural parts
of G-03/G-04/G-05/G-08) runs on drop-in with no repo dependencies installed,
because it uses only the standard library.  The behavioral tier (does the
running system actually refuse to auto-approve, actually roll back, etc.) is
left as honest skips with wiring instructions, because it requires each repo's
domain APIs, which the DEFINER wires per repo.

Tune a profile by editing PROFILES[<package>] below.  Empty value => skip.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Per-repo conformance profiles.  Autodetected by which src package exists.
# A missing / empty key means "rule not declared for this repo" => the
# corresponding structural check skips honestly rather than failing.
# ---------------------------------------------------------------------------

PROFILES: dict[str, dict] = {
    # ---- aip_brain : knowledge engine / platform spine -------------------
    "aip": {
        "src": "src/aip",
        # AIP-G-07 layer discipline (foundation -> orchestration -> adapter)
        "import_rules": [
            ("foundation", ["aip.orchestration", "aip.adapter"],
             "foundation layer must not import upper layers"),
            ("orchestration", ["aip.adapter"],
             "orchestration layer must not import the adapter layer"),
        ],
        # AIP-G-07 surface isolation: GUI is an adapter, talks only via the API
        "surface_isolation": {
            "dir": "gui",
            "forbidden": ["aip.orchestration", "AipContainer"],
        },
        # AIP-G-02 honest-status sentinels that must exist somewhere in source
        "honest_status_sentinels": ["NOT_IMPLEMENTED", "NEEDS_CONFIGURATION", "DISABLED"],
        "status_file": "STATUS.md",
        "scaffold_keyword": "Scaffold",
        # AIP-G-04 lifecycle state names that must appear in source
        "lifecycle_states": ["GENERATED", "REVIEWED", "APPROVED"],
        # AIP-G-10 audit/trace markers
        "audit_markers": ["trace", "structlog"],
        # AIP-G-08 validation-first prompt discipline
        "validation_prompt_dir": "prompts",
        "validation_keywords": ["hypothesis", "validat", "assumpt", "unverified", "faithful"],
        # AIP-G-09 sovereignty: cloud hosts allowed only under adapter layer
        "external_host_patterns": ["api.openai.com", "openrouter.ai", "api.anthropic.com"],
        "external_allowed_path_substrings": ["adapter"],
        "external_forbidden": False,
        # AIP-G-06/07 acknowledged import violations (visible debt per AIP-G-02).
        # Orchestration pipelines import concrete adapter implementations through
        # function-local imports. Resolution: relocate concrete wiring to a
        # composition root so orchestration sees only Protocols.
        # Updated Chunk 6: reconciled with actual AST scan — removed stale
        # entries (embedding.ollama_client, vector._in_memory), added missing
        # entries (model_provider_proxy, codex/librarian, ingestion/corpus_ingest_pipeline,
        # channels/graph_channel).
        "acknowledged_import_violations": [
            "orchestration/ask_pipeline.py imports aip.adapter.embedding.factory",
            "orchestration/ask_pipeline.py imports aip.adapter.artifact_store_versioned",
            "orchestration/ask_pipeline.py imports aip.adapter.ecs_store_persistent",
            "orchestration/ask_pipeline.py imports aip.adapter.project.sqlite_project_store",
            "orchestration/ask_pipeline.py imports aip.adapter.vector.sqlite_vss_store",
            "orchestration/ask_pipeline.py imports aip.adapter.lexical.sqlite_fts5_store",
            "orchestration/ask_pipeline.py imports aip.adapter.event_store_queryable",
            "orchestration/ask_pipeline.py imports aip.adapter.model_slot_resolver",
            "orchestration/ask_pipeline.py imports aip.adapter.graph_store",
            "orchestration/ask_pipeline.py imports aip.adapter.corpus_turn_store",
            "orchestration/review_export_pipeline.py imports aip.adapter.canonical.sqlite_canonical_store",
            "orchestration/review_export_pipeline.py imports aip.adapter.artifact_store_versioned",
            "orchestration/review_export_pipeline.py imports aip.adapter.ecs_store_persistent",
            "orchestration/review_export_pipeline.py imports aip.adapter.project.sqlite_project_store",
            "orchestration/review_export_pipeline.py imports aip.adapter.event_store_queryable",
            "orchestration/ingestion/pipeline.py imports aip.adapter.vector.sqlite_vss_store",
            "orchestration/ingestion/pipeline.py imports aip.adapter.artifact_store_versioned",
            "orchestration/ingestion/pipeline.py imports aip.adapter.lexical.sqlite_fts5_store",
            "orchestration/ingestion/pipeline.py imports aip.adapter.event_store_queryable",
            "orchestration/ingestion/pipeline.py imports aip.adapter.embedding.factory",
            "orchestration/ingestion/corpus_ingest_pipeline.py imports aip.adapter.corpus_turn_store",
            "orchestration/ingestion/corpus_ingest_pipeline.py imports aip.adapter.event_store_queryable",
            "orchestration/embed_providers.py imports aip.adapter.embedding.factory",
            "orchestration/embed_providers.py imports aip.adapter.embedding.ollama_embed",
            "orchestration/embed_providers.py imports aip.adapter.embedding.openai_embed",
            "orchestration/model_provider_proxy.py imports aip.adapter.model_slot_resolver",
            "orchestration/artifact_lifecycle.py imports aip.adapter.artifact_store_versioned",
            "orchestration/artifact_lifecycle.py imports aip.adapter.ecs_store_persistent",
            "orchestration/artifact_lifecycle.py imports aip.adapter.event_store_queryable",
            "orchestration/artifact_lifecycle.py imports aip.adapter.project.sqlite_project_store",
            "orchestration/codex/librarian.py imports aip.adapter.codex.codex_store",
            "orchestration/codex/librarian.py imports aip.adapter.corpus_turn_store",
            "orchestration/channels/graph_channel.py imports aip.adapter.graph_store",
            "orchestration/actors/vigil.py imports aip.adapter.alerting",
            "orchestration/actors/sexton.py imports aip.adapter.alerting",
            "orchestration/actors/sexton.py imports aip.adapter.graph_store",
            "orchestration/actors/sexton.py imports aip.adapter.entity_alias_loader",
            "orchestration/actors/beast.py imports aip.adapter.graph_store",
            "orchestration/actors/beast.py imports aip.adapter.entity_alias_loader",
        ],
    },
    # ---- aip_loom : longform writing workbench ---------------------------
    "aip_loom": {
        "src": "src/aip_loom",
        "import_rules": [],  # single flat module; no inter-layer rules
        "surface_isolation": None,
        "honest_status_sentinels": [],
        "status_file": None,
        "scaffold_keyword": None,
        "lifecycle_states": [],  # review state lives on decision ledger entries
        # AIP-G-05 reversibility is loom's reference implementation
        "reversibility_modules": ["transaction", "lock", "fs"],
        "recovery_markers": ["RECOVERY", "rollback", "snapshot"],
        "audit_markers": ["session", "ledger"],
        "validation_prompt_dir": None,
        "validation_keywords": [],
        # AIP-G-09 sovereignty: loom must have NO cloud egress at all
        "external_host_patterns": ["api.openai.com", "openrouter.ai", "api.anthropic.com",
                                    "googleapis.com", "amazonaws.com"],
        "external_allowed_path_substrings": [],
        "external_forbidden": True,
    },
    # ---- aip_codeforge : spec-driven code generator ----------------------
    "codeforge": {
        "src": "src/codeforge",
        "import_rules": [
            ("models", ["codeforge.cli", "codeforge.dashboard"],
             "data models must not import surface layers"),
            ("intelligence", ["codeforge.cli", "codeforge.dashboard"],
             "intelligence plane must not import surface layers"),
        ],
        "surface_isolation": None,
        "honest_status_sentinels": ["NOT_IMPLEMENTED", "BLOCKED_HUMAN"],
        "status_file": None,
        "scaffold_keyword": None,
        # REQ-078: storage/writer.py is the SOLE issuer of BEGIN IMMEDIATE.
        # This is a concrete single-writer data-integrity guard (supports G-05).
        "single_writer": {
            "marker": "BEGIN IMMEDIATE",
            "allowed_path_substrings": ["storage/writer.py", "storage\\writer.py"],
        },
        "lifecycle_states": ["APPROVED_FOR_EXECUTION", "COMMITTED"],
        # AIP-G-03 provenance: every WorkUnit cites exact spec evidence (INV-03)
        "provenance_fields": ["source_requirement_ids"],
        "audit_markers": ["DecisionRecord", "ExecutionTrace", "cost"],
        "validation_prompt_dir": None,
        "validation_keywords": [],
        "external_host_patterns": ["api.openai.com", "openrouter.ai", "api.anthropic.com"],
        "external_allowed_path_substrings": ["providers", "models"],
        "external_forbidden": False,
    },
}

CLOUD_HOST_IGNORE = ("localhost", "127.0.0.1", "0.0.0.0", "example.com", "schemas")


# ---------------------------------------------------------------------------
# Repo discovery + helpers
# ---------------------------------------------------------------------------

def _repo_root() -> Path:
    """Walk up from this file to the directory that holds pyproject.toml."""
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        if (parent / "pyproject.toml").exists():
            return parent
    return here.parent


def _detect() -> tuple[str, dict, Path]:
    root = _repo_root()
    matches = [(name, prof) for name, prof in PROFILES.items()
               if (root / prof["src"]).is_dir()]
    if len(matches) != 1:
        pytest.skip(
            f"AIP governance: expected exactly one known src package under {root}, "
            f"found {[m[0] for m in matches]}. Add this repo to PROFILES."
        )
    name, prof = matches[0]
    return name, prof, root / prof["src"]


@pytest.fixture(scope="session")
def repo() -> tuple[str, dict, Path]:
    return _detect()


def _py_files(src_root: Path):
    return [p for p in src_root.rglob("*.py") if "__pycache__" not in p.parts]


def _is_type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def _imports(path: Path) -> set[str]:
    """Fully-qualified module names imported at runtime by a file.

    Function-local imports ARE included (they create real runtime coupling),
    but `if TYPE_CHECKING:` blocks are excluded (type-only, no runtime edge).
    """
    names: set[str] = set()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return names

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.If) and _is_type_checking(child.test):
                continue  # skip type-only import island
            if isinstance(child, ast.Import):
                names.update(a.name for a in child.names)
            elif isinstance(child, ast.ImportFrom) and child.module and child.level == 0:
                names.add(child.module)
            visit(child)

    visit(tree)
    return names


def _referenced(path: Path) -> tuple[set[str], set[str]]:
    """Return (imported_modules, referenced_symbols) for a file, from the AST.

    Docstrings, comments, and string literals are NOT included, so a file that
    merely *mentions* a forbidden name in prose is not flagged.
    """
    mods: set[str] = set()
    syms: set[str] = set()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return mods, syms
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                mods.add(a.name)
                syms.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            mods.add(node.module)
            for a in node.names:
                syms.add(a.asname or a.name)
        elif isinstance(node, ast.Name):
            syms.add(node.id)
        elif isinstance(node, ast.Attribute):
            syms.add(node.attr)
    return mods, syms


def _executes_marker(path: Path, marker: str) -> bool:
    """True iff the file passes `marker` as a string argument to .execute()/
    .executescript() — i.e. actually issues it, ignoring docstrings/comments."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return False
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("execute", "executescript")
        ):
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and marker in arg.value:
                    return True
    return False


def _grep(src_root: Path, needle: str) -> bool:
    needle_l = needle.lower()
    for p in _py_files(src_root):
        try:
            if needle_l in p.read_text(encoding="utf-8").lower():
                return True
        except UnicodeDecodeError:
            continue
    return False


# ===========================================================================
# AIP-G-01  DEFINER Authority
# ===========================================================================

def test_g01_definer_authority_behavioral(repo):
    """AIP-G-01: no artifact reaches a terminal/approved state without an
    explicit DEFINER action; autonomy is granted explicitly and is revocable."""
    pytest.skip(
        "AIP-G-01 behavioral check pending DEFINER wiring. Wire to this repo's "
        "approval API and assert: (1) creating an artifact leaves it in a "
        "non-approved state; (2) no code path transitions to APPROVED/COMMITTED "
        "without an explicit actor argument; (3) approve() rejects a missing or "
        "non-DEFINER actor."
    )


def test_g01_no_autoapprove_token_in_source(repo):
    """AIP-G-01 structural smell check: flag literal auto-approval shortcuts."""
    _, _, src = repo
    offenders: list[str] = []
    pat = re.compile(r"auto[_-]?approve\s*=\s*True", re.IGNORECASE)
    for p in _py_files(src):
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if pat.search(line) and "test" not in p.name.lower():
                offenders.append(f"{p.name}:{i}: {line.strip()}")
    assert not offenders, "AIP-G-01: auto-approval shortcut found:\n" + "\n".join(offenders)


# ===========================================================================
# AIP-G-02  No Fake Success
# ===========================================================================

def test_g02_honest_status_sentinels_exist(repo):
    """AIP-G-02: unconfigured/unimplemented paths return honest, distinguishable
    statuses rather than faking success."""
    _, prof, src = repo
    sentinels = prof.get("honest_status_sentinels") or []
    if not sentinels:
        pytest.skip("AIP-G-02: no honest-status sentinels declared for this repo.")
    present = [s for s in sentinels if _grep(src, s)]
    assert present, (
        "AIP-G-02: none of the honest-status sentinels "
        f"{sentinels} were found in source. Unconfigured paths must signal "
        "honestly, not return success."
    )


def test_g02_scaffold_is_disclosed(repo):
    """AIP-G-02: scaffold surfaces are disclosed in a status document."""
    _, prof, _ = repo
    status_file = prof.get("status_file")
    keyword = prof.get("scaffold_keyword")
    if not status_file or not keyword:
        pytest.skip("AIP-G-02: no status-disclosure file declared for this repo.")
    root = _repo_root()
    f = root / status_file
    assert f.exists(), f"AIP-G-02: expected disclosure file {status_file} is missing."
    assert keyword.lower() in f.read_text(encoding="utf-8").lower(), (
        f"AIP-G-02: {status_file} does not disclose '{keyword}'. "
        "Scaffold must be written down, not hidden."
    )


# ===========================================================================
# AIP-G-03  Provenance / Source-Grounding
# ===========================================================================

def test_g03_provenance_field_in_schema(repo):
    """AIP-G-03: generated artifacts carry a provenance/source field."""
    _, prof, src = repo
    fields = prof.get("provenance_fields") or []
    if not fields:
        pytest.skip("AIP-G-03: no provenance field declared for this repo "
                    "(set provenance_fields in the profile).")
    present = [f for f in fields if _grep(src, f)]
    assert present, (
        f"AIP-G-03: none of the provenance fields {fields} appear in source. "
        "Every generated artifact must cite the evidence it derives from."
    )


def test_g03_empty_provenance_rejected_behavioral(repo):
    """AIP-G-03 behavioral: an artifact/work unit with empty provenance is
    refused (cf. codeforge INV-03: empty source_requirement_ids is a block)."""
    pytest.skip(
        "AIP-G-03 behavioral check pending DEFINER wiring. Construct an artifact "
        "with empty sources and assert the synthesizer/validator rejects it."
    )


# ===========================================================================
# AIP-G-04  Governed Lifecycle
# ===========================================================================

def test_g04_lifecycle_states_present(repo):
    """AIP-G-04: explicit, recorded lifecycle states exist."""
    _, prof, src = repo
    states = prof.get("lifecycle_states") or []
    if not states:
        pytest.skip("AIP-G-04: no lifecycle states declared for this repo.")
    missing = [s for s in states if not _grep(src, s)]
    assert not missing, (
        f"AIP-G-04: declared lifecycle states not found in source: {missing}."
    )


def test_g04_terminal_state_not_reverted_behavioral(repo):
    """AIP-G-04 behavioral: a terminal state (COMMITTED/APPROVED) cannot be
    reverted without a DEFINER override recorded as a DecisionRecord."""
    pytest.skip(
        "AIP-G-04 behavioral check pending DEFINER wiring. Drive an artifact to "
        "its terminal state and assert revert() raises unless given an explicit "
        "DEFINER override that writes a DecisionRecord/trace event."
    )


# ===========================================================================
# AIP-G-05  Reversibility / No Silent Data Loss
# ===========================================================================

def test_g05_reversibility_machinery_present(repo):
    """AIP-G-05: file/state mutations are snapshot/rollback/recovery capable."""
    _, prof, src = repo
    modules = prof.get("reversibility_modules") or []
    markers = prof.get("recovery_markers") or []
    if not modules and not markers:
        pytest.skip("AIP-G-05: no reversibility modules/markers declared for this repo.")
    have_module = any(_grep(src, m) for m in modules) if modules else True
    have_marker = any(_grep(src, m) for m in markers) if markers else True
    assert have_module and have_marker, (
        "AIP-G-05: reversibility machinery incomplete. "
        f"modules={modules} markers={markers}; "
        "mutations must snapshot, validate, roll back, and leave recovery instructions."
    )


def test_g05_single_writer_invariant(repo):
    """AIP-G-05 / codeforge REQ-078: the transactional write marker appears in
    exactly one sanctioned module (single-writer integrity guard)."""
    _, prof, src = repo
    cfg = prof.get("single_writer")
    if not cfg:
        pytest.skip("AIP-G-05: no single-writer invariant declared for this repo.")
    marker = cfg["marker"]
    allowed = cfg["allowed_path_substrings"]
    offenders = [
        str(p) for p in _py_files(src)
        if _executes_marker(p, marker) and not any(a in str(p) for a in allowed)
    ]
    assert not offenders, (
        f"AIP-G-05: '{marker}' issued outside the sanctioned writer "
        f"({allowed}): {offenders}"
    )


def test_g05_failed_apply_rolls_back_behavioral(repo):
    """AIP-G-05 behavioral: a mutation that fails mid-apply restores prior state."""
    pytest.skip(
        "AIP-G-05 behavioral check pending DEFINER wiring. Force a failure inside "
        "an apply/reconcile transaction and assert the prior file/db state is "
        "restored byte-for-byte (loom already has test_transaction.py to extend)."
    )


# ===========================================================================
# AIP-G-06  Separation of Orchestration and Judgment
# AIP-G-07  Layer Discipline
# (shared AST import-boundary engine)
# ===========================================================================

def _import_boundary_offenders(src_root: Path, subdir: str, forbidden: list[str]) -> list[str]:
    offenders: list[str] = []
    target = f"{subdir}"
    for p in _py_files(src_root):
        parts = p.relative_to(src_root).parts
        if target not in parts:
            continue
        for mod in _imports(p):
            if any(mod == f or mod.startswith(f + ".") for f in forbidden):
                offenders.append(f"{p.relative_to(src_root)} imports {mod}")
    return offenders


def test_g06_g07_import_boundaries(repo):
    """AIP-G-06 + AIP-G-07: declared layers/planes do not import across the
    forbidden direction.  Orchestration must not make planning judgments and
    lower layers must not import higher ones.

    Adopting this on an existing codebase uses a ratchet: a violation must be
    either fixed, or explicitly listed in the profile's
    'acknowledged_import_violations' (acknowledged debt is visible debt, per
    AIP-G-02).  It is never silently ignored.
    """
    _, prof, src = repo
    rules = prof.get("import_rules") or []
    if not rules:
        pytest.skip("AIP-G-06/07: no import-boundary rules declared for this repo.")
    acknowledged = set(prof.get("acknowledged_import_violations") or [])
    failures: list[str] = []
    for subdir, forbidden, reason in rules:
        offenders = [o for o in _import_boundary_offenders(src, subdir, forbidden)
                     if o not in acknowledged]
        if offenders:
            failures.append(f"[{reason}]\n  " + "\n  ".join(offenders))
    assert not failures, (
        "AIP-G-06/07 boundary violations (fix, or add the exact line to "
        "'acknowledged_import_violations' to record it as known debt):\n"
        + "\n".join(failures)
    )


def test_g07_surface_isolation(repo):
    """AIP-G-07: a surface (CLI/GUI/API) is an adapter and must not reach around
    the contract into orchestration internals."""
    _, prof, _ = repo
    cfg = prof.get("surface_isolation")
    if not cfg:
        pytest.skip("AIP-G-07: no surface-isolation rule declared for this repo.")
    root = _repo_root()
    surface_dir = root / cfg["dir"]
    if not surface_dir.is_dir():
        pytest.skip(f"AIP-G-07: surface dir '{cfg['dir']}' not present.")
    forbidden = cfg["forbidden"]
    offenders: list[str] = []
    for p in surface_dir.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        mods, syms = _referenced(p)
        for f in forbidden:
            if "." in f:  # module prefix, e.g. aip.orchestration
                if any(m == f or m.startswith(f + ".") for m in mods):
                    offenders.append(f"{p.name} imports forbidden module '{f}'")
            else:  # bare symbol, e.g. AipContainer
                if f in syms:
                    offenders.append(f"{p.name} references forbidden symbol '{f}'")
    assert not offenders, (
        "AIP-G-07: surface reaches around the API boundary:\n  " + "\n  ".join(offenders)
    )


# ===========================================================================
# AIP-G-08  Validation-First Output
# ===========================================================================

def test_g08_synthesis_prompts_enforce_validation(repo):
    """AIP-G-08: synthesis prompts instruct the model to distinguish hypothesis
    from validated and to surface validation gates."""
    _, prof, _ = repo
    pdir = prof.get("validation_prompt_dir")
    keywords = prof.get("validation_keywords") or []
    if not pdir or not keywords:
        pytest.skip("AIP-G-08: no validation-prompt discipline declared for this repo.")
    root = _repo_root()
    prompt_dir = root / pdir
    if not prompt_dir.is_dir():
        pytest.skip(f"AIP-G-08: prompt dir '{pdir}' not present.")
    blob = "\n".join(
        f.read_text(encoding="utf-8").lower()
        for f in prompt_dir.rglob("*.md")
    )
    hits = [k for k in keywords if k.lower() in blob]
    assert hits, (
        f"AIP-G-08: prompts in '{pdir}' contain none of {keywords}. Synthesis "
        "output must separate hypothesis from validated and flag validation gates."
    )


def test_g08_output_labels_hypothesis_behavioral(repo):
    """AIP-G-08 behavioral: a synthesized answer tags unvalidated claims."""
    pytest.skip(
        "AIP-G-08 behavioral check pending DEFINER wiring. Run a synthesis with a "
        "speculative question and assert the structured output carries an explicit "
        "hypothesis/validated distinction (not prose-only)."
    )


# ===========================================================================
# AIP-G-09  Local-First Sovereignty
# ===========================================================================

def test_g09_no_unsanctioned_cloud_egress(repo):
    """AIP-G-09: cloud endpoints appear only in sanctioned adapter/provider
    modules (or nowhere, for fully-local components)."""
    _, prof, src = repo
    patterns = prof.get("external_host_patterns") or []
    if not patterns:
        pytest.skip("AIP-G-09: no external-host patterns declared for this repo.")
    forbidden_everywhere = prof.get("external_forbidden", False)
    allowed = prof.get("external_allowed_path_substrings") or []
    offenders: list[str] = []
    for p in _py_files(src):
        rel = str(p)
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for host in patterns:
            if host in text and not any(ig in text and ig in host for ig in CLOUD_HOST_IGNORE):
                if forbidden_everywhere:
                    offenders.append(f"{rel}: contains cloud host '{host}'")
                elif not any(a in rel for a in allowed):
                    offenders.append(f"{rel}: cloud host '{host}' outside sanctioned path {allowed}")
    if forbidden_everywhere:
        assert not offenders, (
            "AIP-G-09: a fully-local component must have NO cloud egress:\n  "
            + "\n  ".join(offenders)
        )
    else:
        assert not offenders, (
            "AIP-G-09: cloud egress must be confined to sanctioned adapters:\n  "
            + "\n  ".join(offenders)
        )


# ===========================================================================
# AIP-G-10  Auditability
# ===========================================================================

def test_g10_audit_trail_surface_exists(repo):
    """AIP-G-10: consequential actions are recorded in a durable audit surface."""
    _, prof, src = repo
    markers = prof.get("audit_markers") or []
    if not markers:
        pytest.skip("AIP-G-10: no audit markers declared for this repo.")
    present = [m for m in markers if _grep(src, m)]
    assert present, (
        f"AIP-G-10: none of the audit markers {markers} found in source. The "
        "system must be able to answer why an artifact looks the way it does."
    )


# ===========================================================================
# AIP-G-11  Conformance is Tested (self-referential)
# ===========================================================================

def test_g11_every_invariant_has_a_test():
    """AIP-G-11: this suite covers every invariant AIP-G-01..G-11. An invariant
    can never be silently dropped, because this test parses its own source."""
    source = Path(__file__).read_text(encoding="utf-8")
    covered = {int(m) for m in re.findall(r"def test_g0?(\d+)_", source)}
    expected = set(range(1, 12))
    missing = expected - covered
    assert not missing, (
        f"AIP-G-11: invariants with no conformance test: "
        f"{sorted(f'AIP-G-{n:02d}' for n in missing)}"
    )
    referenced = {int(m) for m in re.findall(r"AIP-G-0?(\d+)", source)}
    assert expected <= referenced, (
        "AIP-G-11: some invariant IDs are not referenced by name in this suite: "
        f"{sorted(f'AIP-G-{n:02d}' for n in (expected - referenced))}"
    )
