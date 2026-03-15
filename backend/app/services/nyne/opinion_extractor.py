"""
Opinion Extractor — derives real opinion signals from Nyne enrichment data.

For each person, this service:
  1. Filters their newsfeed for posts relevant to the simulation topic
  2. Runs an LLM synthesis CONSTRAINED to their actual posts — no invented positions
  3. Assigns a grounding_level based on how much real evidence was found
  4. Produces a PersonOpinionProfile with cited stances and sentiment scores

The LLM must never invent a stance not evidenced by the real posts. When
evidence is thin, the system downgrades grounding_level to "inferred" and
the confidence score drops accordingly.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

from ...utils.logger import get_logger
from ...utils.llm_client import LLMClient
from .nyne_client import NynePersonData, NewsfeedPost

logger = get_logger('mirofish.opinion_extractor')

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class PersonOpinionProfile:
    """Real-data-grounded opinion profile for one person on one topic."""
    person_name: str
    linkedin_url: str
    topic: str

    # Core opinion signals
    stance: str = "neutral"        # "supportive" | "opposing" | "neutral" | "conflicted"
    sentiment_bias: float = 0.0    # -1.0 (strongly opposing) to 1.0 (strongly supportive)
    confidence: float = 0.0        # 0.0 = no evidence, 1.0 = very strong evidence
    grounding_level: str = "inferred"  # "high" | "medium" | "low" | "inferred"

    # Evidence
    key_positions: List[str] = field(default_factory=list)   # claims WITH citation
    relevant_posts: List[Dict[str, Any]] = field(default_factory=list)   # actual posts
    information_diet: List[str] = field(default_factory=list)  # relevant following
    advocacy_style: str = "observer"  # "vocal" | "passive" | "observer" | "amplifier"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "person_name": self.person_name,
            "linkedin_url": self.linkedin_url,
            "topic": self.topic,
            "stance": self.stance,
            "sentiment_bias": self.sentiment_bias,
            "confidence": self.confidence,
            "grounding_level": self.grounding_level,
            "key_positions": self.key_positions,
            "relevant_posts": self.relevant_posts,
            "information_diet": self.information_diet,
            "advocacy_style": self.advocacy_style,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PersonOpinionProfile":
        p = cls(
            person_name=data.get("person_name", ""),
            linkedin_url=data.get("linkedin_url", ""),
            topic=data.get("topic", ""),
        )
        p.stance = data.get("stance", "neutral")
        p.sentiment_bias = float(data.get("sentiment_bias", 0.0))
        p.confidence = float(data.get("confidence", 0.0))
        p.grounding_level = data.get("grounding_level", "inferred")
        p.key_positions = data.get("key_positions", [])
        p.relevant_posts = data.get("relevant_posts", [])
        p.information_diet = data.get("information_diet", [])
        p.advocacy_style = data.get("advocacy_style", "observer")
        return p


# ---------------------------------------------------------------------------
# LLM prompt
# ---------------------------------------------------------------------------

_OPINION_SYSTEM = """\
You are a social media behavior analyst. Your job is to determine a real person's
stance on a given topic based ONLY on their actual public posts. You MUST NOT invent
positions that are not evidenced in the posts. When evidence is absent or ambiguous,
you MUST reflect that with lower confidence and grounding_level = "inferred".
"""

_OPINION_USER = """\
PERSON: {name}
CURRENT ROLE: {role} at {company}
TOPIC: {topic}

THEIR ACTUAL RECENT POSTS (up to {post_count} shown):
{posts_str}

THEIR CAREER CONTEXT:
{career_str}

THEIR INTERESTS/TOPICS THEY FOLLOW:
{interests_str}

Based ONLY on the above evidence, provide a JSON opinion profile. Do not invent.

Return this exact JSON:
{{
  "stance": "supportive" | "opposing" | "neutral" | "conflicted",
  "sentiment_bias": <float -1.0 to 1.0>,
  "confidence": <float 0.0 to 1.0>,
  "key_positions": [
    "Position statement [Source: post timestamp/URL]",
    ...
  ],
  "advocacy_style": "vocal" | "passive" | "observer" | "amplifier",
  "reasoning": "Brief explanation of how you derived the stance"
}}

Rules:
- confidence > 0.7 only if there are 3+ directly relevant posts
- confidence 0.3–0.7 if 1–2 posts mention the topic
- confidence < 0.3 if stance is inferred from career/interests only
- key_positions must cite the actual post (include timestamp if available)
- If no relevant posts found, stance = "neutral" and confidence < 0.3
"""


# ---------------------------------------------------------------------------
# Topic relevance filter
# ---------------------------------------------------------------------------

_RELEVANCE_SYSTEM = """\
You are filtering social media posts for topic relevance.
Return only the indices (0-based) of posts that are relevant to the given topic.
"""

_RELEVANCE_USER = """\
TOPIC: {topic}

POSTS:
{posts_numbered}

Return JSON: {{"relevant_indices": [0, 2, ...]}}
Only include posts that directly or indirectly mention the topic.
"""


def _filter_relevant_posts(
    posts: List[NewsfeedPost],
    topic: str,
    llm: LLMClient,
    max_posts: int = 50,
) -> List[NewsfeedPost]:
    """Return the subset of posts relevant to the topic."""
    if not posts:
        return []

    # Fast keyword pre-filter (free, no LLM cost)
    topic_words = set(re.sub(r"[^\w\s]", "", topic.lower()).split())
    candidates = []
    for post in posts[:max_posts]:
        post_words = set(re.sub(r"[^\w\s]", "", post.content.lower()).split())
        if topic_words & post_words:
            candidates.append(post)

    # If keyword filter already narrow, skip LLM
    if len(candidates) <= 5:
        return candidates

    # LLM relevance filter on candidates
    posts_numbered = "\n".join(
        f"[{i}] ({p.timestamp[:10] if p.timestamp else 'unknown'} via {p.source}): {p.content[:200]}"
        for i, p in enumerate(candidates)
    )

    try:
        raw = llm.chat_json(
            [
                {"role": "system", "content": _RELEVANCE_SYSTEM},
                {"role": "user", "content": _RELEVANCE_USER.format(
                    topic=topic,
                    posts_numbered=posts_numbered,
                )},
            ],
            temperature=0.1,
        )
        indices = raw.get("relevant_indices", [])
        return [candidates[i] for i in indices if 0 <= i < len(candidates)]
    except Exception as e:
        logger.warning(f"LLM relevance filter failed: {e} — using keyword-filtered posts")
        return candidates


# ---------------------------------------------------------------------------
# OpinionExtractor
# ---------------------------------------------------------------------------

class OpinionExtractor:
    """Extracts real opinion profiles from Nyne enrichment data."""

    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client

    def extract(
        self,
        person: NynePersonData,
        topic: str,
    ) -> PersonOpinionProfile:
        """
        Derive this person's opinion on the topic from their real data.

        Returns a PersonOpinionProfile. The stance is grounded in real posts;
        LLM synthesizes but cannot invent positions without evidence.
        """
        profile = PersonOpinionProfile(
            person_name=person.name,
            linkedin_url=person.linkedin_url,
            topic=topic,
        )

        # Filter newsfeed to topic-relevant posts
        relevant_posts = _filter_relevant_posts(person.newsfeed, topic, self.llm)
        profile.relevant_posts = [
            {"source": p.source, "timestamp": p.timestamp, "content": p.content, "url": p.url}
            for p in relevant_posts
        ]

        # Assign grounding level based on evidence volume
        n = len(relevant_posts)
        if n >= 3:
            profile.grounding_level = "high"
        elif n >= 1:
            profile.grounding_level = "medium"
        elif person.interests:
            profile.grounding_level = "low"
        else:
            profile.grounding_level = "inferred"

        # Build posts string for LLM
        posts_str = "\n".join(
            f"- [{p.timestamp[:10] if p.timestamp else '?'} via {p.source}] {p.content[:300]}"
            + (f"\n  URL: {p.url}" if p.url else "")
            for p in relevant_posts
        ) or "(No directly relevant posts found)"

        # Career context
        career_str = "\n".join(
            f"- {c.position} at {c.company_name} ({c.status})"
            for c in person.career_history[:5]
        ) or person.current_role or "(unknown)"

        interests_str = ", ".join(person.interests[:20]) or "(unknown)"

        try:
            raw = self.llm.chat_json(
                [
                    {"role": "system", "content": _OPINION_SYSTEM},
                    {"role": "user", "content": _OPINION_USER.format(
                        name=person.name,
                        role=person.current_role or "unknown",
                        company=person.current_company or "unknown",
                        topic=topic,
                        post_count=len(relevant_posts),
                        posts_str=posts_str,
                        career_str=career_str,
                        interests_str=interests_str,
                    )},
                ],
                temperature=0.2,
            )

            profile.stance = raw.get("stance", "neutral")
            profile.sentiment_bias = float(raw.get("sentiment_bias", 0.0))
            profile.confidence = float(raw.get("confidence", 0.0))
            profile.key_positions = raw.get("key_positions", [])
            profile.advocacy_style = raw.get("advocacy_style", "observer")

        except Exception as e:
            logger.error(f"Opinion extraction LLM call failed for {person.name}: {e}")
            # Safe defaults
            profile.stance = "neutral"
            profile.sentiment_bias = 0.0
            profile.confidence = 0.0

        logger.info(
            f"{person.name}: stance={profile.stance} bias={profile.sentiment_bias:.2f} "
            f"confidence={profile.confidence:.2f} grounding={profile.grounding_level}"
        )
        return profile

    def extract_batch(
        self,
        people: List[NynePersonData],
        topic: str,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        max_concurrent: int = 5,
    ) -> Dict[str, PersonOpinionProfile]:
        """
        Extract opinions for a batch of people in parallel.

        Returns dict mapping linkedin_url -> PersonOpinionProfile.
        """
        results: Dict[str, PersonOpinionProfile] = {}
        total = len(people)
        completed = 0

        with ThreadPoolExecutor(max_workers=max_concurrent) as executor:
            future_to_person = {
                executor.submit(self.extract, person, topic): person
                for person in people
            }
            for future in as_completed(future_to_person):
                person = future_to_person[future]
                try:
                    opinion = future.result()
                    results[person.linkedin_url] = opinion
                except Exception as e:
                    logger.error(f"Opinion extraction failed for {person.name}: {e}")
                    # Neutral fallback
                    results[person.linkedin_url] = PersonOpinionProfile(
                        person_name=person.name,
                        linkedin_url=person.linkedin_url,
                        topic=topic,
                        stance="neutral",
                        grounding_level="inferred",
                    )
                finally:
                    completed += 1
                    if progress_callback:
                        progress_callback(completed, total, person.name)

        return results
