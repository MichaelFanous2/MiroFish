"""
Enrichment Pipeline — parallel async Nyne enrichment for a cast of people.

For each CastMember with a LinkedIn URL, fetches full NynePersonData
(career, newsfeed, social stats, etc.) and persists it to disk so:
  - The Flask process can stream real-time progress
  - The opinion extractor and persona builder can read results later
  - Context loss is safe (everything is on disk)

Synthetic fallback members are passed through without any API call.
"""

import os
import json
import time
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Callable, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from ...utils.logger import get_logger
from .nyne_client import NyneClient, NynePersonData, url_to_cache_key
from .cast_assembler import CastMember

logger = get_logger('mirofish.enrichment_pipeline')


# ---------------------------------------------------------------------------
# Progress model
# ---------------------------------------------------------------------------

@dataclass
class EnrichmentProgress:
    member_id: str
    name: str
    linkedin_url: Optional[str]
    status: str  # "pending" | "enriching" | "complete" | "failed" | "synthetic"
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "member_id": self.member_id,
            "name": self.name,
            "linkedin_url": self.linkedin_url,
            "status": self.status,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# EnrichmentPipeline
# ---------------------------------------------------------------------------

_ENRICHMENT_DIR = "nyne_enrichment"
_PROGRESS_FILE = "enrichment_progress.json"


def _enrichment_dir(simulation_dir: str) -> str:
    path = os.path.join(simulation_dir, _ENRICHMENT_DIR)
    os.makedirs(path, exist_ok=True)
    return path


def _person_path(simulation_dir: str, linkedin_url: str) -> str:
    key = url_to_cache_key(linkedin_url)
    return os.path.join(_enrichment_dir(simulation_dir), f"{key}.json")


def save_person_data(simulation_dir: str, person: NynePersonData):
    """Persist one NynePersonData to disk."""
    path = _person_path(simulation_dir, person.linkedin_url)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(person.to_dict(), f, ensure_ascii=False, indent=2)


def load_person_data(simulation_dir: str, linkedin_url: str) -> Optional[NynePersonData]:
    """Load one NynePersonData from disk. Returns None if not found."""
    path = _person_path(simulation_dir, linkedin_url)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return NynePersonData.from_dict(data)


def save_progress(simulation_dir: str, progress_list: List[EnrichmentProgress]):
    """Write the full progress list to disk for real-time polling."""
    path = os.path.join(simulation_dir, _PROGRESS_FILE)
    with open(path, "w", encoding="utf-8") as f:
        json.dump([p.to_dict() for p in progress_list], f, ensure_ascii=False, indent=2)


def load_progress(simulation_dir: str) -> List[Dict[str, Any]]:
    """Read the progress file for the /groups/status endpoint."""
    path = os.path.join(simulation_dir, _PROGRESS_FILE)
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


class EnrichmentPipeline:
    """
    Enriches a list of CastMembers via Nyne.

    Synthetic fallback members are passed through unchanged.
    Results are written to disk incrementally so progress can be polled.
    """

    def __init__(self, nyne_client: NyneClient, simulation_dir: str):
        self.nyne = nyne_client
        self.simulation_dir = simulation_dir

    def run(
        self,
        members: List[CastMember],
        progress_callback: Optional[Callable[[int, int, str, str], None]] = None,
        max_concurrent: int = 10,
        include_newsfeed: bool = True,
    ) -> Dict[str, Optional[NynePersonData]]:
        """
        Enrich all members that have a LinkedIn URL.

        progress_callback(completed, total, member_name, status)

        Returns a dict mapping member_id -> NynePersonData (or None for failures).
        Synthetic fallback members return None without any API call.
        """
        # Initialise progress tracking
        progress_map: Dict[str, EnrichmentProgress] = {}
        for m in members:
            if m.source == "synthetic_fallback" or not m.linkedin_url:
                status = "synthetic"
            else:
                # Check if already enriched on disk
                existing = load_person_data(self.simulation_dir, m.linkedin_url)
                status = "complete" if existing else "pending"
            progress_map[m.member_id] = EnrichmentProgress(
                member_id=m.member_id,
                name=m.name,
                linkedin_url=m.linkedin_url,
                status=status,
            )
        save_progress(self.simulation_dir, list(progress_map.values()))

        results: Dict[str, Optional[NynePersonData]] = {}

        # Load already-enriched members from disk
        for m in members:
            if m.linkedin_url and progress_map[m.member_id].status == "complete":
                person = load_person_data(self.simulation_dir, m.linkedin_url)
                results[m.member_id] = person

        # Filter to members that still need enrichment
        to_enrich = [
            m for m in members
            if m.linkedin_url
            and m.source != "synthetic_fallback"
            and progress_map[m.member_id].status == "pending"
        ]

        total = len(members)
        completed = sum(1 for p in progress_map.values() if p.status in ("complete", "synthetic"))

        if not to_enrich:
            logger.info("All members already enriched or synthetic")
            return results

        logger.info(f"Enriching {len(to_enrich)} members via Nyne (max_concurrent={max_concurrent})")

        with ThreadPoolExecutor(max_workers=max_concurrent) as executor:
            future_to_member = {
                executor.submit(
                    self._enrich_one,
                    m,
                    include_newsfeed,
                    progress_map,
                ): m
                for m in to_enrich
            }

            for future in as_completed(future_to_member):
                m = future_to_member[future]
                try:
                    person = future.result()
                    results[m.member_id] = person
                    completed += 1
                    status = "complete" if person else "failed"
                    progress_map[m.member_id].status = status
                    # Update name from enrichment if blank
                    if person and not m.name or m.name == m.linkedin_url:
                        progress_map[m.member_id].name = person.name or m.name
                except Exception as e:
                    logger.error(f"Enrichment failed for {m.name}: {e}")
                    results[m.member_id] = None
                    completed += 1
                    progress_map[m.member_id].status = "failed"
                    progress_map[m.member_id].error = str(e)
                finally:
                    save_progress(self.simulation_dir, list(progress_map.values()))
                    if progress_callback:
                        progress_callback(
                            completed,
                            total,
                            progress_map[m.member_id].name,
                            progress_map[m.member_id].status,
                        )

        success = sum(1 for v in results.values() if v is not None)
        logger.info(f"Enrichment complete: {success}/{len(to_enrich)} succeeded")
        return results

    def _enrich_one(
        self,
        member: CastMember,
        include_newsfeed: bool,
        progress_map: Dict[str, EnrichmentProgress],
    ) -> Optional[NynePersonData]:
        """Enrich a single member and save to disk."""
        progress_map[member.member_id].status = "enriching"
        save_progress(self.simulation_dir, list(progress_map.values()))

        person = self.nyne.enrich_person(
            linkedin_url=member.linkedin_url,
            include_newsfeed=include_newsfeed,
            ai_enhanced=True,
        )

        if person:
            # Fill in name on the member if it was missing
            if not member.name or member.name == member.linkedin_url:
                member.name = person.name
            save_person_data(self.simulation_dir, person)
            member.enriched = True
            logger.info(f"Enriched: {person.name} ({member.linkedin_url})")
        else:
            logger.warning(f"No data returned for {member.linkedin_url}")

        return person

    def load_all_results(
        self, members: List[CastMember]
    ) -> Dict[str, Optional[NynePersonData]]:
        """Load all enrichment results from disk for a list of members."""
        results = {}
        for m in members:
            if m.linkedin_url and m.source != "synthetic_fallback":
                results[m.member_id] = load_person_data(self.simulation_dir, m.linkedin_url)
            else:
                results[m.member_id] = None
        return results
