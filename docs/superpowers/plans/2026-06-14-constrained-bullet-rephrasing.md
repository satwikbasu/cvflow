# Constrained Bullet Rephrasing — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the LLM reword experience/project résumé bullets to align with the target job, while
a deterministic guard rejects any reword that introduces a new fact (number, tool, skill, claim);
rejected bullets fall back to the original master wording.

**Architecture:** All logic lives in `src/cvflow/resume/__init__.py`. A new rephrase pass in
`ResumeTailor.plan` calls the tailoring LLM for reworded bullets, then a no-network
vocabulary+numbers guard accepts or discards each. `render`/`tailored_document` substitute accepted
rewords into the experience + selected-project sections (everything else verbatim); `diff` shows a
word-level before/after for the human gate. A `resume.rephrase` config toggle (default true) gates
the whole pass.

**Tech Stack:** Python 3.11, Cerebras `gpt-oss-120b` (existing tailoring provider), Tectonic,
pytest + ruff + mypy --strict.

**Spec:** `docs/superpowers/specs/2026-06-14-constrained-bullet-rephrasing-design.md`. Scope:
**private repo only.** Standing rules: TDD, Conventional Commits, new config keys
optional-with-default, gate invariant untouched, never fail silently. The fact guard + the human
review gate together enforce CLAUDE.md invariant 2 (no fabricated facts).

---

## File structure

| File | Responsibility | Task |
|---|---|---|
| `src/cvflow/resume/__init__.py` | tokenizer + `STOPWORDS` + vocab + `_guard_ok` + redefined `assert_no_new_facts` | 1 |
| `src/cvflow/resume/__init__.py` | `\resumeItem` extract/substitute helpers | 2 |
| `src/cvflow/resume/__init__.py` | `TailoringPlan.rephrased` + rephrase pass in `plan` + prompt | 3 |
| `src/cvflow/resume/__init__.py` | `render`/`tailored_document` substitution + `diff` before/after | 4 |
| `src/cvflow/config.py`, `config.example.yaml`, `src/cvflow/mcp/tools.py` | `resume.rephrase` toggle + `build_tools` wiring (fact_corpus + flag) | 5 |
| `tests/test_resume.py`, `tests/test_config.py` | tests for all of the above | 1–5 |

---

### Task 1: Fact machinery — tokenizer, STOPWORDS, vocab, guard, redefined `assert_no_new_facts`

**Files:**
- Modify: `src/cvflow/resume/__init__.py`
- Test: `tests/test_resume.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_resume.py`:

```python
def test_guard_accepts_in_vocab_reword() -> None:
    m = _master()
    t = ResumeTailor(_FakeProvider("{}"), m, fact_corpus="kubernetes containers postgres")
    # original mentions "container"; reword uses in-vocab words only, no new numbers
    assert t._guard_ok("Built container tooling", "Built Kubernetes containers") is True


def test_guard_rejects_new_number() -> None:
    t = ResumeTailor(_FakeProvider("{}"), _master(), fact_corpus="")
    assert t._guard_ok("Built APIs for tooling", "Built APIs handling 1000000 requests") is False


def test_guard_rejects_out_of_vocab_word() -> None:
    t = ResumeTailor(_FakeProvider("{}"), _master(), fact_corpus="python flask")
    # "kubernetes" is in neither the original, the master content, nor the fact_corpus
    assert t._guard_ok("Built python flask APIs", "Built kubernetes python flask APIs") is False


def test_guard_stopwords_and_plurals_do_not_trip() -> None:
    t = ResumeTailor(_FakeProvider("{}"), _master(), fact_corpus="container pipeline")
    # plural "containers"/"pipelines" normalize to the singular in vocab; stopwords ignored
    assert t._guard_ok("the container", "managed the containers and pipelines") is True


def test_assert_no_new_facts_vocab_based() -> None:
    # _master() section/project content: EXP, P, SK, IPSEC, CRUD
    t = ResumeTailor(_FakeProvider("{}"), _master(), fact_corpus="")
    t.assert_no_new_facts("EXP SK")  # all words in vocab -> ok
    import pytest
    with pytest.raises(TailoringError):
        t.assert_no_new_facts("EXP kubernetes")  # out-of-vocab word
    with pytest.raises(TailoringError):
        t.assert_no_new_facts("EXP 4242")  # number not in master
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_resume.py -k "guard or vocab_based" -v`
Expected: FAIL (`ResumeTailor.__init__` has no `fact_corpus`; `_guard_ok` undefined).

- [ ] **Step 3: Add the tokenizer + STOPWORDS (module level)**

In `src/cvflow/resume/__init__.py`, after the existing regex constants (near line 43), add:

```python
_WORD_RE = re.compile(r"[A-Za-z]+")
_NUM_RE = re.compile(r"\d[\d,]*")
_LATEX_CMD_RE = re.compile(r"\\[A-Za-z]+")


def _norm(word: str) -> str:
    """Lowercase + strip a common plural/tense suffix so 'containers' matches 'container'."""
    w = word.lower()
    for suf in ("ing", "ed", "es", "s", "d"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[: -len(suf)]
    return w


# Function words + generic résumé verbs/adjectives. These never trip the fact guard — only
# content words (nouns, tools, skills, numbers) must trace to the candidate's own data. NOTE:
# normalized with the SAME _norm as tokens, so e.g. "managed"->"manag" matches at compare time.
STOPWORDS: frozenset[str] = frozenset(
    _norm(w)
    for w in """
    a an the and or but for to of in on at by with from into as is are was were be been being
    this that these those it its their our your his her my we you they i he she them us
    using used use via per across over under between within without about above below
    built build building designed design develop developed developing led lead leading
    created create creating made make making implemented implement implementing
    integrated integrate integrating improved improve improving managed manage managing
    enabled enable enabling added add adding set setting up out leveraged leverage leveraging
    deployed deploy deploying maintained maintain scaling scaled scalable robust custom
    real time end full multi high low new own based around alongside top layer layers
    work working hands on while which who whose where when then so than more most less
    """.split()
)


def _tokens(text: str) -> set[str]:
    """Normalized content-word tokens from prose or LaTeX (commands/markup stripped)."""
    stripped = _LATEX_CMD_RE.sub(" ", text)
    return {_norm(w) for w in _WORD_RE.findall(stripped)}


def _numbers(text: str) -> set[str]:
    """Digit-runs (commas removed), e.g. '500K'->'500', '10,000'->'10000'."""
    return {m.replace(",", "") for m in _NUM_RE.findall(text)}
```

(`re` is already imported at the top of the module.)

- [ ] **Step 4: Extend `ResumeTailor.__init__` to build the vocab**

Replace the current `__init__` (lines ~138-143):

```python
    def __init__(
        self,
        provider: _Provider,
        master: Master,
        *,
        min_projects: int = MIN_PROJECTS,
        fact_corpus: str = "",
        rephrase: bool = True,
    ) -> None:
        self._provider = provider
        self._master = master
        self._min_projects = min_projects
        self._rephrase = rephrase
        corpus = "\n".join(
            [s.content for s in master.sections.values()]
            + [p.content for p in master.projects]
            + [fact_corpus]
        )
        self._allowed_vocab = _tokens(corpus)
        self._master_numbers = _numbers(corpus)
```

- [ ] **Step 5: Add `_guard_ok` and redefine `assert_no_new_facts`**

Add `_guard_ok` to `ResumeTailor`:

```python
    def _guard_ok(self, original: str, reword: str) -> bool:
        """True if ``reword`` adds no fact: no number absent from ``original``, and every
        non-stopword word is in the allowed vocabulary (master + profile + known skills) OR in
        the original bullet itself (keeping an original word is always fine)."""
        if not _numbers(reword) <= _numbers(original):
            return False
        allowed = self._allowed_vocab | _tokens(original)
        content = {t for t in _tokens(reword) if t not in STOPWORDS}
        return content <= allowed
```

Replace the existing `assert_no_new_facts` (lines ~216-227) with the vocab+number form:

```python
    def assert_no_new_facts(self, tailored_tex: str) -> None:
        """Raise if the rendered tex introduces a number not in the master or a content word
        outside the allowed vocabulary (master + profile + known skills). This is the
        deterministic expression of CLAUDE.md invariant 2 for the (possibly reworded) resume."""
        extra_numbers = _numbers(tailored_tex) - self._master_numbers
        bad_words = {
            t for t in _tokens(tailored_tex) if t not in STOPWORDS
        } - self._allowed_vocab
        if extra_numbers or bad_words:
            raise TailoringError(
                f"tailored resume adds facts not in master: "
                f"numbers={sorted(extra_numbers)[:3]} words={sorted(bad_words)[:3]}"
            )
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_resume.py -v`
Expected: PASS (new guard/vocab tests + the existing resume tests, including
`test_assert_no_new_facts_passes_for_subset_and_fails_for_addition`).

- [ ] **Step 7: Lint + types**

Run: `.venv/bin/ruff check src/cvflow/resume/__init__.py tests/test_resume.py && .venv/bin/python -m mypy src`
Expected: clean.

- [ ] **Step 8: Commit**

```bash
git add src/cvflow/resume/__init__.py tests/test_resume.py
git commit -m "feat(resume): fact guard (vocab + numbers) + vocab-based assert_no_new_facts"
```

---

### Task 2: `\resumeItem` extract + substitute helpers

**Files:**
- Modify: `src/cvflow/resume/__init__.py`
- Test: `tests/test_resume.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_resume.py`:

```python
from cvflow.resume import _resume_item_bodies, _substitute_bullets


def test_resume_item_bodies_extracts_inner_text() -> None:
    tex = "x\n  \\resumeItem{Built APIs}\n  \\resumeItem{Designed \\textbf{Docker} swarm}\n"
    bodies = [b for _, _, b in _resume_item_bodies(tex)]
    assert bodies == ["Built APIs", "Designed \\textbf{Docker} swarm"]


def test_substitute_bullets_replaces_only_mapped_bodies() -> None:
    tex = "\\resumeItem{Built APIs}\n\\resumeItem{Kept as-is}\n"
    out = _substitute_bullets(tex, {"Built APIs": "Built REST microservices"})
    assert "\\resumeItem{Built REST microservices}" in out
    assert "\\resumeItem{Kept as-is}" in out  # unmapped bodies untouched
    assert "Built APIs" not in out
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_resume.py -k "resume_item_bodies or substitute_bullets" -v`
Expected: FAIL (helpers undefined).

- [ ] **Step 3: Implement the helpers (module level)**

Add near the other module helpers (after `_numbers`):

```python
_RESUME_ITEM_OPEN = "\\resumeItem{"


def _resume_item_bodies(text: str) -> list[tuple[int, int, str]]:
    """Find each ``\\resumeItem{...}`` and return (body_start, body_end, body) with brace
    balancing so nested ``{...}`` (e.g. ``\\textbf{}``) is handled. ``body_end`` is the index
    of the matching close brace (exclusive of it)."""
    spans: list[tuple[int, int, str]] = []
    i = 0
    while True:
        j = text.find(_RESUME_ITEM_OPEN, i)
        if j == -1:
            break
        start = j + len(_RESUME_ITEM_OPEN)
        depth = 1
        k = start
        while k < len(text) and depth:
            if text[k] == "{":
                depth += 1
            elif text[k] == "}":
                depth -= 1
            k += 1
        end = k - 1  # index of the matching close brace
        spans.append((start, end, text[start:end]))
        i = k
    return spans


def _substitute_bullets(text: str, rephrased: dict[str, str]) -> str:
    """Replace each ``\\resumeItem`` body with ``rephrased[body]`` when present (else leave it)."""
    out: list[str] = []
    last = 0
    for start, end, body in _resume_item_bodies(text):
        out.append(text[last:start])
        out.append(rephrased.get(body, body))
        last = end
    out.append(text[last:])
    return "".join(out)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_resume.py -k "resume_item_bodies or substitute_bullets" -v`
Expected: PASS.

- [ ] **Step 5: Lint + types + commit**

```bash
.venv/bin/ruff check src/cvflow/resume/__init__.py tests/test_resume.py && .venv/bin/python -m mypy src
git add src/cvflow/resume/__init__.py tests/test_resume.py
git commit -m "feat(resume): brace-balanced \\resumeItem extract + substitute helpers"
```

---

### Task 3: `TailoringPlan.rephrased` + the rephrase pass in `plan`

**Files:**
- Modify: `src/cvflow/resume/__init__.py`
- Test: `tests/test_resume.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_resume.py`:

```python
def _order_json() -> str:
    return json.dumps(
        {"section_order": ["experience", "projects", "skills"],
         "selected_project_ids": ["crudbot"], "diff_narration": "x"}
    )


class _TwoCallProvider:
    """First generate() -> order JSON; second -> rephrase JSON."""
    def __init__(self, order_payload: str, rephrase_payload: str) -> None:
        self._payloads = [order_payload, rephrase_payload]
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self._payloads[min(len(self.prompts) - 1, len(self._payloads) - 1)]


def _exp_master() -> Master:
    # one experience bullet we can rephrase, one project with a bullet
    return Master(
        root=Path("/nonexistent"),
        section_order=["experience", "projects"],
        sections={
            "experience": Section(
                "experience", "\\resumeItem{Built python flask APIs for tooling}"
            ),
            "projects": Section("projects", "P"),
        },
        projects=[Project("crudbot", "\\resumeItem{Built a CRUD backend with postgres}")],
    )


def test_plan_keeps_clean_reword_rejects_fabrication() -> None:
    rephrase = json.dumps({"rewrites": [
        "Built python flask REST APIs for tooling",   # clean: all words in vocab
        "Built a CRUD backend with postgres at 1000 rps",  # fabricates a number -> rejected
    ]})
    m = _exp_master()
    t = ResumeTailor(
        _TwoCallProvider(_order_json(), rephrase),
        m, fact_corpus="rest microservices", rephrase=True,
    )
    plan = t.plan(_jd())
    assert plan.rephrased["Built python flask APIs for tooling"] == \
        "Built python flask REST APIs for tooling"
    # the fabricated-number reword was rejected -> not in the map (original kept)
    assert "Built a CRUD backend with postgres" not in plan.rephrased


def test_plan_rephrase_disabled_makes_no_second_call() -> None:
    m = _exp_master()
    p = _TwoCallProvider(_order_json(), "{}")
    t = ResumeTailor(p, m, rephrase=False)
    plan = t.plan(_jd())
    assert plan.rephrased == {}
    assert len(p.prompts) == 1  # only the ordering call


def test_plan_rephrase_bad_json_falls_back_silently() -> None:
    m = _exp_master()
    t = ResumeTailor(_TwoCallProvider(_order_json(), "not json"), m, rephrase=True)
    plan = t.plan(_jd())  # must not raise
    assert plan.rephrased == {}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_resume.py -k "rephrase or reword or fabrication" -v`
Expected: FAIL (`TailoringPlan` has no `rephrased`; rephrase pass not implemented).

- [ ] **Step 3: Add `rephrased` to `TailoringPlan`**

Update the dataclass (and its import). Change the top import line
`from dataclasses import dataclass` to:

```python
from dataclasses import dataclass, field
```

Update `TailoringPlan`:

```python
@dataclass(frozen=True)
class TailoringPlan:
    section_order: list[str]
    selected_project_ids: list[str]
    diff_narration: str
    rephrased: dict[str, str] = field(default_factory=dict)
```

- [ ] **Step 4: Add the rephrase prompt (module level)**

After `_PLAN_PROMPT`:

```python
_REPHRASE_PROMPT = (
    "Reword each résumé bullet below to align with the target job's language and emphasis. "
    "HARD RULE: do NOT add, remove, or invent any fact — no new tools, skills, numbers, "
    "employers, metrics, or claims. Only rephrase what each bullet already states; keep every "
    "concrete detail. Return ONLY a JSON object {\"rewrites\": [...]} — a list of the reworded "
    "bullets in the SAME ORDER as the input, one string per input bullet.\n\n"
    "## Target job\nrequired_skills: {req}\npreferred_quals: {pref}\n"
    "User feedback to incorporate (optional): {feedback}\n\n"
    "## Bullets (in order)\n{bullets}\n"
)
```

- [ ] **Step 5: Add the rephrase pass to `plan`**

In `plan`, replace the final `return TailoringPlan(...)` (lines ~193-197) with:

```python
        rephrased = self._rephrase_bullets(picks, jd, feedback) if self._rephrase else {}

        return TailoringPlan(
            section_order=order,
            selected_project_ids=picks,
            diff_narration=str(d.get("diff_narration", "")),
            rephrased=rephrased,
        )

    def _eligible_bullets(self, selected_pids: list[str]) -> list[str]:
        """Experience bullets + the selected projects' bullets, in order."""
        text = self._master.sections.get("experience")
        bodies: list[str] = []
        if text is not None:
            bodies += [b for _, _, b in _resume_item_bodies(text.content)]
        by_id = {p.project_id: p for p in self._master.projects}
        for pid in selected_pids:
            proj = by_id.get(pid)
            if proj is not None:
                bodies += [b for _, _, b in _resume_item_bodies(proj.content)]
        return bodies

    def _rephrase_bullets(
        self, selected_pids: list[str], jd: JDAnalysis, feedback: str | None
    ) -> dict[str, str]:
        """LLM-reword the eligible bullets; keep only rewords that pass the fact guard. Any
        failure (no bullets, bad JSON, provider error) falls back to originals — never raises."""
        bullets = self._eligible_bullets(selected_pids)
        if not bullets:
            return {}
        prompt = _REPHRASE_PROMPT.format(
            req=", ".join(jd.required_skills),
            pref=", ".join(jd.preferred_quals),
            feedback=feedback or "(none)",
            bullets="\n".join(f"{i}. {b}" for i, b in enumerate(bullets)),
        )
        try:
            raw = self._provider.generate(prompt)
            rewrites = json.loads(_strip_code_fence(raw)).get("rewrites", [])
        except (ValueError, json.JSONDecodeError, AttributeError, KeyError, TypeError) as exc:
            logger.warning("rephrase pass failed; keeping original bullets: %s", exc)
            return {}
        out: dict[str, str] = {}
        for original, reword in zip(bullets, rewrites, strict=False):
            if isinstance(reword, str) and reword.strip() and reword != original \
                    and self._guard_ok(original, reword):
                out[original] = reword
        return out
```

Add a module logger near the top of the file if absent (after the imports):

```python
import logging

logger = logging.getLogger("cvflow.resume")
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_resume.py -v`
Expected: PASS (the new rephrase tests + all existing resume tests — the existing single-call
`_FakeProvider` tests still pass because a second generate() returns the order JSON, whose
`.get("rewrites", [])` is empty → no rewrites).

- [ ] **Step 7: Lint + types + commit**

```bash
.venv/bin/ruff check src/cvflow/resume/__init__.py tests/test_resume.py && .venv/bin/python -m mypy src
git add src/cvflow/resume/__init__.py tests/test_resume.py
git commit -m "feat(resume): LLM rephrase pass in plan, guarded + graceful, on TailoringPlan.rephrased"
```

---

### Task 4: Apply rewrites in `render`/`tailored_document` + before/after `diff`

**Files:**
- Modify: `src/cvflow/resume/__init__.py`
- Test: `tests/test_resume.py`, `tests/test_resume_tailored_compile.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_resume.py`:

```python
def test_render_applies_accepted_rewrites() -> None:
    m = _exp_master()
    plan = TailoringPlan(
        section_order=["experience", "projects"],
        selected_project_ids=["crudbot"],
        diff_narration="",
        rephrased={"Built python flask APIs for tooling": "Built python flask REST APIs"},
    )
    t = ResumeTailor(_FakeProvider("{}"), m)
    out = t.render(plan)
    assert "Built python flask REST APIs" in out
    assert "Built python flask APIs for tooling" not in out


def test_diff_shows_before_after_for_reworded_bullets() -> None:
    m = _exp_master()
    plan = TailoringPlan(
        section_order=["experience"], selected_project_ids=[], diff_narration="why",
        rephrased={"Built python flask APIs for tooling": "Built python flask REST APIs"},
    )
    out = ResumeTailor(_FakeProvider("{}"), m).diff(plan)
    assert "- Built python flask APIs for tooling" in out
    assert "+ Built python flask REST APIs" in out
    assert "Section order:" in out and "why" in out
```

Update the existing `test_tailored_document_reorders_and_selects_projects` in
`tests/test_resume_tailored_compile.py` — projects/experience are now INLINED (not `\input`),
so assert on real content, not the pid-in-path. Replace its body with:

```python
def test_tailored_document_reorders_and_selects_projects() -> None:
    master = parse_master(ROOT)
    tailor = ResumeTailor(_Stub(), master)
    order = list(reversed(master.section_order))
    pid = master.projects[0].project_id
    plan = TailoringPlan(section_order=order, selected_project_ids=[pid], diff_narration="")
    doc = tailor.tailored_document(plan)
    assert "\\begin{document}" in doc and "\\end{document}" in doc
    # the selected project's CONTENT is inlined; the unselected one is absent
    selected_snippet = master.projects[0].content.split("\n", 1)[0][:30]
    assert selected_snippet in doc
    if len(master.projects) > 1:
        other_snippet = master.projects[1].content.split("\n", 1)[0][:30]
        assert other_snippet not in doc
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_resume.py -k "render_applies or diff_shows" tests/test_resume_tailored_compile.py::test_tailored_document_reorders_and_selects_projects -v`
Expected: FAIL (render/diff/tailored_document don't apply rewrites yet).

- [ ] **Step 3: Apply rewrites in `render`**

Replace `render` (lines ~203-210):

```python
    def render(self, plan: TailoringPlan) -> str:
        parts: list[str] = []
        for name in plan.section_order:
            if name == "projects":
                raw = self._render_projects(plan.selected_project_ids)
            else:
                raw = self._master.sections[name].content
            parts.append(_substitute_bullets(raw, plan.rephrased))
        return "\n".join(parts)
```

- [ ] **Step 4: Inline + substitute experience and projects in `tailored_document`**

In `tailored_document`, replace the section loop (the `for name in plan.section_order:` block,
lines ~253-261) with:

```python
        by_id = {p.project_id: p for p in self._master.projects}
        for name in plan.section_order:
            if name == "projects":
                body_lines.append("\\section{Projects}")
                body_lines.append("    \\resumeSubHeadingListStart")
                for pid in plan.selected_project_ids:
                    proj = by_id.get(pid)
                    if proj is not None:
                        body_lines.append(_substitute_bullets(proj.content, plan.rephrased))
                body_lines.append("    \\resumeSubHeadingListEnd")
            elif name == "experience":
                body_lines.append(
                    _substitute_bullets(self._master.sections["experience"].content, plan.rephrased)
                )
            else:
                body_lines.append(f"\\input{{sections/{name}.tex}}")
```

(Experience + projects are inlined so the reworded bullets reach the compiled PDF; education,
skills, and certifications stay `\input` verbatim. When `rephrased` is empty the inlined content is
identical to what `\input` produced.)

- [ ] **Step 5: Lead `diff` with the before/after**

Replace `diff` (lines ~229-237):

```python
    def diff(self, plan: TailoringPlan) -> str:
        lines: list[str] = []
        if plan.rephrased:
            lines.append("Reworded bullets:")
            for original, reword in plan.rephrased.items():
                lines.append(f"  - {original}")
                lines.append(f"  + {reword}")
            lines.append("")
        lines.append("Section order: " + " → ".join(plan.section_order))
        lines.append(
            "Projects shown: " + (", ".join(plan.selected_project_ids) or "(none)")
        )
        if plan.diff_narration:
            lines.append("")
            lines.append(plan.diff_narration)
        return "\n".join(lines)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_resume.py tests/test_resume_tailored_compile.py -v`
Expected: PASS (including the tectonic compile tests if tectonic is installed, and
`test_tailored_document_always_keeps_the_heading`).

- [ ] **Step 7: Lint + types + commit**

```bash
.venv/bin/ruff check src/cvflow/resume/__init__.py tests/test_resume.py tests/test_resume_tailored_compile.py && .venv/bin/python -m mypy src
git add src/cvflow/resume/__init__.py tests/test_resume.py tests/test_resume_tailored_compile.py
git commit -m "feat(resume): render/compile apply accepted rewrites; diff shows bullet before/after"
```

---

### Task 5: `resume.rephrase` config toggle + `build_tools` wiring + final verification

**Files:**
- Modify: `src/cvflow/config.py`, `config.example.yaml`, `src/cvflow/mcp/tools.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing config tests**

Add to `tests/test_config.py`:

```python
def test_rephrase_defaults_true_when_absent(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, VALID_YAML))
    assert cfg.resume.rephrase is True


def test_rephrase_reads_false_override(tmp_path: Path) -> None:
    yaml_text = VALID_YAML.replace(
        '  latex_compiler: "latexmk"\n',
        '  latex_compiler: "latexmk"\n  rephrase: false\n',
    )
    cfg = load_config(_write(tmp_path, yaml_text))
    assert cfg.resume.rephrase is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_config.py -k rephrase -v`
Expected: FAIL (`ResumeConfig` has no `rephrase`).

- [ ] **Step 3: Add the field + loader**

In `src/cvflow/config.py`, add to `ResumeConfig` (after `min_projects`):

```python
    rephrase: bool = True
```

In the `resume=ResumeConfig(...)` construction, after the `min_projects=` line, add:

```python
            rephrase=_opt_bool(res, "rephrase", "resume.", True),
```

- [ ] **Step 4: Document the key in `config.example.yaml`**

Under the `resume:` block (after the `min_projects` line), add:

```yaml
  # true = the LLM rewords experience/project bullets to match the JD's language (a fact guard
  # rejects any reword that adds a number/skill/claim not in your profile; rejected bullets keep
  # the master wording). false = reorder/select only, no rewording.
  rephrase: true
```

- [ ] **Step 5: Wire `build_tools` (fact corpus + flag)**

In `src/cvflow/mcp/tools.py`, replace the `ResumeTailor(...)` construction (around line 282):

```python
    # The fact guard's allowed vocabulary = the profile knowledge base + the curated skill list.
    skills_path = Path(config.profile.knowledge_base_dir) / "candidate_skills.yaml"
    fact_corpus = knowledge.full_context()
    if skills_path.exists():
        fact_corpus += "\n" + skills_path.read_text()
    tailor = ResumeTailor(
        tailoring,
        parse_master(master_root),
        min_projects=config.resume.min_projects,
        fact_corpus=fact_corpus,
        rephrase=config.resume.rephrase,
    )
```

(`knowledge` is already constructed above this line; `Path` is already imported in this module.)

- [ ] **Step 6: Run config tests + build_tools smoke**

Run: `.venv/bin/python -m pytest tests/test_config.py -k rephrase -v`
Expected: PASS.
Run: `.venv/bin/python -c "from cvflow.config import load_config; from cvflow.mcp.tools import build_tools; build_tools(load_config('config.yaml')); print('ok')"`
Expected: prints `ok`.

- [ ] **Step 7: Full suite + lint + types**

Run: `.venv/bin/python -m pytest -q && .venv/bin/ruff check . && .venv/bin/python -m mypy src`
Expected: all green. (If a live `/discover` is running, the `data/discover.lock` discover tests in
`tests/test_cron.py` may collide — re-run when it's free; they're unrelated to this change.)

- [ ] **Step 8: Commit**

```bash
git add src/cvflow/config.py config.example.yaml src/cvflow/mcp/tools.py tests/test_config.py
git commit -m "feat(config): resume.rephrase toggle (default true); wire fact_corpus into ResumeTailor"
```

---

## Self-review

**Spec coverage:**
- Constrained rephrase of experience + selected-project bullets → Tasks 3, 4. ✓
- Deterministic vocab+number guard, fall back to original → Task 1 (`_guard_ok`), applied in Task 3. ✓
- `fact_corpus` from knowledge + candidate_skills → Tasks 1 (ctor), 5 (wiring). ✓
- `assert_no_new_facts` redefined to vocab+number → Task 1. ✓
- Bullet extract/substitute, brace-balanced → Task 2. ✓
- Inline experience/projects so rewrites reach the PDF; other sections verbatim → Task 4. ✓
- `diff` before/after → Task 4. ✓
- `resume.rephrase` toggle default true; reorder kept → Tasks 5 (toggle), 3 (`rephrase` flag gates the pass; ordering untouched). ✓
- Graceful failure (no bullets / bad JSON / provider error) → Task 3 `_rephrase_bullets`. ✓
- Gate invariant untouched → no statemachine/gate changes anywhere. ✓

**Type consistency:** `_tokens`/`_numbers`/`_norm` (Task 1) reused by `_guard_ok` + `assert_no_new_facts` (Task 1). `_resume_item_bodies`/`_substitute_bullets` (Task 2) reused by `_eligible_bullets`/`_rephrase_bullets` (Task 3) and `render`/`tailored_document` (Task 4). `ResumeTailor.__init__(provider, master, *, min_projects, fact_corpus, rephrase)` defined in Task 1, called identically in Task 5. `TailoringPlan(..., rephrased=...)` defined Task 3, consumed Task 4. `config.resume.rephrase` defined Task 5, consumed by `build_tools` (Task 5) → `ResumeTailor(rephrase=...)`.

**Placeholder scan:** none — every code step contains the full code; the only existing-test edit
(Task 4) shows the full replacement body.

## Notes for the executor
- Existing single-call `_FakeProvider` resume tests stay green: with `rephrase=True` (default) a
  second `generate()` returns the order JSON, whose `.get("rewrites", [])` is empty → no rewrites.
- `test_plan_threads_feedback_into_prompt` captures the LAST prompt; since `_rephrase_bullets`
  also receives `feedback`, the rephrase prompt contains it too → the assertion still holds.
- Do not touch the live `data/cvflow.db` or `~/.hermes/`. After merge, deploy is just a gateway
  restart (code-only); `resume.rephrase` defaults true with no live config edit needed.
