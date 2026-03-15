# MiroFish × Nyne Integration — Progress & Architecture

## What This Is

MiroFish is a swarm intelligence social-media simulation engine (built on [OASIS/CAMEL-AI](https://github.com/camel-ai/oasis)). It extracts entities from a seed document, builds agent personas, then runs parallel Twitter + Reddit simulations to predict public opinion trajectories.

This integration replaces *synthetic* LLM-fabricated personas with **real people enriched via Nyne.ai** — grounding every agent's stance, voice, and behavior in actual public posts and verified career data.

---

## Architecture Overview

```
[1] EVENT INPUT
    Document upload OR plain-text topic
         ↓
[2] EVENT ANALYSIS  (unchanged — Zep graph + entity extraction)
         ↓
[3] CAST ASSEMBLY  ← NEW STEP (Step2CastAssembly.vue)
    LLM proposes stakeholder groups
    ├── Named entities from doc → Nyne search by name
    ├── Auto-generated archetypes → Nyne person search per group
    └── User: add groups / remove / CSV upload / paste LinkedIn URLs
    [User approves cast]
         ↓
[4] NYNE ENRICHMENT PIPELINE  ← NEW (parallel async, background thread)
    Per cast member with a LinkedIn URL:
      → career history, current role, education, skills
      → newsfeed (LinkedIn + Twitter posts)
      → social follower/connection counts
      → interest clusters (psychographic)
    Synthetic fallback for any Nyne gap
         ↓
[5] OPINION EXTRACTION  ← NEW
    Per person + topic:
      → filter newsfeed for topic-relevant posts
      → LLM synthesis CONSTRAINED by actual post evidence
      → stance, sentiment_bias, grounding_level, key_positions (cited)
         ↓
[6] REAL PERSONA BUILDER  ← NEW (replaces OasisProfileGenerator for real people)
    NynePersonData + PersonOpinionProfile → OasisAgentProfile
      → 1200-word persona narrative constrained by real facts
      → behavioral params derived from real social data
         ↓
[7] OASIS SIMULATION  (unchanged)
         ↓
[8] REPORT + GROUNDING REPORT  ← enhanced
    Real citations to actual posts as evidence
```

---

## What Was Built

### New Backend Services

| File | Status | Description |
|------|--------|-------------|
| `backend/app/services/nyne/__init__.py` | ✅ Done | Package marker |
| `backend/app/services/nyne/nyne_client.py` | ✅ Done | Nyne API wrapper with async submit→poll pattern. `NynePersonData` dataclass + `NyneClient` with `enrich_person`, `search_person`, `batch_enrich`. Auth via `NYNE_API_KEY` + `NYNE_API_SECRET`. |
| `backend/app/services/nyne/cast_assembler.py` | ✅ Done | `StakeholderGroup` + `CastMember` dataclasses. `CastAssembler` generates groups via LLM, populates via Nyne search / CSV / direct URLs, fills synthetic fallbacks. Persists to `cast_groups.json`. |
| `backend/app/services/nyne/enrichment_pipeline.py` | ✅ Done | `EnrichmentPipeline.run()` — parallel async enrichment via `ThreadPoolExecutor`. Saves per-person `NynePersonData` to `nyne_enrichment/{hash}.json` incrementally. Tracks progress in `enrichment_progress.json`. |
| `backend/app/services/nyne/opinion_extractor.py` | ✅ Done | `PersonOpinionProfile` dataclass. `OpinionExtractor.extract()` — keyword + LLM semantic filter of newsfeed, LLM synthesis constrained to actual post evidence, `grounding_level` assignment. |
| `backend/app/services/persona/__init__.py` | ✅ Done | Package marker |
| `backend/app/services/persona/real_persona_builder.py` | ✅ Done | `RealPersonaBuilder.build()` → `OasisAgentProfile`. Field mapping: real Twitter followers → `follower_count`, real LinkedIn connections → `friend_count`, `log10(followers+1)/6` → `influence_weight`, post timestamps → `active_hours`, real posts → `sentiment_bias`/`stance`. Falls back to `OasisProfileGenerator` for synthetic members. |

### Modified Backend Files

| File | Change |
|------|--------|
| `backend/app/config.py` | Added `NYNE_API_KEY`, `NYNE_API_SECRET`, `NYNE_BASE_URL`, `NYNE_MAX_CONCURRENT`, `NYNE_POLL_INTERVAL`, `NYNE_POLL_TIMEOUT` |
| `backend/app/services/simulation_manager.py` | Added `SimulationStatus` enum values (`CASTING`, `ENRICHING`, `EXTRACTING_OPINIONS`, `BUILDING_PERSONAS`). Added `use_real_people`, `groups_generated`, `groups_approved`, `enrichment_complete` to `SimulationState`. Added `prepare_simulation_real_people()` pipeline method. Added `_save_grounding_report()`. |
| `backend/app/api/simulation.py` | Added 8 new endpoints (additive — zero existing endpoints changed): `groups/generate`, `groups/populate`, `groups/upload-csv`, `groups/status`, `groups/approve`, `groups/:id` (PATCH/DELETE), `grounding-report`. |
| `backend/.env.example` | Created with full env var documentation |

### New Frontend Components

| File | Status | Description |
|------|--------|-------------|
| `frontend/src/components/Step2CastAssembly.vue` | ✅ Done | Full Vue 3 Composition API component. 3 phases: (01) Generate Groups — LLM proposes stakeholder groups, (02) Review & Curate Cast — group cards with member lists, per-group CSV/URL/Nyne-search input, add/remove groups, (03) Enrichment Progress — status grid polling `/groups/status`. Emits `cast-approved` when enrichment completes. |

### Modified Frontend Files

| File | Change |
|------|--------|
| `frontend/src/api/simulation.js` | Added `generateGroups`, `getGroups`, `populateGroup`, `uploadGroupCSV`, `getGroupsStatus`, `approveGroups`, `updateGroup`, `deleteGroup`, `getGroundingReport` |
| `frontend/src/components/Step2EnvSetup.vue` | Added `useRealPeople` prop. When true: shows Step 01b enrichment progress card with phase bar, member status grid (enriching/complete/failed/synthetic chips), grounding report summary. Polls `/groups/status` every 3s. |

---

## Data Flow — Grounding Levels

Every opinion is labeled with how much real-post evidence backs it:

| Level | Meaning |
|-------|---------|
| `high` | 3+ direct relevant posts found |
| `medium` | 1-2 relevant posts found |
| `low` | No direct posts, but interests/follows suggest alignment |
| `inferred` | No evidence — LLM inference only, clearly labeled |

These propagate into `grounding_report.json` (saved per simulation) and are surfaced in the UI.

---

## API Endpoints Added

```
POST   /api/simulation/:id/groups/generate      — LLM proposes stakeholder groups
GET    /api/simulation/:id/groups               — get current group list
PATCH  /api/simulation/:id/groups/:gid          — update group name/criteria/count
DELETE /api/simulation/:id/groups/:gid          — remove a group
POST   /api/simulation/:id/groups/populate      — populate group via nyne_search or urls
POST   /api/simulation/:id/groups/upload-csv    — populate group via CSV file
GET    /api/simulation/:id/groups/status        — real-time enrichment progress polling
POST   /api/simulation/:id/groups/approve       — approve cast, trigger enrichment pipeline
GET    /api/simulation/:id/grounding-report     — get grounding report after prep completes
```

All existing endpoints are **unchanged** — this is a fully additive integration.

---

## Key Design Decisions

**1. Population-based casting, not entity-extraction**
The old flow enriched whatever named entities appeared in the article. This flow lets the user define *who are ALL relevant stakeholders* — even people never mentioned in the document.

**2. Real people, named**
Agents simulate with their real name, real career background, and opinions derived from their actual public posts. No anonymization.

**3. Synthetic fallback, clearly labeled**
Any slot Nyne can't fill gets an LLM-generated synthetic persona tagged `source: "synthetic_fallback"`. These are visually distinct in the UI and labeled in the grounding report.

**4. Opinion grounding is constrained**
The `OpinionExtractor` LLM prompt explicitly forbids inventing stances not evidenced by actual posts. If evidence is thin, the output says so — the `grounding_level` field reflects this.

**5. Backward compatible**
The existing synthetic preparation pipeline (`POST /api/simulation/prepare`) is untouched. Pass `use_real_people: false` (or just don't use the cast assembly step) to get the original behavior.

---

## Environment Variables Required

```bash
# Nyne API credentials
NYNE_API_KEY=your_nyne_api_key
NYNE_API_SECRET=your_nyne_api_secret

# Optional tuning
NYNE_MAX_CONCURRENT=10     # parallel enrichment threads
NYNE_POLL_INTERVAL=4       # seconds between Nyne status polls
NYNE_POLL_TIMEOUT=500      # max seconds to wait for Nyne job
```

---

## What Remains To Wire Up

The backend and frontend modules are complete. To make the full flow work end-to-end:

1. **Wire `Step2CastAssembly.vue` into the parent wizard** (`App.vue` or equivalent): insert it as the new Step 2, passing `simulationId` from Step 1 (env setup), and listening for `cast-approved` to advance to Step 3.

2. **Pass `useRealPeople` flag** into `Step2EnvSetup.vue` from the wizard when the user came through the cast assembly path.

3. **Test with a real Nyne API key** — the enrichment pipeline is written against the live Nyne API contract (matching `nyne_enrich/nyne_batch_enrich.py`), but has not yet been run end-to-end.

4. **Verify `SimulationConfigGenerator` patching** in `simulation_manager.py` — the `_activity_level`, `_stance`, `_sentiment_bias` attributes attached to `OasisAgentProfile` objects need to be confirmed to propagate correctly into the OASIS `AgentActivityConfig` objects.

---

## File Tree — New Files Only

```
MiroFish/
├── NYNE_INTEGRATION.md              ← this file
├── .gitignore                       ← updated with Nyne runtime artifacts
├── backend/
│   ├── .env.example                 ← NEW: full env var documentation
│   └── app/
│       ├── config.py                ← modified: Nyne config block
│       ├── api/
│       │   └── simulation.py        ← modified: 8 new endpoints appended
│       └── services/
│           ├── simulation_manager.py ← modified: real-people pipeline
│           ├── nyne/                 ← NEW package
│           │   ├── __init__.py
│           │   ├── nyne_client.py
│           │   ├── cast_assembler.py
│           │   ├── enrichment_pipeline.py
│           │   └── opinion_extractor.py
│           └── persona/              ← NEW package
│               ├── __init__.py
│               └── real_persona_builder.py
└── frontend/
    └── src/
        ├── api/
        │   └── simulation.js         ← modified: 9 new API functions
        └── components/
            ├── Step2CastAssembly.vue  ← NEW: cast assembly wizard step
            └── Step2EnvSetup.vue      ← modified: real-people enrichment UI
```
