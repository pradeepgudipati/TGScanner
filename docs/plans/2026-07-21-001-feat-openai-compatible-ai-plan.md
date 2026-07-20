---
title: OpenAI-Compatible AI - Plan
date: 2026-07-21
type: feat
topic: openai-compatible-ai
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-brainstorm
execution: code
product_contract_preservation: Product Contract unchanged
---

# OpenAI-Compatible AI - Plan

## Goal Capsule

- **Objective:** Route all AI features through a single OpenAI-compatible HTTP client so any compatible server (OpenAI, OpenRouter, Ollama, LM Studio, vLLM, etc.) works via config.
- **Product authority:** This Product Contract.
- **Open blockers:** None.
- **Execution profile:** Small focused change across two AI call sites, one shared config helper, dependency cleanup, and unit tests. Prefer characterization-style unit tests for config/client construction; live LLM calls are optional smoke only.
- **Tail ownership:** LFG owns simplify → review → commit/PR → CI.

## Product Contract

### Summary

Replace Gemini-specific AI integration with one OpenAI-compatible client for magazine relevance scoring and TOI semantic filename filtering.
Operators configure base URL, model, and an optional API key; the native Gemini SDK and `GOOGLE_API_KEY` are removed.

### Problem Frame

AI is split across Gemini (native SDK) and a hardcoded OpenAI client (default host + `gpt-4o-mini`).
Local and third-party OpenAI-compatible servers cannot be used without code changes, and dual providers add config and fallback complexity.

### Requirements

**Configuration**
- R1. AI uses an OpenAI-compatible chat Completions client configurable via `OPENAI_BASE_URL` (optional; omit to use the client's default host), `OPENAI_MODEL` (required), and `OPENAI_API_KEY` (optional).
- R2. When `OPENAI_API_KEY` is unset, the client still initializes with a placeholder key so local servers that ignore auth work.
- R3. When `OPENAI_MODEL` is unset, AI features fail fast with a clear configuration error before making network calls.

**Coverage**
- R4. Magazine keyword relevance evaluation uses this client only.
- R5. TOI/DC semantic filename filtering (`--ai-query`) uses this same client and env vars.

**Removal**
- R6. The native Gemini SDK dependency and all Gemini-specific code paths are removed.
- R7. `GOOGLE_API_KEY`, `--provider`, and Gemini/auto fallback behavior are removed from config, CLI, and docs.
- R8. README documents the new env vars with examples for both cloud OpenAI and a local compatible server.

### Scope Boundaries

**In scope**
- Shared OpenAI-compatible client setup and env loading for magazine + TOI AI paths
- Removal of Gemini SDK, keys, and provider switching
- README / setup doc updates

**Out of scope**
- GUI controls for base URL / model / key
- Provider-specific features (tools, vision, streaming)
- Multi-provider fallback or load balancing
- Changing non-AI search behavior (Telegram scan, keyword matching)

### Key Decisions

- KD1. OpenAI-compatible is the only AI path — no soft deprecation of Gemini.
- KD2. API key is optional with an automatic placeholder when unset.
- KD3. Model name is required with no default — avoids wrong silent defaults on local servers.
- KD4. Settings stay in `.env` / env; no GUI settings in this change.

### Acceptance Examples

- A1. Official OpenAI
  - **Given:** `OPENAI_API_KEY` and `OPENAI_MODEL` set; `OPENAI_BASE_URL` unset
  - **When:** Magazine search runs with AI evaluation
  - **Then:** Requests go to the default OpenAI host with the configured model and return relevance decisions
- A2. Local compatible server
  - **Given:** `OPENAI_BASE_URL` points at a local server; `OPENAI_MODEL` set; `OPENAI_API_KEY` unset
  - **When:** TOI search runs with `--ai-query`
  - **Then:** The filter uses that server/model without requiring a real API key
- A3. Missing model
  - **Given:** `OPENAI_MODEL` unset and AI is needed
  - **When:** Magazine or TOI AI path starts
  - **Then:** The app errors with a clear message that `OPENAI_MODEL` is required
- A4. Gemini gone
  - **Given:** Only `GOOGLE_API_KEY` is set (legacy)
  - **When:** AI features run
  - **Then:** Gemini is not used; behavior matches R2–R3 for OpenAI-compatible config only

### Success Criteria

- Any OpenAI-compatible endpoint works by setting base URL + model (+ key when needed).
- No remaining Gemini imports, provider flags, or `GOOGLE_API_KEY` references in app code or README setup.
- Magazine and TOI AI paths share the same configuration contract.

### Dependencies and Assumptions

- Assumption: Target servers implement enough of the OpenAI chat Completions API for a single user message and a text reply.
- Assumption: Operators know their server's model id and will set `OPENAI_MODEL` accordingly.
- Dependency: Existing `openai` Python package remains available.

### Outstanding Questions

None — ready for implementation.

## Planning Contract

### Key Technical Decisions

- KTD1. **Shared helper module** `openai_compat.py` owns env read + `AsyncOpenAI` construction + model resolution. Both `find_magazine.py` and `find_toi.py` import it so R1–R5 stay one contract.
- KTD2. **Placeholder API key** is the literal string `not-needed` when `OPENAI_API_KEY` is empty/whitespace. Documented in README.
- KTD3. **Pass `base_url` only when set** — do not pass `None`/empty into `AsyncOpenAI` so the SDK default host remains correct for A1.
- KTD4. **Hard-fail on missing model** via a small typed error (or clear `ValueError`) raised from the helper before any client call; callers log and abort AI paths.
- KTD5. **Remove `google-genai`** from `pyproject.toml` and refresh the lockfile; delete Gemini branches, `--provider`, and hardcoded `gpt-4o-mini` / Gemini model ids.
- KTD6. **CLI simplification:** drop `--provider` from magazine CLI; AI-on paths require successful helper config (model present). Keyword-only magazine mode remains AI-free.

### High-Level Design

```
.env / process env
  OPENAI_BASE_URL?  OPENAI_API_KEY?  OPENAI_MODEL!
        │
        ▼
 openai_compat.load_openai_compat()
  → { client: AsyncOpenAI, model: str }
        │
   ┌────┴────┐
   ▼         ▼
find_magazine   find_toi.ai_filter_matches
chat.completions.create(model=..., messages=[...])
```

Magazine keeps its existing JSON batch prompt and retry/429 logic, but only against this client.
TOI replaces Gemini `generate_content` with the same chat Completions shape and filename-line parsing.

### Assumptions

- Assumed: No other files import `google.genai` beyond `find_magazine.py` / `find_toi.py` (verify with repo search during U1).
- Assumed: `toi_gui.py` only shells scripts and needs no AI env UI changes.
- Assumed: Existing tests (`test_extraction.py`, `test_toi_matcher.py`) stay valid; new unit tests cover the helper.

### Implementation Constraints

- Do not commit or rewrite `.env` secrets; README shows placeholder examples only.
- Preserve magazine prompt/JSON parsing behavior except for provider transport.
- Keep rate-limit retry behavior for OpenAI-compatible 429s (existing magazine backoff is fine).

### Sequencing

1. U1 shared helper + tests
2. U2 magazine migration + Gemini removal there
3. U3 TOI migration
4. U4 dependency + docs cleanup
U2 and U3 both depend on U1; U4 after U2/U3 so grep confirms zero Gemini leftovers.

### Risks

- Some local servers use a nonstandard Completions path — mitigated by documenting `OPENAI_BASE_URL` must include the API root the OpenAI SDK expects (usually `.../v1`).
- Operators with only `GOOGLE_API_KEY` will break until they set `OPENAI_*` — intentional per KD1; README migration note required.

## Implementation Units

### U1. Shared OpenAI-compatible config helper

- **Goal:** One place that loads env, validates model, builds `AsyncOpenAI`, returns model string.
- **Requirements:** R1, R2, R3
- **Files:** `openai_compat.py` (new), `test_openai_compat.py` (new)
- **Approach:** Export `load_openai_compat()` (sync) returning `(client, model)` or a small dataclass. Raise clear error if model missing. Apply placeholder key and conditional `base_url`. Unit-test with env monkeypatch; do not hit the network.
- **Dependencies:** None
- **Test scenarios:**
  - Missing `OPENAI_MODEL` → raises with message mentioning `OPENAI_MODEL`
  - Key unset → client constructed with api_key `not-needed`
  - Base URL set → client receives that `base_url`
  - Base URL unset → client uses SDK default (no empty base_url arg)
  - Model + key set → returned model matches env
- **Verification:** `uv run python -m pytest test_openai_compat.py -q` (or `uv run python test_openai_compat.py` if pytest not added — prefer unittest/stdlib if no pytest dependency)

### U2. Magazine search uses shared client only

- **Goal:** Magazine AI evaluation uses helper; Gemini and `--provider` gone.
- **Requirements:** R1–R4, R6, R7
- **Files:** `find_magazine.py`
- **Approach:** Remove `google.genai` imports/clients, `provider` ctor/CLI, `_call_gemini*`, auto-fallback. Construct client via helper when not `--keyword-only`. Use returned `model` in `chat.completions.create`. Update module docstring.
- **Dependencies:** U1
- **Test scenarios:**
  - Grep: no `genai`, `GOOGLE_API_KEY`, `--provider`, or `gemini` in this file
  - With helper mocked, batch path still parses JSON decisions (smoke/unit if easy; else manual path covered by U1 + unchanged prompt helpers)
- **Verification:** Import/module loads; existing magazine keyword-only path still runs without AI env

### U3. TOI AI filter uses shared client

- **Goal:** `--ai-query` semantic filter uses the same OpenAI-compatible contract.
- **Requirements:** R1–R3, R5, R6, R7
- **Files:** `find_toi.py`
- **Approach:** Remove Gemini import/usage. On AI query, call helper; send user prompt via chat Completions; keep “filenames one per line, intersect with input” behavior. Fail clearly if model missing instead of silently skipping when AI was requested (align with R3; if AI query present and config invalid, error rather than return unfiltered list).
- **Dependencies:** U1
- **Test scenarios:**
  - Missing model with `--ai-query` intent → clear error / no Gemini skip path
  - Grep: no `GOOGLE_API_KEY` / `genai` in this file
- **Verification:** Module loads; docstring lists `OPENAI_*` instead of Gemini

### U4. Dependency and docs cleanup

- **Goal:** Remove Gemini package; document OpenAI-compatible setup.
- **Requirements:** R6, R7, R8
- **Files:** `pyproject.toml`, `uv.lock`, `README.md`
- **Approach:** Drop `google-genai` dependency; `uv sync` / lock update. README: replace `GOOGLE_API_KEY` with `OPENAI_API_KEY`, `OPENAI_MODEL`, optional `OPENAI_BASE_URL`; show cloud + local (e.g. Ollama) examples; note key optional / placeholder.
- **Dependencies:** U2, U3
- **Test scenarios:**
  - Repo grep for `GOOGLE_API_KEY`, `google.genai`, `google-genai` in app/docs (exclude this plan) → none
  - `uv sync` succeeds
- **Verification:** README examples match R1–R3; lockfile no longer lists google-genai

## Verification Contract

- Unit: `uv run python -m unittest test_openai_compat.py -v` (stdlib unittest — avoid adding pytest unless already present)
- Existing: `uv run python test_extraction.py` and `uv run python test_toi_matcher.py` still pass
- Static: `rg -n "GOOGLE_API_KEY|google.genai|google-genai|genai\\.Client|--provider" --glob '!docs/plans/**' --glob '!uv.lock'` should be clean after U4 (lock checked separately for package removal)
- Optional smoke (manual): point `OPENAI_BASE_URL` at a local server and run magazine search with keywords

## Definition of Done

- All U1–U4 complete with requirements trace satisfied
- Gemini SDK and provider switching fully removed from runtime code and README
- Shared config helper covered by unit tests
- No launch-blocking open questions
- Ready for LFG simplify / review / PR
