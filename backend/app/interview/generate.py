import hashlib
import json
from pathlib import Path
from typing import Any


def generate_question(
    project_id: str,
    round: str,
    difficulty: int,
    commit: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Generate a deterministic interview question for a project."""
    if difficulty < 1:
        raise ValueError("difficulty must be at least 1")

    seed_text = "|".join(
        [project_id, round, str(difficulty), commit or "", str(dry_run)]
    )
    seed = int(hashlib.sha256(seed_text.encode("utf-8")).hexdigest()[:8], 16)

    questions_path = Path(__file__).resolve().parents[3] / "evals" / "questions.json"
    candidates: list[dict[str, Any]] = []
    if questions_path.exists():
        with questions_path.open("r", encoding="utf-8") as file:
            all_questions = json.load(file)
        candidates = [
            item
            for item in all_questions
            if str(item.get("category", "")).lower() == round.lower()
        ]

    if commit:
        commit_matches = [
            item for item in candidates
            if commit.lower() in str(item.get("question", "")).lower()
        ]
        if commit_matches:
            candidates = commit_matches

    if not candidates:
        topic = f" commit {commit}" if commit else ""
        question = (
            f"Explain a significant design or implementation decision in project "
            f"{project_id} for the {round} round{topic}."
        )
        answer = "Connect the implementation to its code, evidence, and tradeoffs; cite the relevant files or commit history."
    else:
        selected = candidates[(seed + difficulty) % len(candidates)]
        question = str(selected.get("question", ""))
        concepts = selected.get("answer_must_contain_any") or []
        answer = "Ground your answer in repository evidence and cover: " + ", ".join(str(value) for value in concepts) + "."

    return {"question": question, "answer": answer, "seed": seed}
