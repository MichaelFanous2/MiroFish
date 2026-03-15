"""
Nyne API client with async polling pattern.

All Nyne endpoints are async: submit a request, get a request_id, then poll
until status = "completed". This module wraps that pattern so callers get
synchronous-feeling results.

Based on the polling pattern from nyne_enrich/nyne_batch_enrich.py.
"""

import time
import hashlib
import logging
from dataclasses import dataclass, field
from typing import Optional, List, Callable, Any
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from ...config import Config
from ...utils.logger import get_logger

logger = get_logger('mirofish.nyne_client')


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class CareerEntry:
    company_name: str
    position: str
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    status: str = "past"  # "current" | "past"


@dataclass
class EducationEntry:
    school: str
    field: Optional[str] = None
    degree: Optional[str] = None
    graduation_year: Optional[int] = None


@dataclass
class NewsfeedPost:
    source: str          # "LinkedIn" | "Twitter" | etc.
    timestamp: str
    content: str
    url: Optional[str] = None


@dataclass
class NynePersonData:
    """Enriched profile for one real person, returned by NyneClient."""
    linkedin_url: str
    name: str = ""
    first_name: str = ""
    last_name: str = ""
    age: Optional[int] = None
    location: Optional[str] = None
    current_role: str = ""
    current_company: str = ""
    career_history: List[CareerEntry] = field(default_factory=list)
    education: List[EducationEntry] = field(default_factory=list)
    skills: List[str] = field(default_factory=list)
    twitter_url: Optional[str] = None
    twitter_followers: Optional[int] = None
    linkedin_connections: Optional[int] = None
    newsfeed: List[NewsfeedPost] = field(default_factory=list)
    interests: List[str] = field(default_factory=list)
    enriched_at: str = ""
    raw: dict = field(default_factory=dict)  # full API response for debugging

    def to_dict(self) -> dict:
        return {
            "linkedin_url": self.linkedin_url,
            "name": self.name,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "age": self.age,
            "location": self.location,
            "current_role": self.current_role,
            "current_company": self.current_company,
            "career_history": [
                {
                    "company_name": c.company_name,
                    "position": c.position,
                    "start_date": c.start_date,
                    "end_date": c.end_date,
                    "status": c.status,
                }
                for c in self.career_history
            ],
            "education": [
                {
                    "school": e.school,
                    "field": e.field,
                    "degree": e.degree,
                    "graduation_year": e.graduation_year,
                }
                for e in self.education
            ],
            "skills": self.skills,
            "twitter_url": self.twitter_url,
            "twitter_followers": self.twitter_followers,
            "linkedin_connections": self.linkedin_connections,
            "newsfeed": [
                {
                    "source": p.source,
                    "timestamp": p.timestamp,
                    "content": p.content,
                    "url": p.url,
                }
                for p in self.newsfeed
            ],
            "interests": self.interests,
            "enriched_at": self.enriched_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "NynePersonData":
        person = cls(linkedin_url=data.get("linkedin_url", ""))
        person.name = data.get("name", "")
        person.first_name = data.get("first_name", "")
        person.last_name = data.get("last_name", "")
        person.age = data.get("age")
        person.location = data.get("location")
        person.current_role = data.get("current_role", "")
        person.current_company = data.get("current_company", "")
        person.career_history = [
            CareerEntry(**c) for c in data.get("career_history", [])
        ]
        person.education = [
            EducationEntry(**e) for e in data.get("education", [])
        ]
        person.skills = data.get("skills", [])
        person.twitter_url = data.get("twitter_url")
        person.twitter_followers = data.get("twitter_followers")
        person.linkedin_connections = data.get("linkedin_connections")
        person.newsfeed = [
            NewsfeedPost(**p) for p in data.get("newsfeed", [])
        ]
        person.interests = data.get("interests", [])
        person.enriched_at = data.get("enriched_at", "")
        return person


# ---------------------------------------------------------------------------
# NyneClient
# ---------------------------------------------------------------------------

class NyneClient:
    """
    Wraps the Nyne REST API.

    All methods block until the async Nyne job completes (or times out),
    using the polling pattern from nyne_batch_enrich.py.
    """

    ENRICHMENT_URL = f"{Config.NYNE_BASE_URL}/person/enrichment"
    INTERESTS_URL = f"{Config.NYNE_BASE_URL}/person/interests"
    SEARCH_URL = f"{Config.NYNE_BASE_URL}/person/search"

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        poll_interval: float = None,
        poll_timeout: int = None,
    ):
        self.api_key = api_key or Config.NYNE_API_KEY
        self.api_secret = api_secret or Config.NYNE_API_SECRET
        self.poll_interval = poll_interval or Config.NYNE_POLL_INTERVAL
        self.poll_timeout = poll_timeout or Config.NYNE_POLL_TIMEOUT

        if not self.api_key or not self.api_secret:
            logger.warning("NYNE_API_KEY or NYNE_API_SECRET not set — Nyne calls will fail")

    @property
    def _headers(self) -> dict:
        return {
            "X-API-Key": self.api_key,
            "X-API-Secret": self.api_secret,
            "Content-Type": "application/json",
        }

    # ------------------------------------------------------------------
    # Internal: submit + poll
    # ------------------------------------------------------------------

    def _submit(self, url: str, payload: dict) -> Optional[str]:
        """Submit a request and return the request_id, or None on failure."""
        try:
            resp = requests.post(url, headers=self._headers, json=payload, timeout=60)
            resp.raise_for_status()
            data = resp.json()
            if data.get("success"):
                result_data = data.get("data", {})
                # If already completed immediately
                if result_data.get("status") == "completed":
                    return f"__immediate__{result_data.get('request_id')}"
                return result_data.get("request_id")
            else:
                logger.warning(f"Nyne submit failed: {data.get('error')}")
                return None
        except requests.RequestException as e:
            logger.error(f"Nyne submit error: {e}")
            return None

    def _poll(self, url: str, request_id: str) -> Optional[dict]:
        """Poll until completed. Returns the result dict or None on timeout/failure."""
        start = time.time()
        while time.time() - start < self.poll_timeout:
            try:
                resp = requests.get(
                    f"{url}?request_id={request_id}",
                    headers=self._headers,
                    timeout=30,
                )
                resp.raise_for_status()
                data = resp.json()
                if data.get("success"):
                    result_data = data.get("data", {})
                    status = result_data.get("status", "pending")
                    if status == "completed":
                        return result_data
                    elif status == "failed":
                        logger.warning(f"Nyne job failed: {request_id}")
                        return None
            except requests.RequestException:
                pass
            time.sleep(self.poll_interval)

        logger.warning(f"Nyne poll timed out after {self.poll_timeout}s for {request_id}")
        return None

    def _submit_and_poll(self, url: str, payload: dict) -> Optional[dict]:
        """Submit and poll in one call."""
        request_id = self._submit(url, payload)
        if request_id is None:
            return None
        if request_id.startswith("__immediate__"):
            real_id = request_id[len("__immediate__"):]
            return self._poll(url, real_id)
        return self._poll(url, request_id)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def enrich_person(
        self,
        linkedin_url: str,
        include_newsfeed: bool = True,
        ai_enhanced: bool = True,
    ) -> Optional[NynePersonData]:
        """
        Enrich a person by LinkedIn URL.

        Returns NynePersonData or None if not found / timed out.
        """
        if not linkedin_url.startswith("http"):
            linkedin_url = "https://" + linkedin_url

        payload = {
            "social_media_url": linkedin_url,
            "ai_enhanced_search": ai_enhanced,
        }
        if include_newsfeed:
            payload["newsfeed"] = ["all"]

        result = self._submit_and_poll(self.ENRICHMENT_URL, payload)
        if result is None:
            return None

        return self._parse_enrichment(linkedin_url, result)

    def get_interests(self, linkedin_url: str) -> List[str]:
        """
        Return a list of interest/topic strings for the person.
        Falls back to empty list on failure.
        """
        if not linkedin_url.startswith("http"):
            linkedin_url = "https://" + linkedin_url

        payload = {"social_media_url": linkedin_url}
        result = self._submit_and_poll(self.INTERESTS_URL, payload)
        if result is None:
            return []

        raw_interests = result.get("result", result)
        if isinstance(raw_interests, list):
            return [str(i) for i in raw_interests if i]
        if isinstance(raw_interests, dict):
            # Some responses are {category: [items]} — flatten
            flat = []
            for v in raw_interests.values():
                if isinstance(v, list):
                    flat.extend(str(x) for x in v if x)
            return flat
        return []

    def search_person(
        self,
        name: str,
        company: Optional[str] = None,
        title: Optional[str] = None,
        context: Optional[str] = None,
    ) -> List[str]:
        """
        Search for a person by name/company/title.

        Returns a list of LinkedIn URLs (0–5 candidates).
        Falls back to empty list on failure.
        """
        payload: dict = {"name": name}
        if company:
            payload["company"] = company
        if title:
            payload["title"] = title
        if context:
            payload["context"] = context

        try:
            resp = requests.post(
                self.SEARCH_URL,
                headers=self._headers,
                json=payload,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("success"):
                results = data.get("data", {}).get("results", [])
                urls = []
                for r in results:
                    url = r.get("linkedin_url") or r.get("url") or r.get("social_media_url")
                    if url and "linkedin.com" in url:
                        urls.append(url)
                return urls
        except requests.RequestException as e:
            logger.warning(f"Nyne search failed for '{name}': {e}")

        return []

    def batch_enrich(
        self,
        linkedin_urls: List[str],
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        max_concurrent: int = None,
        include_newsfeed: bool = True,
    ) -> List[Optional[NynePersonData]]:
        """
        Enrich a list of LinkedIn URLs in parallel (bounded concurrency).

        progress_callback(completed_count, total_count, name_or_url)

        Returns a list aligned with the input — None for any that failed.
        """
        max_concurrent = max_concurrent or Config.NYNE_MAX_CONCURRENT
        total = len(linkedin_urls)
        results: List[Optional[NynePersonData]] = [None] * total
        completed = 0

        with ThreadPoolExecutor(max_workers=max_concurrent) as executor:
            future_to_idx = {
                executor.submit(self.enrich_person, url, include_newsfeed): i
                for i, url in enumerate(linkedin_urls)
            }
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    person = future.result()
                    results[idx] = person
                    completed += 1
                    if progress_callback:
                        label = person.name if person else linkedin_urls[idx]
                        progress_callback(completed, total, label)
                except Exception as e:
                    logger.error(f"Batch enrich error for {linkedin_urls[idx]}: {e}")
                    completed += 1
                    if progress_callback:
                        progress_callback(completed, total, linkedin_urls[idx])

        return results

    # ------------------------------------------------------------------
    # Internal: parse raw Nyne response → NynePersonData
    # ------------------------------------------------------------------

    def _parse_enrichment(self, linkedin_url: str, raw: dict) -> NynePersonData:
        """Convert raw Nyne enrichment response to NynePersonData."""
        from datetime import datetime

        # The actual profile data lives under raw["result"] in most responses
        result = raw.get("result", raw)

        first = result.get("firstname", "") or ""
        last = result.get("lastname", "") or ""
        name = f"{first} {last}".strip() or result.get("name", "")

        # Current role — from first career entry with status=current
        careers_raw = result.get("careers_info", []) or []
        career_history = []
        current_role = ""
        current_company = ""
        for c in careers_raw:
            entry = CareerEntry(
                company_name=c.get("company_name", ""),
                position=c.get("position", ""),
                start_date=c.get("start_date"),
                end_date=c.get("end_date"),
                status=c.get("status", "past"),
            )
            career_history.append(entry)
            if entry.status == "current" and not current_role:
                current_role = entry.position
                current_company = entry.company_name

        # Education
        education = [
            EducationEntry(
                school=e.get("school", ""),
                field=e.get("field"),
                degree=e.get("degree"),
                graduation_year=e.get("graduation_year"),
            )
            for e in (result.get("education", []) or [])
        ]

        # Social profiles
        social = result.get("social_profiles", {}) or {}
        twitter_data = social.get("twitter", {}) or {}
        linkedin_data = social.get("linkedin", {}) or {}
        twitter_url = twitter_data.get("url")
        twitter_followers = twitter_data.get("followers")
        linkedin_connections = linkedin_data.get("connections")

        # Newsfeed
        newsfeed = [
            NewsfeedPost(
                source=p.get("source", ""),
                timestamp=p.get("timestamp", ""),
                content=p.get("content", ""),
                url=p.get("url"),
            )
            for p in (result.get("newsfeed", []) or [])
            if p.get("content")
        ]

        # Skills
        skills = result.get("skills", []) or []

        # Location — may be in address or location field
        location = (
            result.get("location")
            or result.get("city")
            or result.get("address", {}).get("city") if isinstance(result.get("address"), dict) else None
            or result.get("address") if isinstance(result.get("address"), str) else None
        )

        return NynePersonData(
            linkedin_url=linkedin_url,
            name=name,
            first_name=first,
            last_name=last,
            age=result.get("age"),
            location=location,
            current_role=current_role,
            current_company=current_company,
            career_history=career_history,
            education=education,
            skills=skills,
            twitter_url=twitter_url,
            twitter_followers=twitter_followers,
            linkedin_connections=linkedin_connections,
            newsfeed=newsfeed,
            enriched_at=datetime.now().isoformat(),
            raw=raw,
        )


def url_to_cache_key(linkedin_url: str) -> str:
    """Convert a LinkedIn URL to a filesystem-safe cache key."""
    return hashlib.md5(linkedin_url.encode()).hexdigest()
