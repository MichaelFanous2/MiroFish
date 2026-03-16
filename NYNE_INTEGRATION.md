# MiroFish × Nyne Integration — Architecture & Status

> **Fork:** `MichaelFanous2/MiroFish` (upstream: `666ghj/MiroFish`)
> **Last updated:** 2026-03-15

---

## Goal

Replace MiroFish's synthetic LLM-fabricated personas with **real people enriched via Nyne.ai**.
The core architectural shift: instead of extracting named entities from an article and fabricating their personalities, we:

1. Ask "who are ALL the relevant stakeholder groups for this event?" (LLM-proposed, user-editable)
2. Populate each group with real people via Nyne search or CSV
3. Enrich each person with their actual career history and public posts
4. Extract their real opinions on the topic (grounded in their own words)
5. Build personas that are constrained by verifiable evidence

Every agent is now a **named real person** with their actual stance, communication style, and behavioral parameters derived from real social data. Synthetic fallback is available for any slot Nyne can't fill, clearly labeled.

---

## Data Flow

```
[1] EVENT INPUT
    Document upload OR plain-text topic description
         ↓
[2] EVENT ANALYSIS  (UNCHANGED — Zep graph + entity extraction)
    Extract: event type, key themes, named entities from document
         ↓
[3] CAST ASSEMBLY  ← NEW (Step2CastAssembly.vue)
    LLM proposes stakeholder groups:
      ├── Named entities from document  → auto-populated from Zep
      ├── Relevant archetypes           → populated via Nyne person search
      └── User additions                → CSV upload | LinkedIn URL paste
    User reviews, edits groups, adjusts counts, then approves.
         ↓
[4] NYNE ENRICHMENT PIPELINE  ← NEW (async, background thread)
    For each cast member with a LinkedIn URL:
      → career history, current role, education, skills
      → newsfeed (actual LinkedIn + Twitter posts)
      → follower / connection counts
      → interest clusters (psychographic)
    Synthetic fallback fires for any unfilled slot → labeled "synthetic_fallback"
         ↓
[5] OPINION EXTRACTION  ← NEW
    Per person + event topic:
      → keyword + LLM semantic filter of newsfeed for topic-relevant posts
      → LLM synthesis constrained ONLY to evidence from actual posts
      → stance, sentiment_bias, confidence, key_positions (with citation URLs)
      → grounding_level: "high" (3+ posts) | "medium" (1-2) | "low" | "inferred"
         ↓
[6] REAL PERSONA BUILDER  ← NEW (replaces OasisProfileGenerator for real people)
    NynePersonData + PersonOpinionProfile → OasisAgentProfile
      → 1200-word persona narrative constrained by verified facts
      → follower_count     ← real Twitter followers
      → friend_count       ← real LinkedIn connections
      → influence_weight   = log10(followers + 1) / 6
      → activity_level     = min(len(newsfeed) / 30, 1.0)
      → active_hours       ← inferred from post timestamps
      → sentiment_bias     ← from PersonOpinionProfile (real post evidence)
      → stance             ← from PersonOpinionProfile (real post evidence)
    Synthetic members delegate to existing OasisProfileGenerator (unchanged).
         ↓
[7] OASIS SIMULATION  (COMPLETELY UNCHANGED)
    Twitter + Reddit parallel simulation
         ↓
[8] REPORT + GROUNDING REPORT  ← enhanced
    grounding_report.json saved per simulation.
    Surfaces real citations as evidence for each predicted behavior.
```

---

## Completed Work

### New backend services

| File | Description |
|------|-------------|
| `backend/app/services/nyne/__init__.py` | Package marker |
| `backend/app/services/nyne/nyne_client.py` | Nyne API wrapper. Async submit→poll pattern (mirrors `nyne_batch_enrich.py`). `NynePersonData` dataclass with `linkedin_url`, `name`, `career_history`, `newsfeed`, `twitter_followers`, `linkedin_connections`, `interests`, etc. `NyneClient` with `enrich_person`, `get_interests`, `search_person`, `batch_enrich`. Auth via `NYNE_API_KEY` + `NYNE_API_SECRET`. Poll interval 4s, timeout 500s. |
| `backend/app/services/nyne/cast_assembler.py` | `StakeholderGroup` + `CastMember` dataclasses. `CastAssembler.generate_groups_from_event()` — LLM proposes groups from event description + named entities. `populate_group_via_nyne()`, `populate_group_via_csv()` (auto-detects LinkedIn URL column), `populate_group_via_urls()`, `fill_synthetic_fallback()`. Persists to `cast_groups.json`. |
| `backend/app/services/nyne/enrichment_pipeline.py` | `EnrichmentPipeline.run()` — parallel async enrichment via `ThreadPoolExecutor` (default 10 workers). Saves each `NynePersonData` to `nyne_enrichment/{md5_hash}.json` incrementally as they complete. Progress tracked in `enrichment_progress.json`. `EnrichmentProgress` dataclass tracks per-member status: `"pending" | "enriching" | "complete" | "failed" | "synthetic"`. |
| `backend/app/services/nyne/opinion_extractor.py` | `PersonOpinionProfile` dataclass: `stance`, `sentiment_bias` (-1 to 1), `confidence`, `key_positions` (cited), `relevant_posts`, `grounding_level`, `advocacy_style`. `OpinionExtractor.extract()`: (1) keyword + LLM semantic filter of newsfeed, (2) LLM synthesis constrained to actual posts only — explicitly forbidden from inventing positions, (3) grounding_level assignment. `extract_batch()` for parallel processing. |
| `backend/app/services/persona/__init__.py` | Package marker |
| `backend/app/services/persona/real_persona_builder.py` | `RealPersonaBuilder.build(person, opinion, user_id, topic)` → `OasisAgentProfile`. All behavioral params derived from real data. LLM writes 1200-word persona narrative with explicit rules: quote actual posts, no invented positions, hedged language for gaps. Private `_activity_level`, `_stance`, `_sentiment_bias`, `_influence_weight` attributes attached for `SimulationConfigGenerator` patching. Synthetic members delegated to existing `OasisProfileGenerator`. |

### Modified backend files

| File | Changes |
|------|---------|
| `backend/app/config.py` | Added `NYNE_API_KEY`, `NYNE_API_SECRET`, `NYNE_BASE_URL`, `NYNE_MAX_CONCURRENT` (default 10), `NYNE_POLL_INTERVAL` (default 4s), `NYNE_POLL_TIMEOUT` (default 500s). All from env vars with safe defaults. |
| `backend/app/services/simulation_manager.py` | New `SimulationStatus` values: `CASTING`, `ENRICHING`, `EXTRACTING_OPINIONS`, `BUILDING_PERSONAS`. New `SimulationState` fields: `use_real_people`, `groups_generated`, `groups_approved`, `enrichment_complete` — all backward-compatible with fallback defaults. New method `prepare_simulation_real_people()`: full pipeline (load groups → enrich → extract opinions → build personas → generate config → patch real activity/stance → save profiles → save grounding report → mark READY). New `_save_grounding_report()` writes `grounding_report.json` with per-group, per-member citations and `overall_grounding` score. |
| `backend/app/api/simulation.py` | 8 new endpoints appended (zero existing endpoints modified): `POST /:id/groups/generate`, `GET /:id/groups`, `PATCH /:id/groups/:gid`, `DELETE /:id/groups/:gid`, `POST /:id/groups/populate`, `POST /:id/groups/upload-csv`, `GET /:id/groups/status`, `POST /:id/groups/approve`, `GET /:id/grounding-report`. |
| `backend/.env.example` | Created with full env var documentation including Nyne section. |

### New frontend components

| File | Description |
|------|-------------|
| `frontend/src/components/Step2CastAssembly.vue` | Vue 3 Composition API. 3 phases: **(01) Generate Groups** — calls `generateGroups()`, shows LLM-proposed group cards with member counts; **(02) Review & Curate Cast** — group cards with member lists (name, role, source badge), per-group inline URL input, CSV file upload, "Search Nyne" button placeholder, add custom group form, delete group; summary bar (groups / real / synthetic / total); "Approve Cast & Start Enrichment" CTA; **(03) Enrichment Progress** — polls `/groups/status` every 3s, shows member status grid with colored chips (Nyne / CSV / synthetic / failed). Emits `cast-approved` event when enrichment completes. |

### Modified frontend files

| File | Changes |
|------|---------|
| `frontend/src/api/simulation.js` | Added: `generateGroups`, `getGroups`, `populateGroup`, `uploadGroupCSV`, `getGroupsStatus`, `approveGroups`, `updateGroup`, `deleteGroup`, `getGroundingReport`. All follow existing `service.get/post/patch/delete` + `requestWithRetry` patterns. |
| `frontend/src/components/Step2EnvSetup.vue` | Added `useRealPeople: Boolean` prop. When `true`: (a) enrichment polling starts on mount, (b) new Step 01b card visible — shows phase progress bar (Enrichment → Opinion Extraction → Persona Building), live member status grid with animated chips (`enriching` pulses blue, `complete` green, `synthetic` yellow, `failed` red), grounding report summary once pipeline completes. Added imports for `getGroupsStatus` and `getGroundingReport`. All existing logic unchanged when `useRealPeople=false`. |

---

## API Endpoints Added

All additive — zero existing endpoints changed.

```
POST   /api/simulation/:id/groups/generate      LLM proposes stakeholder groups
GET    /api/simulation/:id/groups               Get current group list
PATCH  /api/simulation/:id/groups/:gid          Update group name / criteria / target_count
DELETE /api/simulation/:id/groups/:gid          Remove a group
POST   /api/simulation/:id/groups/populate      Populate group via nyne_search or urls
POST   /api/simulation/:id/groups/upload-csv    Populate group via CSV file upload
GET    /api/simulation/:id/groups/status        Real-time enrichment progress (poll this)
POST   /api/simulation/:id/groups/approve       Approve cast, triggers enrichment pipeline
GET    /api/simulation/:id/grounding-report     Grounding report (available after READY)
```

---

## Grounding Levels

Every opinion is labeled with how much real-post evidence supports it:

| Level | Meaning | Color in UI |
|-------|---------|-------------|
| `high` | 3+ topic-relevant public posts found | Green |
| `medium` | 1–2 relevant posts found | Yellow |
| `low` | No direct posts, interests/follows suggest alignment | Orange |
| `inferred` | No evidence — LLM inference only, clearly labeled | Purple |

---

## Design Decisions

**Named real people, not anonymized.** Agents simulate with their real name and background. The system is designed for researchers and practitioners who need attribution.

**Synthetic fallback is always available.** Any slot Nyne can't fill (no LinkedIn URL match, enrichment failure) falls back to an LLM-generated persona tagged `source: "synthetic_fallback"`. These are visually distinct in the UI and counted separately in the grounding report.

**Opinion grounding is hard-constrained.** The `OpinionExtractor` LLM prompt explicitly forbids inventing stances not evidenced by actual posts. When evidence is thin, it says so. `grounding_level` is the primary trust signal.

**Fully backward-compatible.** The existing synthetic preparation pipeline (`POST /api/simulation/prepare`, `OasisProfileGenerator`, all existing API endpoints) is untouched. Not using the cast assembly step produces identical behavior to the original MiroFish.

**File-based persistence matching existing patterns.** All new state (groups, enrichment progress, grounding report) is stored as JSON files alongside existing simulation data. No new dependencies.

---

## What Remains To Wire Up

### ✅ ~~Wire `Step2CastAssembly.vue` into the wizard~~ — Done

`SimulationView.vue` now manages two sub-phases. See "What Was Wired" below.

### ✅ ~~Pass `useRealPeople` into `Step2EnvSetup`~~ — Done

`useRealPeople` is a reactive ref in `SimulationView.vue`, set to `true` on cast approval.

### ✅ ~~Verify `SimulationConfigGenerator` patching~~ — Done

All `AgentActivityConfig` fields match exactly. See "What Was Wired" below.

### 1. Test with a live Nyne API key ← only remaining step

Set in your `.env`:
```env
NYNE_API_KEY=your_nyne_api_key
NYNE_API_SECRET=your_nyne_api_secret
```

The enrichment pipeline is written against the live Nyne API contract (matching `nyne_enrich/nyne_batch_enrich.py`) but has not yet been run end-to-end against production.

---

## What Was Wired

### `SimulationView.vue` — two sub-phases

`SimulationView.vue` (the `/simulation/:id` route) manages two sub-phases:

| `simPhase` | Component shown | Triggered by |
|------------|-----------------|--------------|
| `'cast'` (default) | `Step2CastAssembly` | Arriving at `/simulation/:id` |
| `'prepare'` | `Step2EnvSetup` | User approves cast or clicks Skip |

Navigation:
- **Approve cast** → `useRealPeople=true`, `simPhase='prepare'`
- **Skip (synthetic mode)** → `useRealPeople=false`, `simPhase='prepare'`
- **Back from prepare** → returns to `simPhase='cast'` (stays in same route)
- **Back from cast** → navigates to `/process/:projectId`

The `eventDescription` passed to `Step2CastAssembly` is pulled from `simulation.simulation_requirement` (loaded on mount).

### `AgentActivityConfig` patching — verified correct

All private `_` attributes attached by `real_persona_builder.py` map to real fields in `AgentActivityConfig`:

| Attribute | Field | Type |
|-----------|-------|------|
| `_activity_level` | `activity_level` | `float` |
| `_sentiment_bias` | `sentiment_bias` | `float` |
| `_stance` | `stance` | `str` |
| `_active_hours` | `active_hours` | `List[int]` |
| `_influence_weight` | `influence_weight` | `float` |

---

## File Tree (new files only)

```
MiroFish/
├── NYNE_INTEGRATION.md                          ← this file
├── .gitignore                                   ← updated (Nyne runtime artifacts)
├── README.md                                    ← updated (Nyne section added)
├── backend/
│   ├── .env.example                             ← NEW
│   └── app/
│       ├── config.py                            ← modified
│       ├── api/
│       │   └── simulation.py                    ← modified (8 endpoints appended)
│       └── services/
│           ├── simulation_manager.py            ← modified
│           ├── nyne/                            ← NEW package
│           │   ├── __init__.py
│           │   ├── nyne_client.py               — Nyne API wrapper
│           │   ├── cast_assembler.py            — group/cast assembly
│           │   ├── enrichment_pipeline.py       — parallel async enrichment
│           │   └── opinion_extractor.py         — opinion grounding
│           └── persona/                         ← NEW package
│               ├── __init__.py
│               └── real_persona_builder.py      — NynePersonData → OasisAgentProfile
└── frontend/
    └── src/
        ├── api/
        │   └── simulation.js                    ← modified (9 functions added)
        ├── views/
        │   └── SimulationView.vue               ← modified (cast→prepare sub-phases)
        └── components/
            ├── Step2CastAssembly.vue            ← NEW
            └── Step2EnvSetup.vue                ← modified
```

---

## Environment Variables

```env
# Required for real-people mode
NYNE_API_KEY=your_nyne_api_key
NYNE_API_SECRET=your_nyne_api_secret

# Optional tuning (defaults shown)
NYNE_MAX_CONCURRENT=10     # parallel enrichment threads
NYNE_POLL_INTERVAL=4       # seconds between Nyne status polls
NYNE_POLL_TIMEOUT=500      # max seconds to wait for a Nyne job

# Existing required vars (unchanged)
LLM_API_KEY=...
LLM_BASE_URL=...
LLM_MODEL_NAME=...
ZEP_API_KEY=...
```
