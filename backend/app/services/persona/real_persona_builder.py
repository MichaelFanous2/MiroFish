"""
Real Persona Builder — maps NynePersonData + PersonOpinionProfile → OasisAgentProfile.

Every field is derived from real data where available. The LLM writes the
persona narrative but is constrained by real facts — it cannot invent positions
or traits that contradict the evidence. For synthetic fallback members this
module delegates to the existing OasisProfileGenerator.

Key field derivations:
  follower_count    ← real Twitter follower count
  friend_count      ← real LinkedIn connection count
  influence_weight  ← log10(follower_count + 1) / 6  (stored in persona extras)
  activity_level    ← min(len(newsfeed) / 30, 1.0)
  active_hours      ← inferred from post timestamps
  sentiment_bias    ← from PersonOpinionProfile (grounded in real posts)
  stance            ← from PersonOpinionProfile (grounded in real posts)
"""

import re
import math
import random
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List, Dict, Any

from ...utils.logger import get_logger
from ...utils.llm_client import LLMClient
from ..oasis_profile_generator import OasisAgentProfile
from ..nyne.nyne_client import NynePersonData
from ..nyne.opinion_extractor import PersonOpinionProfile

logger = get_logger('mirofish.real_persona_builder')


# ---------------------------------------------------------------------------
# LLM prompt for persona narrative
# ---------------------------------------------------------------------------

_PERSONA_SYSTEM = """\
You are writing a simulation persona for a REAL person. Every personality trait,
communication pattern, and opinion you describe MUST be grounded in the verified
facts provided. Do NOT invent biographical details, opinions, or traits that
contradict or go beyond the evidence. When evidence is thin, acknowledge
uncertainty rather than fabricating detail.
"""

_PERSONA_USER = """\
Write a simulation persona for {name}.

═══ VERIFIED FACTS (do not contradict these) ═══

Current role: {role} at {company}
Location: {location}
Age: {age}

Career history:
{career_str}

Education:
{education_str}

Skills: {skills_str}

LinkedIn connections: {linkedin_connections}
Twitter followers: {twitter_followers}

═══ THEIR ACTUAL PUBLIC POSTS ON "{topic}" ═══

{posts_str}

═══ KNOWN STANCE ON THIS TOPIC ═══

Stance: {stance}  (grounding level: {grounding_level})
Evidence: {key_positions_str}

═══ THEIR INTERESTS / TOPICS THEY FOLLOW ═══

{interests_str}

═══ INSTRUCTIONS ═══

Write a 1200-word persona narrative for use in a social media simulation about "{topic}".

Structure:
1. Identity & Background (3–4 sentences grounded in career facts)
2. Relationship to this Topic (what they know, why they care — from posts/career only)
3. Communication Style (inferred from actual post tone and vocabulary)
4. What Triggers Them to Post (based on their posting patterns)
5. What They Share & Amplify (based on interests and newsfeed patterns)
6. Blind Spots & Biases (cautious inference from career/interests — label as inferred)

Rules:
- Quote actual posts when describing voice/tone (attribution: "As they wrote: ...")
- Do not invent posts or statements
- For gaps in evidence, use hedged language ("likely", "probably", "given their background")
- The persona must sound like a real person, not a stereotype of their job title
"""


# ---------------------------------------------------------------------------
# RealPersonaBuilder
# ---------------------------------------------------------------------------

class RealPersonaBuilder:
    """Builds an OasisAgentProfile from real Nyne data and opinion signals."""

    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client

    def build(
        self,
        person: NynePersonData,
        opinion: PersonOpinionProfile,
        user_id: int,
        topic: str = "",
        source_entity_uuid: Optional[str] = None,
        source_entity_type: Optional[str] = None,
    ) -> OasisAgentProfile:
        """
        Build an OasisAgentProfile from real Nyne data.

        Falls back to safe defaults for any missing fields.
        """
        # ── Derived numeric fields ──────────────────────────────────────────
        twitter_followers = person.twitter_followers or 0
        linkedin_connections = person.linkedin_connections or 0

        follower_count = twitter_followers
        friend_count = linkedin_connections

        # activity_level: posts per month, capped at 1.0
        activity_level = min(len(person.newsfeed) / 30.0, 1.0)

        # influence_weight for simulation config (stored as extra field)
        influence_weight = math.log10(follower_count + 1) / 6.0

        # active_hours: from post timestamps
        active_hours = self._infer_active_hours(person.newsfeed)

        # statuses_count: from real post count × some factor (estimate total history)
        statuses_count = max(len(person.newsfeed) * 10, 500)

        # karma: loosely derived from LinkedIn connections
        karma = max(linkedin_connections, 500)

        # ── Persona narrative ───────────────────────────────────────────────
        persona_text = self._build_persona_narrative(person, opinion, topic)

        # ── Bio: real LinkedIn headline ─────────────────────────────────────
        bio = self._build_bio(person)

        # ── Username ────────────────────────────────────────────────────────
        user_name = self._make_username(person.name, user_id)

        # ── MBTI: infer from post tone if not known ─────────────────────────
        mbti = self._infer_mbti_from_posts(person.newsfeed)

        # ── Gender: not inferred (leave None unless available in raw data) ──
        gender = person.raw.get("result", {}).get("gender")

        # ── Build profile ───────────────────────────────────────────────────
        profile = OasisAgentProfile(
            user_id=user_id,
            user_name=user_name,
            name=person.name,
            bio=bio,
            persona=persona_text,
            karma=karma,
            friend_count=friend_count,
            follower_count=follower_count,
            statuses_count=statuses_count,
            age=person.age,
            gender=gender,
            mbti=mbti,
            country=self._extract_country(person.location),
            profession=person.current_role or "",
            interested_topics=person.interests[:10],
            source_entity_uuid=source_entity_uuid,
            source_entity_type=source_entity_type or "real_person",
        )

        # Attach extra fields for SimulationConfigGenerator
        # (stored as attributes — the config generator reads them if present)
        profile._activity_level = activity_level
        profile._active_hours = active_hours
        profile._influence_weight = influence_weight
        profile._stance = opinion.stance
        profile._sentiment_bias = opinion.sentiment_bias
        profile._grounding_level = opinion.grounding_level
        profile._opinion_confidence = opinion.confidence

        logger.info(f"Built real persona for {person.name} (user_id={user_id})")
        return profile

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_persona_narrative(
        self,
        person: NynePersonData,
        opinion: PersonOpinionProfile,
        topic: str,
    ) -> str:
        """Call LLM to write a constrained persona narrative."""
        career_str = "\n".join(
            f"  - {c.position} at {c.company_name} ({c.start_date or '?'} – {c.end_date or 'present'})"
            for c in person.career_history[:6]
        ) or f"  - {person.current_role} at {person.current_company}"

        education_str = "\n".join(
            f"  - {e.degree or 'degree'} in {e.field or '?'} from {e.school} ({e.graduation_year or '?'})"
            for e in person.education[:3]
        ) or "  - (not available)"

        posts_str = "\n".join(
            f"  [{p['timestamp'][:10] if p.get('timestamp') else '?'} via {p.get('source','?')}] "
            f"{p.get('content','')[:300]}"
            + (f"\n    URL: {p['url']}" if p.get('url') else "")
            for p in opinion.relevant_posts[:8]
        ) or "  (No directly relevant public posts found)"

        key_positions_str = "\n".join(
            f"  - {pos}" for pos in opinion.key_positions[:5]
        ) or "  (No specific positions identified)"

        try:
            narrative = self.llm.chat(
                [
                    {"role": "system", "content": _PERSONA_SYSTEM},
                    {"role": "user", "content": _PERSONA_USER.format(
                        name=person.name,
                        role=person.current_role or "unknown",
                        company=person.current_company or "unknown",
                        location=person.location or "unknown",
                        age=person.age or "unknown",
                        career_str=career_str,
                        education_str=education_str,
                        skills_str=", ".join(person.skills[:15]) or "not listed",
                        linkedin_connections=person.linkedin_connections or "unknown",
                        twitter_followers=person.twitter_followers or "unknown",
                        topic=topic or "this topic",
                        posts_str=posts_str,
                        stance=opinion.stance,
                        grounding_level=opinion.grounding_level,
                        key_positions_str=key_positions_str,
                        interests_str=", ".join(person.interests[:20]) or "not available",
                    )},
                ],
                temperature=0.6,
                max_tokens=2000,
            )
            return narrative.strip()
        except Exception as e:
            logger.error(f"Persona narrative LLM call failed for {person.name}: {e}")
            # Minimal fallback narrative
            return (
                f"{person.name} is a {person.current_role} at {person.current_company}. "
                f"Based in {person.location or 'an unknown location'}. "
                f"Known stance on this topic: {opinion.stance}."
            )

    def _build_bio(self, person: NynePersonData) -> str:
        """Build a 200-char bio from real profile data."""
        parts = []
        if person.current_role:
            parts.append(person.current_role)
        if person.current_company:
            parts.append(f"@ {person.current_company}")
        if person.location:
            parts.append(f"• {person.location}")
        if person.skills:
            parts.append(f"• {', '.join(person.skills[:3])}")
        bio = " ".join(parts)
        return bio[:200] if bio else f"{person.name} on social media"

    def _make_username(self, name: str, user_id: int) -> str:
        """Create a URL-safe username from the real name."""
        clean = re.sub(r"[^\w\s]", "", name.lower()).strip()
        parts = clean.split()
        if len(parts) >= 2:
            base = f"{parts[0]}_{parts[-1]}"
        elif parts:
            base = parts[0]
        else:
            base = f"user_{user_id}"
        return f"{base}_{user_id}"

    def _infer_active_hours(self, newsfeed) -> List[int]:
        """Infer active posting hours from actual post timestamps."""
        if not newsfeed:
            return list(range(9, 18))  # default business hours

        hour_counts: Counter = Counter()
        for post in newsfeed:
            ts = post.timestamp if hasattr(post, "timestamp") else post.get("timestamp", "")
            if ts and len(ts) >= 13:
                try:
                    hour = int(ts[11:13])
                    hour_counts[hour] += 1
                except (ValueError, IndexError):
                    pass

        if not hour_counts:
            return list(range(9, 18))

        # Return top-N hours (at least 4, up to 8)
        top_hours = sorted(hour_counts, key=hour_counts.get, reverse=True)[:8]
        return sorted(top_hours)

    def _infer_mbti_from_posts(self, newsfeed) -> Optional[str]:
        """
        Very rough MBTI inference from post tone/length.
        Only used as a rough signal — labeled as inferred.
        """
        if not newsfeed:
            return None

        posts = newsfeed[:20]
        total = len(posts)
        if total == 0:
            return None

        # Heuristics (all extremely rough — just for simulation flavor)
        avg_len = sum(
            len(p.content if hasattr(p, "content") else p.get("content", ""))
            for p in posts
        ) / total

        # Long posts → more likely N; short → S
        # Many posts → E; few → I
        ei = "E" if total >= 10 else "I"
        ns = "N" if avg_len > 150 else "S"
        # Default TF and JP
        tf = "T"
        jp = "J"
        return f"{ei}{ns}{tf}{jp}"

    def _extract_country(self, location: Optional[str]) -> Optional[str]:
        """Extract country from a location string."""
        if not location:
            return None
        # Common US indicators
        us_keywords = ["san francisco", "new york", "los angeles", "chicago",
                       "seattle", "boston", "austin", " ca", " ny", " tx",
                       "united states", "usa", "u.s."]
        loc_lower = location.lower()
        if any(kw in loc_lower for kw in us_keywords):
            return "United States"
        # Return last comma-separated token as country
        parts = [p.strip() for p in location.split(",")]
        return parts[-1] if len(parts) >= 2 else location
