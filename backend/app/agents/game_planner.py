from hashlib import sha256
import re

from app.models import GamePlan, Requirement


class GamePlannerAgent:
    """Extract explicit requirements without converting them into a game type."""

    def plan(self, prompt: str) -> GamePlan:
        text = prompt.lower()
        entities: list[str] = []
        mechanics: list[str] = []
        requested_ui: list[str] = []

        if re.search(r"\bzombies?\b", text):
            entities.append("zombie")
        if re.search(r"\bbuildings?\b|\bstructures?\b", text):
            entities.append("building")
        if re.search(r"\b(?:shoot|shooting|gun|guns|weapon|weapons|fire)\b", text):
            mechanics.append("projectiles")
        if re.search(r"\b(?:chase|chasing|pursue|pursuing)\b", text) or "zombie" in entities:
            mechanics.append("chasing_enemies")
        if re.search(r"\b(?:survive|survival)\b", text):
            mechanics.append("survival")
        if re.search(r"\b(?:race|racing|racetrack)\b", text):
            genre_intent = "racing"
        elif "zombie" in entities:
            genre_intent = "top_down_survival"
        elif re.search(r"\bmaze|labyrinth\b", text):
            genre_intent = "maze"
        elif re.search(r"\bplatformer|platform adventure\b", text):
            genre_intent = "platformer"
        else:
            genre_intent = None

        for ui_name, pattern in (
            ("score", r"\bscore\b"),
            ("health", r"\bhealth|lives?\b"),
            ("timer", r"\btimer|timed\b"),
        ):
            if re.search(pattern, text):
                requested_ui.append(ui_name)

        requirements: list[Requirement] = []

        def add_requirement(category: str, description: str) -> None:
            normalized = re.sub(r"\s+", " ", description.strip().lower())
            requirement_id = "req-" + sha256(
                f"{category}:{normalized}".encode("utf-8")
            ).hexdigest()[:12]
            requirements.append(
                Requirement(
                    id=requirement_id,
                    category=category,
                    description=description,
                )
            )

        if genre_intent is not None:
            add_requirement("genre", genre_intent)
        for entity in entities:
            add_requirement("entity", entity)
        for mechanic in dict.fromkeys(mechanics):
            add_requirement("mechanic", mechanic)
        for ui_item in requested_ui:
            add_requirement("ui", ui_item)

        return GamePlan(
            original_prompt=prompt,
            genre_intent=genre_intent,
            entities=entities,
            mechanics=list(dict.fromkeys(mechanics)),
            requested_ui=requested_ui,
            requirements=requirements,
        )
