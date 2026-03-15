"""
Cast Assembler — builds the stakeholder cast for a simulation.

The unit of casting is a StakeholderGroup, not an individual. Given an event
description and optional named entities from a document, this service:

  1. Generates relevant stakeholder groups (via LLM)
  2. Populates each group with real people via Nyne search, CSV upload,
     or direct LinkedIn URL input
  3. Fills any unfilled slots with synthetic fallback markers

Groups and members are persisted to disk so the Flask process and the
simulation manager can both access them.
"""

import os
import uuid
import json
import csv
import io
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

from ...config import Config
from ...utils.logger import get_logger
from ...utils.llm_client import LLMClient
from ..zep_entity_reader import EntityNode

logger = get_logger('mirofish.cast_assembler')


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class CastMember:
    """One person in the simulation cast."""
    member_id: str
    name: str
    role: str
    group_id: str
    linkedin_url: Optional[str] = None
    # "nyne_search" | "csv" | "named_entity" | "user_url" | "synthetic_fallback"
    source: str = "synthetic_fallback"
    confidence: float = 0.0
    entity_uuid: Optional[str] = None   # links back to Zep entity if from doc
    enriched: bool = False              # True once NynePersonData has been fetched

    def to_dict(self) -> Dict[str, Any]:
        return {
            "member_id": self.member_id,
            "name": self.name,
            "role": self.role,
            "group_id": self.group_id,
            "linkedin_url": self.linkedin_url,
            "source": self.source,
            "confidence": self.confidence,
            "entity_uuid": self.entity_uuid,
            "enriched": self.enriched,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CastMember":
        return cls(
            member_id=data["member_id"],
            name=data["name"],
            role=data["role"],
            group_id=data["group_id"],
            linkedin_url=data.get("linkedin_url"),
            source=data.get("source", "synthetic_fallback"),
            confidence=data.get("confidence", 0.0),
            entity_uuid=data.get("entity_uuid"),
            enriched=data.get("enriched", False),
        )


@dataclass
class StakeholderGroup:
    """A category of people relevant to the event being simulated."""
    group_id: str
    name: str
    criteria: str           # description used to search Nyne
    target_count: int
    # "auto_named_entity" | "auto_archetype" | "user_defined" | "csv"
    source: str = "auto_archetype"
    members: List[CastMember] = field(default_factory=list)
    # "pending" | "populated" | "partial" | "fallback_only"
    status: str = "pending"

    @property
    def filled_count(self) -> int:
        return sum(1 for m in self.members if m.source != "synthetic_fallback")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "group_id": self.group_id,
            "name": self.name,
            "criteria": self.criteria,
            "target_count": self.target_count,
            "source": self.source,
            "members": [m.to_dict() for m in self.members],
            "status": self.status,
            "filled_count": self.filled_count,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StakeholderGroup":
        g = cls(
            group_id=data["group_id"],
            name=data["name"],
            criteria=data.get("criteria", ""),
            target_count=data.get("target_count", 5),
            source=data.get("source", "auto_archetype"),
            status=data.get("status", "pending"),
        )
        g.members = [CastMember.from_dict(m) for m in data.get("members", [])]
        return g


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def _groups_path(simulation_dir: str) -> str:
    return os.path.join(simulation_dir, "cast_groups.json")


def save_groups(groups: List[StakeholderGroup], simulation_dir: str):
    """Persist groups list to disk."""
    path = _groups_path(simulation_dir)
    with open(path, "w", encoding="utf-8") as f:
        json.dump([g.to_dict() for g in groups], f, ensure_ascii=False, indent=2)


def load_groups(simulation_dir: str) -> List[StakeholderGroup]:
    """Load groups list from disk. Returns [] if not found."""
    path = _groups_path(simulation_dir)
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return [StakeholderGroup.from_dict(g) for g in data]


# ---------------------------------------------------------------------------
# CastAssembler
# ---------------------------------------------------------------------------

_GROUP_GENERATION_PROMPT = """\
You are designing the cast of a social simulation about the following event.

EVENT DESCRIPTION:
{event_description}

NAMED ENTITIES ALREADY IN THE SOURCE DOCUMENT:
{named_entities_str}

Your task: propose stakeholder groups that should be represented in the simulation.
For each group, include people who would realistically react to this event.
Include diverse perspectives (supporters, opponents, observers, affected parties).

Rules:
- Always include one group for "Named entities from source document" (the people explicitly mentioned).
- Add 4–7 additional groups of real stakeholder types relevant to this event.
- Each group should have 3–8 people.
- Be specific about criteria (e.g. "US Senators on the Senate Banking Committee" not "politicians").
- Return JSON only.

Return this exact JSON structure:
{{
  "groups": [
    {{
      "name": "Group display name",
      "criteria": "Nyne search criteria — who to look for and why they matter",
      "target_count": 5,
      "source": "auto_named_entity"  // or "auto_archetype"
    }}
  ]
}}
"""


class CastAssembler:
    """
    Assembles a stakeholder cast for a simulation.

    Usage:
        assembler = CastAssembler(llm_client, nyne_client)
        groups = assembler.generate_groups_from_event(event_desc, named_entities)
        groups = assembler.populate_group_via_nyne(groups[1])
        save_groups(groups, sim_dir)
    """

    def __init__(self, llm_client: LLMClient, nyne_client=None):
        self.llm = llm_client
        self.nyne = nyne_client  # optional; only needed for Nyne population

    # ------------------------------------------------------------------
    # Group generation
    # ------------------------------------------------------------------

    def generate_groups_from_event(
        self,
        event_description: str,
        named_entities: Optional[List[EntityNode]] = None,
    ) -> List[StakeholderGroup]:
        """
        Use LLM to propose stakeholder groups for an event.

        Always creates one group for named entities from the source document.
        """
        named_entities = named_entities or []
        named_entities_str = "\n".join(
            f"- {e.name} ({e.get_entity_type()})"
            for e in named_entities
        ) or "(none — pure topic-based simulation)"

        prompt = _GROUP_GENERATION_PROMPT.format(
            event_description=event_description,
            named_entities_str=named_entities_str,
        )

        try:
            raw = self.llm.chat_json([{"role": "user", "content": prompt}], temperature=0.4)
            groups_data = raw.get("groups", [])
        except Exception as e:
            logger.error(f"LLM group generation failed: {e}")
            groups_data = []

        groups: List[StakeholderGroup] = []

        # Always ensure the named-entity group exists and is first
        has_named_entity_group = any(
            g.get("source") == "auto_named_entity" for g in groups_data
        )

        if named_entities and not has_named_entity_group:
            groups_data.insert(0, {
                "name": "Named entities from source document",
                "criteria": "People explicitly mentioned in the source document",
                "target_count": len(named_entities),
                "source": "auto_named_entity",
            })

        for g in groups_data:
            group = StakeholderGroup(
                group_id=f"grp_{uuid.uuid4().hex[:10]}",
                name=g.get("name", "Unknown Group"),
                criteria=g.get("criteria", ""),
                target_count=int(g.get("target_count", 5)),
                source=g.get("source", "auto_archetype"),
            )
            groups.append(group)

        # Pre-populate the named entity group from Zep entities
        if named_entities:
            for group in groups:
                if group.source == "auto_named_entity":
                    self._populate_named_entity_group(group, named_entities)
                    break

        logger.info(f"Generated {len(groups)} stakeholder groups")
        return groups

    def _populate_named_entity_group(
        self,
        group: StakeholderGroup,
        entities: List[EntityNode],
    ):
        """Fill the named-entity group from Zep entity list."""
        for entity in entities:
            member = CastMember(
                member_id=f"mbr_{uuid.uuid4().hex[:10]}",
                name=entity.name,
                role=entity.get_entity_type(),
                group_id=group.group_id,
                source="named_entity",
                confidence=0.9,
                entity_uuid=entity.uuid,
            )
            group.members.append(member)

        # Try Nyne search for LinkedIn URLs if client available
        if self.nyne:
            for member in group.members:
                if not member.linkedin_url:
                    urls = self.nyne.search_person(name=member.name)
                    if urls:
                        member.linkedin_url = urls[0]
                        member.source = "nyne_search"
                        member.confidence = 0.75

        group.status = "populated" if group.members else "pending"

    # ------------------------------------------------------------------
    # Population via Nyne search
    # ------------------------------------------------------------------

    def populate_group_via_nyne(
        self,
        group: StakeholderGroup,
        event_context: str = "",
    ) -> StakeholderGroup:
        """
        Use Nyne person search to find real people for this group.

        Searches using group.criteria as the query context.
        Fills up to group.target_count members.
        """
        if self.nyne is None:
            logger.warning("NyneClient not set — cannot populate via Nyne")
            return group

        existing_urls = {m.linkedin_url for m in group.members if m.linkedin_url}
        needed = group.target_count - len(group.members)
        if needed <= 0:
            return group

        # Build search query from criteria
        # The criteria already describes who to find; pass as context
        logger.info(f"Nyne search for group '{group.name}': {group.criteria}")
        urls = self.nyne.search_person(
            name="",
            context=f"{group.criteria}. Event: {event_context}",
        )

        added = 0
        for url in urls:
            if url in existing_urls:
                continue
            if added >= needed:
                break
            member = CastMember(
                member_id=f"mbr_{uuid.uuid4().hex[:10]}",
                name=url,           # name will be filled after enrichment
                role=group.name,
                group_id=group.group_id,
                linkedin_url=url,
                source="nyne_search",
                confidence=0.7,
            )
            group.members.append(member)
            existing_urls.add(url)
            added += 1

        group.status = "populated" if group.members else "pending"
        logger.info(f"Group '{group.name}': added {added} via Nyne search")
        return group

    # ------------------------------------------------------------------
    # Population via CSV
    # ------------------------------------------------------------------

    def populate_group_via_csv(
        self,
        group: StakeholderGroup,
        csv_content: str,
    ) -> StakeholderGroup:
        """
        Populate group from CSV text content.

        CSV must have at minimum a column containing a LinkedIn URL.
        Optional columns: name, role, title, company.
        Auto-detects the LinkedIn column by name.
        """
        existing_urls = {m.linkedin_url for m in group.members if m.linkedin_url}
        reader = csv.DictReader(io.StringIO(csv_content))
        headers = reader.fieldnames or []

        linkedin_col = next(
            (h for h in headers if "linkedin" in h.lower()), None
        )
        name_col = next(
            (h for h in headers if h.lower() in ("name", "full_name", "fullname")), None
        )
        role_col = next(
            (h for h in headers if h.lower() in ("role", "title", "position", "job_title")), None
        )

        if not linkedin_col:
            logger.warning(f"No LinkedIn column found in CSV. Headers: {headers}")
            return group

        added = 0
        for row in reader:
            url = (row.get(linkedin_col, "") or "").strip()
            if not url or "linkedin.com" not in url.lower():
                continue
            if not url.startswith("http"):
                url = "https://" + url
            if url in existing_urls:
                continue

            name = (row.get(name_col, "") if name_col else "").strip()
            role = (row.get(role_col, "") if role_col else group.name).strip()

            member = CastMember(
                member_id=f"mbr_{uuid.uuid4().hex[:10]}",
                name=name or url,
                role=role or group.name,
                group_id=group.group_id,
                linkedin_url=url,
                source="csv",
                confidence=0.95,
            )
            group.members.append(member)
            existing_urls.add(url)
            added += 1

        group.status = "populated" if group.members else "pending"
        logger.info(f"Group '{group.name}': added {added} from CSV")
        return group

    # ------------------------------------------------------------------
    # Population via direct URLs
    # ------------------------------------------------------------------

    def populate_group_via_urls(
        self,
        group: StakeholderGroup,
        urls: List[str],
    ) -> StakeholderGroup:
        """Populate group from a direct list of LinkedIn URLs."""
        existing_urls = {m.linkedin_url for m in group.members if m.linkedin_url}
        added = 0
        for url in urls:
            if not url or "linkedin.com" not in url.lower():
                continue
            if not url.startswith("http"):
                url = "https://" + url
            if url in existing_urls:
                continue

            member = CastMember(
                member_id=f"mbr_{uuid.uuid4().hex[:10]}",
                name=url,
                role=group.name,
                group_id=group.group_id,
                linkedin_url=url,
                source="user_url",
                confidence=1.0,
            )
            group.members.append(member)
            existing_urls.add(url)
            added += 1

        group.status = "populated" if group.members else "pending"
        logger.info(f"Group '{group.name}': added {added} from direct URLs")
        return group

    # ------------------------------------------------------------------
    # Synthetic fallback fill
    # ------------------------------------------------------------------

    def fill_synthetic_fallback(
        self,
        group: StakeholderGroup,
    ) -> StakeholderGroup:
        """
        Fill any remaining open slots with synthetic fallback markers.

        These members will be handled by the existing OasisProfileGenerator
        (LLM-only synthesis) during the persona building step.
        """
        current = len(group.members)
        needed = group.target_count - current
        if needed <= 0:
            return group

        for i in range(needed):
            member = CastMember(
                member_id=f"mbr_{uuid.uuid4().hex[:10]}",
                name=f"[Synthetic] {group.name} #{current + i + 1}",
                role=group.name,
                group_id=group.group_id,
                source="synthetic_fallback",
                confidence=0.0,
            )
            group.members.append(member)

        logger.info(f"Group '{group.name}': added {needed} synthetic fallback slots")
        if group.filled_count == 0:
            group.status = "fallback_only"
        else:
            group.status = "partial"
        return group

    # ------------------------------------------------------------------
    # Convenience: all members across all groups
    # ------------------------------------------------------------------

    @staticmethod
    def all_members(groups: List[StakeholderGroup]) -> List[CastMember]:
        members = []
        for g in groups:
            members.extend(g.members)
        return members

    @staticmethod
    def real_members(groups: List[StakeholderGroup]) -> List[CastMember]:
        """Members that have a LinkedIn URL (will be Nyne-enriched)."""
        return [
            m for g in groups for m in g.members
            if m.source != "synthetic_fallback" and m.linkedin_url
        ]

    @staticmethod
    def synthetic_members(groups: List[StakeholderGroup]) -> List[CastMember]:
        """Members marked as synthetic fallback."""
        return [
            m for g in groups for m in g.members
            if m.source == "synthetic_fallback"
        ]
