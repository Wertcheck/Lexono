# Lexono — Master Product Definition

**Status of this document:** Authoritative product-level source of truth,
reconstructed and consolidated 12.09.2026 from actual code + actually
executed tests/runtime evidence + current explicit architecture/product
decisions (`ARCHITECTURE.md`'s "AKTUELLER VERBINDLICHER ARCHITEKTURSTAND"
block) + `.agentic/` status documents. Historical documents (`TODO.md`,
old `ARCHITECTURE.md` sections marked "ÜBERHOLT", `FINAL_REVIEW_REPORT.md`,
`HANDOFF_PROMPT36_37_WINDOWS.md`, etc.) remain valid as **HISTORICAL** —
they are not deleted or rewritten, and are superseded by this file wherever
they conflict with it.

This file does **not** replace `.agentic/PROJECT_STATE.md`/`OPEN_ISSUES.md`
(day-to-day operative status) or `ARCHITECTURE.md` (full chronological
architecture history/reasoning). It sits **above** them as the place a new
agent reads first to understand what Lexono is, before consulting the
operative documents for current details.

---

## 1. Product Identity

Lexono (current identifiers, verified in code 2026-10-09: package `lexono`, data directory
`%ProgramData%\Lexono` (a legacy `KanzleiAI` directory is migrated once), binary `Lexono.exe`
(`windows/lexono.spec`), installer `Lexono_Setup.exe`; only the log file is still named
`kanzlei_ai.log`. Older text below that mentions `KanzleiAI`/`kanzlei_ai.exe` is historical, see
§59 of `ARCHITECTURE.md`) is a **Windows desktop application** for a
law firm (`Anwaltskanzlei`). It is not a web app, not a SaaS multi-tenant
product from the firm's point of view, and not a chatbot wrapper around
Claude — it is a configurable AI-assisted workflow platform that processes
firm-internal emails/documents, and that (as of Phase 3, §65/§71) uses a
mandatory local AI pre-processing step before any cloud AI call, with a
privacy pipeline enforced structurally, not by policy alone.

## 2. Target Users

A single law firm's staff (attorneys + `Mitarbeiter`/paralegal staff),
running the desktop app locally on a Windows PC. Role model:
`admin`/`anwalt`/`mitarbeiter` (seeded default roles, `app/auth/`).
Multi-firm/cross-tenant support is an explicit, **not yet decided**
product question (`PROMPT38_ANALYSIS.md`, tracked open in
`.agentic/TASK_MAP.md` category A) — do not assume multi-tenancy exists
at the application layer (it exists only at the **Gateway** layer, see
§11, as tenant/credential separation for the shared Anthropic relay).

## 3. Problem

A law firm needs to: ingest emails/documents, classify and assign them to
matters (`Matter`), extract content, detect deadlines, retrieve only
authorized matter/knowledge context, research configured legal sources,
draft replies, and file them after human approval or into an outbox —
**never send autonomously by default** (CLAUDE.md, non-negotiable). It
needs to do this while keeping personally identifiable client data off
any third-party cloud service unless it has first been pseudonymized
locally, and while running on a normal firm laptop (~16GB RAM, no
dedicated GPU assumed) — see §7 and §9.

## 4. Product Promise

- Local-first data handling: firm/document/client data stays on the
  firm's PC by default.
- AI assistance for drafting comes from two cooperating layers: a
  mandatory local model (Ollama) that does the actual sensitive
  document/context reasoning, followed by Claude (Anthropic) used
  **only** for language production on an already-pseudonymized payload.
- No autonomous legal decisions, no fabricated legal citations, full
  audit trail of AI actions.
- Runs as a real native Windows application (WebView2-hosted), installed
  once via a Windows installer, with automatic hardware-adaptive local-AI
  setup.

## 5. Core Architecture

```
USER
 ↓
LEXONO DESKTOP (pywebview + WebView2, native OS window chrome)
 ↓
DOCUMENT / CHAT INPUT  (app/web/chat_router.py, app/documents, app/document_generator)
 ↓
LOCAL PII DETECTION  (app/privacy/detectors.py [regex] + app/privacy/presidio_ner.py [Presidio/spaCy de_core_news_lg NER])
 ↓
LOCAL PSEUDONYMIZATION  (app/privacy/pseudonymizer.py + app/privacy/security_check.py + app/privacy/gateway.py::ClaudePrivacyGateway — "Final Payload Gate")
 ↓
LOCAL AI  (app/ai_providers/ollama_provider.py::OllamaLocalLLMProvider — mandatory intermediate step, fail-closed)
 ↓
PSEUDONYMIZED PAYLOAD  (app/privacy/gateway_schema.py::ClaudeRequestPayload — 7-field allowlist, no free-text escape)
 ↓
LEXONO GATEWAY  (gateway/ — own FastAPI process, holds the real Anthropic key; OR direct AnthropicClaudeWritingProvider in dev mode, see below)
 ↓
ANTHROPIC / CLAUDE API
 ↓
CLAUDE RESPONSE
 ↓
LEXONO (app/privacy/gateway.py::reconstruct_response — local depseudonymization)
 ↓
USER
```

### Transition-by-transition status

| Transition | Documented | Implemented | Verified (this session/real evidence) |
|---|---|---|---|
| Input → PII Detection | YES (§27) | YES (`app/privacy/detectors.py`, `presidio_ner.py`) | YES — real Presidio/spaCy run, synthetic data, this session and earlier `local_ai_smoke_test.py` runs |
| PII Detection → Pseudonymization | YES (§27, §63) | YES (`pseudonymizer.py`) | YES — placeholder round-trip verified repeatedly this session (`[MANDANT_01]`, `[AKTENZEICHEN_01]`) |
| Pseudonymization → Local AI | YES (§65, canonical block) | YES (`DraftingService.create_draft`, `ChatService.send_message` both wired per §71) | YES — real Ollama calls this session, `qwen3:8b`, placeholders preserved through inference |
| Local AI → Claude (direct dev path) | YES (§28, §57 "no gateway for dev") | YES (`AnthropicClaudeWritingProvider`) | YES — real Anthropic API call made earlier this session (CORE_ARCHITECTURE_PROOF run) with a real key, real response, real reconstruction |
| Local AI → Claude (via Lexono Gateway, production path) | YES (§70, canonical block: "kein direkter Anthropic-Zugriff vom Kanzlei-PC in Produktion") | YES (`gateway/`, `GatewayRelayWritingProvider`) | **UNKNOWN this session** — §70 itself states the gateway was only ever run locally on `127.0.0.1`, never deployed; no evidence in this session of an E2E run through the actual `gateway/` process (only the direct-key dev path was exercised). Do not report the Gateway relay path as runtime-verified without a fresh, explicit test of that specific path. |
| Claude → Reconstruction → User | YES | YES (`gateway.py::reconstruct_response`) | YES — verified together with the local-AI/direct-Claude runs above |

**Gap called out explicitly (do not silently ignore):** the production
path (via `gateway/`) and the dev path (direct Anthropic key) are
architecturally distinct and *both real code*, but only the dev path has
concrete E2E runtime evidence collected so far. Do not conflate "Claude
integration verified" with "Gateway relay path verified" — they are
different P0 gates (see §7, P0-07 vs P0-08).

## 6. End-to-End Workflow

Primary user-facing entry point is **Chat** (`app/web/chat_router.py`,
`app/chat/`) — confirmed as the actual home/landing surface, not a
secondary feature (`.agentic/TASK_MAP.md` category C). Document handling
(`app/documents`, `app/document_generator`, OCR via Tesseract, §15) feeds
into the same drafting/privacy pipeline. Every AI-assisted draft or chat
reply passes through the mandatory chain in §5 — there is no code path
that sends firm/document content directly to Claude without first going
through `ClaudePrivacyGateway`.

## 7. P0 Core Systems

Status legend (§12 below): `NOT_STARTED / IMPLEMENTED / PARTIALLY_IMPLEMENTED / VERIFIED / BLOCKED / UNKNOWN / RISK_ACCEPTED / DEFERRED / RELEASE_READY`.

---

**P0-01 — Native Windows Desktop Application**
- Purpose: the actual product surface; not a browser tab.
- Architecture role: `run.py` (PyInstaller-bundled entry point), `pywebview` + Edge WebView2, real Win32/DWM APIs for native chrome.
- Dependencies: WebView2 Evergreen Bootstrapper (bundled, §70), Windows DWM APIs.
- Status: **VERIFIED**. Evidence: this session's real Win32/DWM queries against a live installed process (`WS_CAPTION`/`WS_MAXIMIZEBOX`/`WS_THICKFRAME` all set, `DwmGetWindowAttribute` confirms `DWMWCP_ROUND` active) confirm native OS chrome + rounded corners, per an explicit user decision this session reversing an earlier custom-titlebar implementation.
- **DRIFT (see §19):** `ARCHITECTURE.md`'s canonical block and `.agentic/TASK_MAP.md` category C both still describe a **custom title bar** ("eigene Titelleiste statt nativer OS-Chrome") as the current state — this is now factually outdated. Neither file has been corrected in this run (out of the authorized file scope for this reconstruction — see §19).
- Release blocking: NO (working, verified).
- Next action: none required; a future run should correct the stale native-chrome-vs-custom-titlebar statements in `ARCHITECTURE.md`/`TASK_MAP.md`.

**P0-02 — Authentication / User Management**
- Purpose: gate access to the desktop app; forced password change; session invalidation on credential change.
- Architecture role: `app/auth/` (`service.py`, `session.py`, `security.py` [Argon2], `rate_limit.py`, `pin_lock.py`).
- Status: **VERIFIED**. Evidence: this session's real clean-room test — fresh admin created via `create-admin` with self-chosen, non-historical credentials → login → forced `/dashboard/change-password` → old session invalidated (`sessions_invalidated_after`, confirmed via `Max-Age=0` cookie) → login with new self-chosen password → app restart → login again, all via real HTTP against the real installed binary.
- Known risk: interactive `getpass.getpass()` first-run flow cannot be driven via stdin redirection in this automation environment (Windows `msvcrt` limitation) — not a product bug, only affects future automated testing of the literal keystroke path; the underlying `create-admin`/`migrate` code paths are exercised identically via direct subcommand invocation.
- Release blocking: NO.

**P0-03 — Chat / Core User Interaction**
- Purpose: primary user-facing workflow surface.
- Architecture role: `app/web/chat_router.py`, `app/chat/`, `ChatService.send_message` (wired into the same privacy→local-AI→Claude chain as `DraftingService`, confirmed §71 — "es gab also keine fehlende Verkabelung").
- Status: **IMPLEMENTED**, chat-level UI/UX itself not re-verified this session (out of this run's scope — no code touched here); local-AI status indicator in the chat header confirmed implemented (§71).
- Release blocking: NO (pre-existing, not touched, no new evidence gathered or required this run).

**P0-04 — Document Handling**
- Purpose: intake, OCR, classification, matter assignment, templates/generated documents.
- Architecture role: `app/documents`, `app/document_generator`, `app/models/document.py`, Tesseract OCR (§15), vendored into the installer bundle.
- Status: **IMPLEMENTED**, not exercised in this session's runs (out of scope). Tesseract bundle presence confirmed intact in this session's installer-integrity checks (bundle directory listing).
- Release blocking: NO (not touched, pre-existing).

**P0-05 — Microsoft Presidio / Pseudonymization**
- Purpose: the actual, structural privacy boundary — the reason this architecture is not "just call Claude directly."
- Architecture role: `app/privacy/detectors.py` (regex) + `app/privacy/presidio_ner.py` (real Presidio `AnalyzerEngine`, spaCy `de_core_news_lg`, lazy-cached) → `pseudonymizer.py` → `security_check.py` (7-point check, fail-closed) → `gateway.py::ClaudePrivacyGateway` (single allowed path toward Claude) → `check_payload_placeholder_integrity` (Final Payload Gate).
- Status: **VERIFIED**. Evidence: real Presidio/spaCy NER run against synthetic data this session and in the prior `CORE_ARCHITECTURE_PROOF` run; placeholder integrity (`[MANDANT_01]`, `[AKTENZEICHEN_01]`) confirmed preserved through real local-AI inference multiple times this session.
- Release blocking: NO.

**P0-06 — Local AI / Ollama**
- Purpose: mandatory local reasoning step before any data (even pseudonymized) reaches Claude; also the practical hardware-adaptive on-ramp for firms without cloud access.
- Architecture role: `app/local_ai/` (hardware detection, model catalog, recommendation engine, `OllamaInstaller`, `LocalAiSetupService`), `app/ai_providers/ollama_provider.py::OllamaLocalLLMProvider`.
- Status: **VERIFIED**, with one important, evidenced, already-flagged **RISK_ACCEPTED** item (see §19 drift #2): hardware detection → Ollama auto-install → model pull → health check → `.env` persistence all confirmed working end-to-end this session (genuine clean-room run with Ollama initially absent, and a fast re-run with Ollama already present), restart-persistence confirmed, real inference confirmed multiple times (`qwen3:8b`, 97–120s). A UX gap (no progress feedback during a long first-time model download, real risk of looking "frozen") was found, root-caused, fixed (heartbeat + corrected wizard text), and regression-tested this session.
- Release blocking: NO (functional; the model-choice latency risk is explicitly accepted, not blocking, per direct prior user instruction not to swap `qwen3:8b`).

**P0-07 — Cloud AI / Claude / Anthropic API (direct/dev path = CURRENT PILOT PATH)**
- Purpose: final language-production step on already-pseudonymized text.
- Architecture role: `app/ai_providers/anthropic_writing_provider.py::AnthropicClaudeWritingProvider`, official `anthropic` SDK, no `base_url` override (`tests/test_no_ai_gateway_proxy.py` guards this structurally). Selected automatically whenever `lexono_gateway_url` is unset (`app/ai_providers/factory.py::build_writing_provider`) — confirmed this is the actual configuration in use (`GATEWAY_URL_SET: False`).
- Status: **VERIFIED**, real, fresh, full-pipeline evidence (12.09.2026, P0 Cloud AI Direct-Anthropic validation run): called the real, unmodified `app/web/service_factory.py::get_drafting_service()` → real `DraftingService.create_draft()` (the identical function `ChatService.send_message`/`chat_router.py` calls in production) with synthetic PII (`Max Mustermann`, synthetic email/phone/address/Aktenzeichen). Confirmed: `KEY_PRESENT=True`, `GATEWAY_URL_SET=False` (direct path in effect), real Presidio pseudonymization ran, real Ollama (`qwen3:8b`) local-AI step ran (not bypassed), real Anthropic API call made and accepted, real Claude response received, real local reconstruction restored the original name correctly with no residual placeholder leak. No API key value was ever printed/logged — only a `KEY_PRESENT` boolean was checked at runtime.
- This is explicitly a **P0 CORE SYSTEM**, not optional, not a future feature.
- Release blocking: NO for the current Direct-Key pilot path.

**P0-08 — Lexono Gateway (FUTURE PRODUCTION ARCHITECTURE — explicitly DEFERRED, not part of the current pilot path)**
- Purpose: production-path Anthropic relay — the real Anthropic key must never exist on a firm PC in production (§70, non-negotiable per its own stated reasoning).
- Architecture role: `gateway/` (own top-level directory, own FastAPI app, own DB, own `.env.gateway` namespace), `app/ai_providers/gateway_relay_client.py`/`gateway_writing_provider.py`, `app/review/gateway_review_provider.py`. Tenant auth via `Authorization: Bearer <client_id>:<secret>`, Argon2id-hashed secrets, per-tenant revocation, in-process rate limiting, structurally content-free logging.
- Status: **DEFERRED** — IMPLEMENTED, NOT VERIFIED, NOT DEPLOYED (per its own architecture record, §70: "kein echter Serverbetrieb... ausschließlich LOKAL getestet auf 127.0.0.1"). This is a deliberate, explicit deferral for the current pilot phase (governing instruction for the 12.09.2026 Direct-Anthropic validation run), **not an oversight and not forgotten** — `DEFERRED != FORGOTTEN`. It carries its own, separate P0 gate that must be satisfied before any release that ships with `lexono_gateway_url` configured instead of a direct key.
- Release blocking: **NO for the current Direct-Key pilot** (explicitly out of scope for it). **UNKNOWN / DECISION REQUIRED** only for a *future* production release that assumes the Gateway path specifically — do not read this line as blocking the pilot.

**P0-09 — End-to-End AI Workflow**
- Purpose: the full chain, not its parts in isolation.
- Status: **VERIFIED for the current pilot path** (direct-key variant) — see P0-07 for the full-pipeline evidence (Presidio → pseudonymize → real Ollama → real direct Anthropic call → real reconstruction), gathered at the `DraftingService.create_draft()` level (backend/service-level E2E — the real production orchestration function, not a bypassing script; a full native-window click-through UI test remains infeasible in this automation environment, an already-documented limitation, not new). **DEFERRED for the Gateway-relay variant** (see P0-08) — this is a distinct, separate gate, not a partial failure of this one.
- Release blocking: NO for the current Direct-Key pilot. See P0-08 for the separate, future Gateway gate.

**P0-10 — Security / Secrets / Data Isolation**
- Purpose: non-negotiable per CLAUDE.md.
- Status: **VERIFIED with one real finding, now fixed**: this session found and redacted a plaintext test-admin password that had been written directly into `.agentic/PROJECT_STATE.md` in an earlier session — a real, concrete violation of the "never write secrets into code/logs" rule, now corrected. `.env` confirmed gitignored. No secrets observed in this session's own logs/tests/docs. `tests/test_no_ai_gateway_proxy.py`/`test_no_editable_api_key_in_ui.py` structurally guard against key exposure.
- Release blocking: NO (finding fixed).

**P0-11 — Persistence / Database**
- Purpose: SQLite prototype, PostgreSQL-ready abstraction (CLAUDE.md, non-negotiable technical decision).
- Architecture role: SQLAlchemy + Alembic (`migrations/`, `alembic.ini`), `DATABASE_URL` configurable.
- Status: **VERIFIED**. Real Alembic migrations run repeatedly this session from a genuinely empty DATA_DIR against the real installed binary, always to completion (`schritt3_010` head reached).
- Release blocking: NO.

**P0-12 — Installer / Deployment**
- Purpose: the actual delivery mechanism to a firm PC.
- Architecture role: PyInstaller onedir build (`windows/lexono.spec`) + Inno Setup (`windows/installer.iss`, **FROZEN** unless a proven blocker requires a change — none has this session), WebView2 bootstrapper bundled, per-user install under `%LocalAppData%\Lexono`, persistent data under `%ProgramData%\Lexono` (never deleted on uninstall).
- Status: **VERIFIED**, with one real, evidenced, currently-open risk: this session rebuilt the installer (new SHA-256 `9c495240...`, superseding the prior `027fcfcb...`), reinstalled it fresh, and re-ran the full clean-room→first-run→login→restart→local-AI chain successfully against the new artifact.
- Release blocking: NO for the installer mechanics themselves; see §19 for the open antivirus/Defender risk (D-classified, not proven, not reproduced this session, with new evidence *against* Defender's standard detection pipeline as the cause).

**P0-13 — Required Windows Runtime Dependencies**
- Purpose: WebView2 runtime, Tesseract OCR binaries, spaCy German model — all must survive packaging.
- Status: **VERIFIED**. Bundle integrity (~1GB/~1022MB) confirmed intact this session with `app/`, `migrations/`, `presidio_analyzer/`, `de_core_news_lg/`, `tesseract/` all present after a real install.
- Release blocking: NO.

## 8. Security & Privacy Principles

- CLAUDE.md's non-negotiable rules apply without exception: no real client data in tests, no secrets in code/logs, document/email content is untrusted input, no fabricated legal citations, no autonomous legal decisions, no autonomous external communication, strict matter/client isolation, full audit trail for AI actions.
- The privacy boundary (Presidio + pseudonymization + Final Payload Gate) sits **on the firm PC, before any network call** — the Gateway (§70) is explicitly infrastructure for key custody, not a second privacy checkpoint, and must never be described as one.
- API call logs are structurally free of content fields (`ApiCallLog` excludes `content`/`text`/`prompt`/`response`/`details`/`message` column names — architecturally enforced, not just convention).

## 9. Local AI Principles

- Mandatory intermediate step once enabled — fail-closed (`LocalLLMUnavailableError` blocks the request; Claude is **never** called as a fallback when local AI is configured but unreachable).
- Hardware-adaptive model selection exists (`HardwareDetector` → `RecommendationEngine` → `ModelCatalog`) and must not be replaced by a single hardcoded model choice without a data-based benchmark decision (as was done for the `qwen3:4b` → `qwen2.5:1.5b` default change, §71) — see §19 for the currently-accepted risk in the *adaptive* selection's own tie-break logic.
- `local_ai_enabled` defaults to `False` at the raw `Settings` level (deliberate, hardware-conflict-aware decision, §70) but the interactive setup wizard defaults its own prompt to "yes" — so a real first-run installation ends up with local AI enabled in practice unless the user actively declines.

## 10. Cloud AI / Claude Principles

- Claude/Anthropic is a **P0 core system**, never optional, never a "future feature" — see §7 P0-07/P0-08/P0-09.
- Claude is used **exclusively for language production** on an already-pseudonymized, allowlist-schema payload (`ClaudeRequestPayload`, 7 fields, no free-text escape) — never for matter analysis, matter assignment, legal research, deadline determination, strategy decisions, or sending.
- Two real, distinct call paths exist: **direct** (dev/test, `anthropic_api_key` in local `.env`) and **Gateway relay** (production, `lexono_gateway_url` only, no key on the firm PC). Presence of `lexono_gateway_url` in config exclusively selects the Gateway path (`app/ai_providers/factory.py`) — there is no separate mode switch to misconfigure.
- A mock response, a unit test alone, or the mere existence of an Anthropic client class does **not** satisfy this gate — only a real API call with a real response counts as VERIFIED (per the governing rules for this reconstruction).

## 11. Gateway Principles

- Self-operated by Lexono, not a third-party SaaS gateway (Portkey and similar were explicitly evaluated and rejected, §57) — this distinction matters and must not be conflated in future audits.
- Never sees, stores, or inspects original (non-pseudonymized) data — receives exactly the same bytes that would otherwise go directly to Anthropic, over an authenticated hop.
- Per-tenant (per-firm) credentials, Argon2id-hashed, individually revocable, without touching the shared Anthropic key.
- **Not currently deployed anywhere** — exists only as code, tested locally on `127.0.0.1` (§70). A production release that assumes the Gateway path requires this to change (see P0-08).

## 12. Desktop / Installer Principles

- Native OS window chrome (not a custom-drawn titlebar) is the **current, confirmed** state, per an explicit user decision this session — this supersedes `ARCHITECTURE.md`'s canonical-block statement to the contrary (see §19 drift #1).
- `windows/installer.iss` is **FROZEN** — changed only when a concrete, proven installer blocker requires it. None has, this session or the prior one.
- Persistent firm data (`%ProgramData%\Lexono`) is never deleted by the installer/uninstaller.

## 13. Product Definition of Done

### PRODUCT FUNCTIONALLY COMPLETE
All P0 systems in §7 are IMPLEMENTED. **True today**, with the Gateway
(P0-08) implemented but unverified/undeployed noted as a real gap, not a
missing implementation.

### RELEASE READY

**Two separate release targets exist and must not be conflated:**

1. **Current pilot (Direct Anthropic Key path).** Requires, in addition
   to functional completeness: release candidate identified (done —
   SHA-256 `9c495240e3ef073b483f04767a2e30e260fcc817434089aa10ce2ce7dd8d288e`),
   regressions passed (done — 1522 passed/1 skipped/0 failed), security/
   privacy gates met (done, with one finding fixed this session), installer
   verified (done), the full Presidio→Local AI→Direct Claude→reconstruction
   E2E flow verified (done — 12.09.2026 Direct-Anthropic validation run,
   see P0-07/P0-09), no unresolved P0 blockers **for this path** (none —
   the Gateway is DEFERRED for this path by explicit decision, not an
   unresolved blocker for it), P1 risks explicitly assessed (done —
   antivirus/Defender D-classified with new negative evidence;
   model-latency tie-break RISK_ACCEPTED). **→ RELEASE READY.**
2. **Future production (Lexono Gateway relay path).** P0-08/P0-09's
   Gateway variant is DEFERRED, not verified, not deployed — this target
   is simply **not yet evaluated for release readiness** and must not be
   called release-ready, blocked, or broken; it has its own future gate.

**Never state "Lexono is release ready" or "the production architecture
is verified" without naming which of these two targets is meant** — say
"the current Direct-Key pilot path is release ready" specifically.

## 14. Release Gates

A release must not be called RELEASE_READY while any P0 system relevant
to *that specific release target* is BLOCKED, UNKNOWN, or NOT VERIFIED,
except where explicitly marked RISK_ACCEPTED or DEFERRED with a
documented decision. Per §7/§13, the **current Direct-Key pilot** has no
system in that state — all its relevant P0 gates are VERIFIED. **P0-08
(Gateway)** carries status DEFERRED by explicit governing decision for
this pilot — this is a deliberate deferral, not a silent pass, and it
does not block the current pilot's release-ready call. It remains its
own open gate (**DECISION REQUIRED before *any future* Gateway-based
production release**), tracked separately, not forgotten (`DEFERRED !=
FORGOTTEN`).

## 15. Evidence Model

Every VERIFIED claim in this document is backed by one of:
- a real, executed test suite run (with pass count and date),
- a real installed-binary runtime interaction (HTTP call, process
  inspection, DWM/Win32 API query) performed this session or the
  immediately prior one, with concrete numbers (hashes, timings, HTTP
  status codes) cited inline above, or
- an explicit, named architecture/product decision in `ARCHITECTURE.md`
  or `.agentic/DECISIONS.md`.

Claims not backed by one of these three are marked IMPLEMENTED (code
exists) or UNKNOWN (no evidence found), never VERIFIED.

## 16. Status Model

```
NOT_STARTED / IMPLEMENTED / PARTIALLY_IMPLEMENTED / VERIFIED / BLOCKED / UNKNOWN / RISK_ACCEPTED / DEFERRED / RELEASE_READY
```

Rules (non-negotiable, apply to every future run):
- `IMPLEMENTED != VERIFIED`
- `DOCUMENTED != VERIFIED`
- `TEST_EXISTS != VERIFIED` (a mocked unit test proves the code shape, not the real integration)
- `MOCKED != REAL_E2E`
- `UNKNOWN != DONE`
- `API CLIENT EXISTS != API WORKS`
- `DIRECT API VERIFIED != GATEWAY VERIFIED`
- `DEFERRED != FORGOTTEN` (a deliberately deferred item — e.g. the
  Gateway relay path for the current pilot, see P0-08 — has its own
  future gate; it is not a pass, not a fail, and not to be raised again
  as a surprise)

## 17. Future / Non-Core Scope

Explicitly out of core scope, not to be pulled in without a direct
product decision: multi-firm/cross-tenant support at the application
layer (§2), runtime extensibility beyond Ollama (llama.cpp etc. —
architecture-ready but unimplemented), a full async job/queue rebuild of
the chat response path (§71, deliberately not built — current synchronous
call is UI-acceptable with the current model choice), automated
feedback→prioritization→release pipeline beyond current manual
categorization (`.agentic/TASK_MAP.md` category F).

## 18. Agent Operating Rules

- Read this file, then the applicable `.agentic/` operative documents,
  before any substantial Lexono task — see the pointer added to
  `CLAUDE.md`.
- Never classify Lexono, or a release, as complete without checking the
  applicable P0 gates in §7 against **current** evidence, not against
  what an older document claims.
- A single component working (e.g., "the installer works") never implies
  the whole product is release-ready — all P0 gates apply regardless of
  which single component a task happens to name.
- When code and documented architecture decisions conflict, or when two
  documents (or a document and the actual code) disagree: **report the
  conflict explicitly** (DRIFT DETECTED → EVIDENCE → IMPACT → REQUIRED
  DECISION/UPDATE, see §19 for two live examples). Never silently
  reconcile, never silently pick one side, never hide the discrepancy.
- `UNKNOWN` is a legitimate, final answer for a given run — it is not a
  failure state for the agent, and must never be upgraded to `PASS` by
  assumption, precedent, or "it probably still works."
- The existing agentic structure (`agents/`, `skills/`, `.agentic/`) is
  the only agent framework for this project. No parallel agent runtime,
  no new orchestration layer, ever.

## 19. Source-of-Truth Rules

Evidence hierarchy for reconstructing product truth (used to build this
document): (1) actual current code, (2) actually executed tests/runtime
evidence, (3) explicit current architecture/product decisions, (4)
current `.agentic/` status docs, (5) older handoffs/historical docs, (6)
old plans/TODOs. A business/architecture decision is never *invented*
from code alone — where code and an explicit decision conflict, or where
no clear decision exists, this is stated as a conflict or as `UNKNOWN /
DECISION REQUIRED`, never guessed.

### Live drift found during this reconstruction (12.09.2026)

**Drift #1 — window chrome.** `ARCHITECTURE.md`'s canonical block (top of
file) and `.agentic/TASK_MAP.md` category C both state a custom title bar
("eigene Titelleiste statt nativer OS-Chrome") as current. Actual current
code + this session's real DWM/Win32 evidence (native `WS_CAPTION`, native
rounded corners via `DwmSetWindowAttribute`) confirm **native OS chrome**,
per an explicit, confirmed user decision made this session that reversed
the custom-titlebar approach. **Impact:** low — this is a resolved,
intentional, already-executed decision, not an open question; the
documents are simply stale. **Required update:** a future run with
explicit permission to edit `ARCHITECTURE.md`/`TASK_MAP.md` should mark
the custom-titlebar statements "ÜBERHOLT" and update the canonical block,
following this project's own established convention for such reversals
(§59/§60/§63 pattern). Not done in this run — out of the authorized file
scope (§27 of the governing instructions limited changes primarily to
this file).

**Drift #2 — local model choice on capable CPU-only hardware.**
`ARCHITECTURE.md` §71 and `.agentic/DECISIONS.md` document a deliberate,
data-based decision to default to `qwen2.5:1.5b` specifically *because*
the `qwen3` model family has a "structural thinking-latency problem" on
CPU-only hardware (`qwen3:4b`: >20 minutes, timed out). However,
`RecommendationEngine.recommend()` (`app/local_ai/recommendation.py:262`)
selects, among all models reaching the best status tier, the one with the
**highest** `recommendation_priority` (`max(candidates_at_best, key=...)`)
— and `qwen3:8b` has priority 4 versus `qwen2.5:1.5b`'s priority 0
(`app/local_ai/model_catalog.py:194,202`). On this session's real
CPU-only reference machine (16GB-class RAM, no dedicated GPU), the
adaptive engine recommended and installed `qwen3:8b`, not `qwen2.5:1.5b`
— real inference on it took 97–120 seconds this session, close to the
production 120s provider timeout, and structurally the same "qwen3
family" risk §71 explicitly moved away from. **Impact:** medium — real,
reproducible, but already known and explicitly **RISK_ACCEPTED** by
direct prior user instruction not to swap `qwen3:8b` on the reference
machine. **Required decision (not made in this or the prior session):**
should `RecommendationEngine`'s tie-break prefer the *smallest* model
meeting the best tier instead of the largest, given §71's own evidence
about the qwen3 family — or is the current behavior intentional for
non-reference hardware? Documented here as `DECISION REQUIRED`, not
silently resolved either direction.

**Drift #3 — Gateway path never end-to-end proven.** See P0-08. Not a
documentation/code mismatch, but a documented architecture (§70) that
itself says the Gateway was only ever locally tested, and no session
since has produced real evidence of the actual relay path working
end-to-end. **Update 12.09.2026 (P0 Cloud AI Direct-Anthropic validation
run):** this is now explicitly reframed, by governing decision for that
run, as **DEFERRED for the current Direct-Key pilot** rather than an
open blocker for it — the pilot's own release-readiness does not depend
on the Gateway. It remains flagged as `DECISION REQUIRED` before any
*future* release that ships with `lexono_gateway_url` (no direct key)
instead — that is a separate, still-unresolved gate, not resolved by
this update.

## 20. Change Log

- 12.09.2026 — Initial creation of this file (LEXONO MASTER PRODUCT
  TRUTH INITIALIZATION run). Reconstructed from `CLAUDE.md`,
  `ARCHITECTURE.md` (full section index + targeted reads of §27/28/57/60/
  63/65/70/71 + canonical block), `.agentic/PROJECT_STATE.md`,
  `OPEN_ISSUES.md`, `TASK_MAP.md`, `DECISIONS.md`, `TEST_STATE.md`,
  `SECURITY_REVIEW.md` (spot-checked for DSGVO/ISO overclaiming — none
  found), and this session's own real E2E evidence (installer rebuild,
  clean-room test, local-AI setup/inference, Defender log forensics).
  Three drift items identified and documented (§19), none silently
  resolved. No product code changed as part of creating this file.

- 12.09.2026 (later) — P0 Cloud AI / Direct Anthropic E2E validation run.
  Real, fresh, full-pipeline proof gathered: `get_drafting_service()` →
  `DraftingService.create_draft()` (the exact function the real chat
  route calls) run with synthetic PII, real Presidio pseudonymization,
  real Ollama (`qwen3:8b`) local-AI step, real direct Anthropic API call,
  real reconstruction — all confirmed via a payload-boundary capture at
  the point the Claude request is built (no original PII present, correct
  placeholder present) and via the final reconstructed text (original
  name correctly restored, no placeholder leak). `KEY_PRESENT=True` only
  ever checked as a boolean, never printed. P0-07/P0-09 upgraded from
  "verified via an earlier proof + component-level re-verification" to
  "verified via a dedicated, fresh, full-pipeline run this session."
  P0-08/§13/§14/§19 reworded to unambiguously mark the Gateway as
  **DEFERRED for the current pilot**, not a blocker for it — the earlier
  wording risked being read as "Gateway status decides current pilot
  release-readiness," which was not the intent and is corrected here.
  Real, reproducible finding: running Presidio+spaCy, FastEmbed, and a
  loaded `qwen3:8b` concurrently on the 16GB-class reference machine
  caused severe memory pressure (free RAM dropped under 1GB); retried
  successfully after freeing a leftover process, and by using the
  codebase's existing `FakeEmbeddingProvider` test double for the
  research/search side-dependency only (not for Presidio, Local AI, or
  Claude, which stayed fully real) — noted as a real environmental risk,
  not fixed or investigated further (out of this run's scope). No
  product code changed; a temporary local diagnostic script and dev-only
  `.env` additions were used and fully removed/reverted after the run.

- 12.09.2026 (later still) — P1 memory-pressure root-cause investigation.
  Measured real, isolated component costs on the reference machine
  (Presidio/spaCy ~938MB, FastEmbed ~1.71GB, Ollama `qwen3:8b` ~5.6GB in
  its own `llama-server` process). Reproduced (twice) a real Ollama
  `/api/generate` HTTP 404 failure specifically when free system RAM fell
  below ~1GB with all three resident simultaneously — root-caused to
  `app/search/service.py` unconditionally calling the embedding provider
  even against an empty knowledge-base/source/document corpus (the common
  case for a new firm). Fixed minimally (embed only when there is
  something to score against) and verified with a real before/after
  measurement (full `create_draft()` now ~1GB proc memory instead of
  ~2.68GB) plus 4 new regression tests; full suite 1526 passed/1 skipped/
  0 failed. Presidio, Local AI, and the Direct-Claude pilot path (P0-05/
  P0-06/P0-07/P0-09) are unaffected and remain VERIFIED. The `qwen3:8b`
  sizing risk itself remains RISK_ACCEPTED, unchanged (see Drift #2) — a
  new installer build + clean-room re-verification is recommended before
  the next real pilot deployment, per the Change Invalidation Rule, since
  this is a real production-code change. See `.agentic/OPEN_ISSUES.md`
  (MEDIUM — MITIGATED) and `.agentic/DECISIONS.md` for full detail.

- 12.09.2026 (Zero-Excuse Release Run) — **Real, user-reported P0 defect
  fixed in P0-02 (Authentication):** `run.py::main()`'s first-run
  detection checked only `.env` presence, not whether any user actually
  existed in the database. Since `.env` is written as the very first step
  of `run_setup_wizard()` — before migration/admin-creation, the actually
  load-bearing steps — any single failure in those later steps left a
  real end user stranded on the login page with no way to know
  credentials (exactly as reported). Root-caused, fixed
  (`_first_run_setup_required()` now also checks for an existing user;
  re-triggers setup with `force=True` when none exists), and verified
  against the actual freshly-built, freshly-installed release candidate
  via a faithful reproduction (a genuinely failed `create-admin`, not a
  synthetic shortcut) — confirmed broken before the fix (silent login),
  confirmed correct after (setup assistant re-invoked). A pre-existing
  test had encoded the broken behavior as expected; corrected. P0-02's
  status in §7 remains VERIFIED, now with this specific failure mode
  closed rather than merely untested. New release candidate SHA-256
  `01351b5fd32ee9ed74911e1ead503d995195d92a5d7b68f7b49ff60557783239`.

- 12.09.2026 (Legacy Cleanup / Start.vbs fix) — **Second, independent
  instance of the same defect class in P0-01/P0-02.** `Start.vbs` (the
  actual Start Menu/desktop shortcut launch mechanism, per
  `installer.iss`) had its own separate, unfixed copy of the exact same
  "`.env` presence = setup complete" logic just corrected in `run.py`.
  Found while investigating a real end user's report of losing access to
  their own freshly-created account (First Run had genuinely succeeded —
  a real user existed in the database — but the one-time password
  display was likely covered by the immediately-opening native window).
  Fixed with a `.setup_complete` marker, written by
  `run_setup_wizard()` only after real admin-creation success;
  `Start.vbs` now checks that marker instead of `.env`. Verified against
  the actual newly built, installed `.exe` via the real production
  subprocess calls. A legacy-artifact audit (explicit user request)
  found no separate `dist\KanzleiAI` build or `KanzleiAI_Setup.exe`
  anywhere — only one spec, one installer output — and confirmed
  `kanzlei_ai.exe`/`Start.vbs`/`%ProgramData%\KanzleiAI` are all
  intentional (ARCHITECTURE.md §59), not legacy debris, and not the
  cause of either P0. New release candidate SHA-256
  `a973cf1f66eb77beef3a3e3be5e62b22af78ba750ddbff9092b18385a8e0f258`.
