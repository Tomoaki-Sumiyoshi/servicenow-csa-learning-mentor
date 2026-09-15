#!/usr/bin/env python3
"""Validate and update ServiceNow CSA learning progress.

Live progress is stored outside the skill so skill upgrades cannot overwrite it.
Only the Python standard library is used.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator


SCHEMA_VERSION = 2
WEEK_NUMBERS = tuple(range(1, 9))
TOPIC_STATUSES = {"pending", "completed"}
HANDS_ON_STATUSES = {"pending", "in_progress", "completed", "blocked"}
MASTERY_LEVELS = {"weak", "medium", "strong"}
STUDY_STAGES = {"planning", "university", "hands_on", "recall", "quiz", "review"}
EXERCISE_END_STATUSES = {"completed", "abandoned"}
EXERCISE_KINDS = {"hands_on", "recall"}
QUESTION_VERDICTS = {"correct", "partial", "incorrect"}
EVIDENCE_DIMENSIONS = {"learning", "hands_on", "recall"}
EVIDENCE_OUTCOMES = {"passed", "failed"}
MAX_HINT_LEVEL = 3
PASS_PERCENT = 80.0
LOCK_TIMEOUT_SECONDS = 5.0
STALE_LOCK_SECONDS = 30.0
SKILL_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_PATH = SKILL_ROOT / "data" / "progress.json"
WORKSPACE_ROOT = SKILL_ROOT.parents[2]
DEFAULT_PROGRESS_PATH = WORKSPACE_ROOT / ".servicenow-csa-learning" / "progress.json"


class ProgressError(Exception):
    """Raised for invalid progress data or invalid state transitions."""


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ProgressError(message)


def _require_object(value: Any, location: str) -> dict[str, Any]:
    _require(isinstance(value, dict), f"{location} must be an object")
    return value


def _require_list(value: Any, location: str) -> list[Any]:
    _require(isinstance(value, list), f"{location} must be an array")
    return value


def _require_string(value: Any, location: str, *, nullable: bool = False) -> None:
    if nullable and value is None:
        return
    _require(isinstance(value, str) and bool(value.strip()), f"{location} must be a non-empty string")


def _require_int(value: Any, location: str, *, minimum: int | None = None) -> None:
    _require(_is_int(value), f"{location} must be an integer")
    if minimum is not None:
        _require(value >= minimum, f"{location} must be at least {minimum}")


def _require_number(value: Any, location: str, *, minimum: float | None = None) -> None:
    _require(
        isinstance(value, (int, float)) and not isinstance(value, bool),
        f"{location} must be numeric",
    )
    if minimum is not None:
        _require(value >= minimum, f"{location} must be at least {minimum}")


def _require_fields(
    value: dict[str, Any], required: set[str], location: str, optional: set[str] | None = None
) -> None:
    optional = optional or set()
    missing = sorted(required - value.keys())
    unknown = sorted(value.keys() - required - optional)
    _require(not missing, f"{location} is missing fields: {', '.join(missing)}")
    _require(not unknown, f"{location} has unknown fields: {', '.join(unknown)}")


def _parse_iso_timestamp(value: str, location: str) -> datetime:
    _require_string(value, location)
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ProgressError(f"{location} must be an ISO 8601 timestamp") from exc
    _require(parsed.tzinfo is not None, f"{location} must include a timezone")
    return parsed


def _validate_timestamp(value: Any, location: str, *, nullable: bool = False) -> None:
    if nullable and value is None:
        return
    _require(isinstance(value, str), f"{location} must be an ISO 8601 timestamp")
    _parse_iso_timestamp(value, location)


def _validate_date(value: Any, location: str, *, nullable: bool = False) -> None:
    if nullable and value is None:
        return
    _require(isinstance(value, str), f"{location} must be an ISO date")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ProgressError(f"{location} must be an ISO date (YYYY-MM-DD)") from exc


def _validate_topic(topic: Any, location: str) -> None:
    topic = _require_object(topic, location)
    required = {"name", "status", "mastery", "review_count", "last_reviewed_on", "completed_at"}
    _require_fields(topic, required, location)
    _require_string(topic["name"], f"{location}.name")
    _require(topic["status"] in TOPIC_STATUSES, f"{location}.status must be pending or completed")
    _require(
        topic["mastery"] is None or topic["mastery"] in MASTERY_LEVELS,
        f"{location}.mastery must be null, weak, medium, or strong",
    )
    _require_int(topic["review_count"], f"{location}.review_count", minimum=0)
    _validate_date(topic["last_reviewed_on"], f"{location}.last_reviewed_on", nullable=True)
    _validate_timestamp(topic["completed_at"], f"{location}.completed_at", nullable=True)
    _require(
        (topic["status"] == "completed") == (topic["completed_at"] is not None),
        f"{location}.completed_at must be set exactly when status is completed",
    )
    _require(
        (topic["review_count"] == 0) == (topic["last_reviewed_on"] is None),
        f"{location}.last_reviewed_on must be set exactly when review_count is positive",
    )


def _validate_hands_on(item: Any, location: str) -> None:
    item = _require_object(item, location)
    required = {"task", "status", "completed_at"}
    _require_fields(item, required, location)
    _require_string(item["task"], f"{location}.task")
    _require(item["status"] in HANDS_ON_STATUSES, f"{location}.status is invalid")
    _validate_timestamp(item["completed_at"], f"{location}.completed_at", nullable=True)
    _require(
        (item["status"] == "completed") == (item["completed_at"] is not None),
        f"{location}.completed_at must be set exactly when status is completed",
    )


def _validate_quiz_question(question: Any, location: str, *, require_answered: bool) -> None:
    question = _require_object(question, location)
    required = {
        "id",
        "prompt",
        "learner_answer",
        "verdict",
        "expected_answer",
        "rationale",
        "verification",
        "misconception",
        "answered_at",
    }
    _require_fields(question, required, location)
    _require_string(question["id"], f"{location}.id")
    _require_string(question["prompt"], f"{location}.prompt")
    verdict = question["verdict"]
    _require(verdict is None or verdict in QUESTION_VERDICTS, f"{location}.verdict is invalid")
    if verdict is None:
        _require(not require_answered, f"{location} must be answered")
        for field in ("learner_answer", "expected_answer", "rationale", "verification", "misconception", "answered_at"):
            _require(question[field] is None, f"{location}.{field} must be null before grading")
        return
    for field in ("learner_answer", "expected_answer", "rationale", "verification"):
        _require_string(question[field], f"{location}.{field}")
    _require_string(question["misconception"], f"{location}.misconception", nullable=True)
    if verdict != "correct":
        _require(question["misconception"] is not None, f"{location}.misconception is required for {verdict}")
    _validate_timestamp(question["answered_at"], f"{location}.answered_at")


def _validate_active_quiz(quiz: Any, location: str) -> None:
    quiz = _require_object(quiz, location)
    required = {"id", "week", "topic", "started_at", "questions"}
    _require_fields(quiz, required, location)
    _require_string(quiz["id"], f"{location}.id")
    _require_int(quiz["week"], f"{location}.week")
    _require(quiz["week"] in WEEK_NUMBERS, f"{location}.week must be 1 through 8")
    _require_string(quiz["topic"], f"{location}.topic")
    _validate_timestamp(quiz["started_at"], f"{location}.started_at")
    questions = _require_list(quiz["questions"], f"{location}.questions")
    _require(3 <= len(questions) <= 5, f"{location}.questions must contain 3 through 5 questions")
    ids: list[str] = []
    for index, question in enumerate(questions):
        _validate_quiz_question(question, f"{location}.questions[{index}]", require_answered=False)
        ids.append(question["id"])
    _require(len(ids) == len(set(ids)), f"{location}.questions contains duplicate ids")


def _question_points(verdict: str) -> float:
    return {"correct": 1.0, "partial": 0.5, "incorrect": 0.0}[verdict]


def _validate_quiz_attempt(attempt: Any, location: str) -> None:
    attempt = _require_object(attempt, location)
    required = {"id", "week", "topic", "started_at", "completed_at", "questions", "result", "wrong_question_ids"}
    _require_fields(attempt, required, location)
    _validate_active_quiz(
        {key: attempt[key] for key in ("id", "week", "topic", "started_at", "questions")},
        location,
    )
    _validate_timestamp(attempt["completed_at"], f"{location}.completed_at")
    for index, question in enumerate(attempt["questions"]):
        _validate_quiz_question(question, f"{location}.questions[{index}]", require_answered=True)
    result = _require_object(attempt["result"], f"{location}.result")
    _require_fields(result, {"earned_points", "total", "score_percent", "passed"}, f"{location}.result")
    expected_points = sum(_question_points(question["verdict"]) for question in attempt["questions"])
    expected_total = len(attempt["questions"])
    expected_score = round(expected_points * 100.0 / expected_total, 2)
    _require_number(result["earned_points"], f"{location}.result.earned_points", minimum=0)
    _require_int(result["total"], f"{location}.result.total", minimum=3)
    _require(abs(float(result["earned_points"]) - expected_points) < 0.001, f"{location}.result.earned_points is inconsistent")
    _require(result["total"] == expected_total, f"{location}.result.total is inconsistent")
    _require_number(result["score_percent"], f"{location}.result.score_percent", minimum=0)
    _require(abs(float(result["score_percent"]) - expected_score) < 0.001, f"{location}.result.score_percent is inconsistent")
    _require(isinstance(result["passed"], bool), f"{location}.result.passed must be boolean")
    _require(result["passed"] == (expected_score >= PASS_PERCENT), f"{location}.result.passed is inconsistent")
    wrong_ids = _require_list(attempt["wrong_question_ids"], f"{location}.wrong_question_ids")
    for index, wrong_id in enumerate(wrong_ids):
        _require_string(wrong_id, f"{location}.wrong_question_ids[{index}]")
    expected_wrong_count = sum(question["verdict"] != "correct" for question in attempt["questions"])
    _require(len(wrong_ids) == expected_wrong_count, f"{location}.wrong_question_ids count is inconsistent")
    _require(len(wrong_ids) == len(set(wrong_ids)), f"{location}.wrong_question_ids contains duplicates")


def _validate_wrong_question(item: Any, location: str) -> None:
    item = _require_object(item, location)
    required = {
        "id",
        "attempt_id",
        "question_id",
        "week",
        "topic",
        "prompt",
        "learner_answer",
        "verdict",
        "expected_answer",
        "rationale",
        "verification",
        "misconception",
        "recorded_at",
        "resolved",
        "resolved_at",
    }
    _require_fields(item, required, location)
    _require_string(item["id"], f"{location}.id")
    _require_string(item["attempt_id"], f"{location}.attempt_id")
    _require_string(item["question_id"], f"{location}.question_id")
    _require_int(item["week"], f"{location}.week")
    _require(item["week"] in WEEK_NUMBERS, f"{location}.week must be 1 through 8")
    _require_string(item["topic"], f"{location}.topic")
    for field in ("prompt", "learner_answer", "expected_answer", "rationale", "verification", "misconception"):
        _require_string(item[field], f"{location}.{field}")
    _require(item["verdict"] in {"partial", "incorrect"}, f"{location}.verdict must be partial or incorrect")
    _validate_timestamp(item["recorded_at"], f"{location}.recorded_at")
    _require(isinstance(item["resolved"], bool), f"{location}.resolved must be boolean")
    _validate_timestamp(item["resolved_at"], f"{location}.resolved_at", nullable=True)
    _require(
        item["resolved"] == (item["resolved_at"] is not None),
        f"{location}.resolved_at must be set exactly when resolved is true",
    )


def _validate_active_exercise(item: Any, location: str) -> None:
    item = _require_object(item, location)
    required = {
        "id",
        "title",
        "kind",
        "topic",
        "week",
        "objective",
        "constraints",
        "deliverable",
        "acceptance_criteria",
        "started_at",
        "hint_level",
        "answer_revealed",
    }
    _require_fields(item, required, location)
    _require_string(item["id"], f"{location}.id")
    _require_string(item["title"], f"{location}.title")
    _require(item["kind"] in EXERCISE_KINDS, f"{location}.kind is invalid")
    _require_string(item["topic"], f"{location}.topic")
    _require_int(item["week"], f"{location}.week")
    _require(item["week"] in WEEK_NUMBERS, f"{location}.week must be 1 through 8")
    _require_string(item["objective"], f"{location}.objective")
    constraints = _require_list(item["constraints"], f"{location}.constraints")
    for index, value in enumerate(constraints):
        _require_string(value, f"{location}.constraints[{index}]")
    _require_string(item["deliverable"], f"{location}.deliverable")
    criteria = _require_list(item["acceptance_criteria"], f"{location}.acceptance_criteria")
    _require(bool(criteria), f"{location}.acceptance_criteria cannot be empty")
    for index, value in enumerate(criteria):
        _require_string(value, f"{location}.acceptance_criteria[{index}]")
    _validate_timestamp(item["started_at"], f"{location}.started_at")
    _require_int(item["hint_level"], f"{location}.hint_level", minimum=0)
    _require(item["hint_level"] <= MAX_HINT_LEVEL, f"{location}.hint_level cannot exceed {MAX_HINT_LEVEL}")
    _require(isinstance(item["answer_revealed"], bool), f"{location}.answer_revealed must be boolean")


def _validate_exercise_history(item: Any, location: str) -> None:
    item = _require_object(item, location)
    required = {
        "id",
        "title",
        "kind",
        "topic",
        "week",
        "objective",
        "constraints",
        "deliverable",
        "acceptance_criteria",
        "started_at",
        "ended_at",
        "status",
        "final_hint_level",
        "answer_revealed",
    }
    _require_fields(item, required, location)
    active_view = {
        "id": item.get("id"),
        "title": item.get("title"),
        "kind": item.get("kind"),
        "topic": item.get("topic"),
        "week": item.get("week"),
        "objective": item.get("objective"),
        "constraints": item.get("constraints"),
        "deliverable": item.get("deliverable"),
        "acceptance_criteria": item.get("acceptance_criteria"),
        "started_at": item.get("started_at"),
        "hint_level": item.get("final_hint_level"),
        "answer_revealed": item.get("answer_revealed"),
    }
    _validate_active_exercise(active_view, location)
    _validate_timestamp(item["ended_at"], f"{location}.ended_at")
    _require(item["status"] in EXERCISE_END_STATUSES, f"{location}.status is invalid")
    _require(
        _parse_iso_timestamp(item["ended_at"], f"{location}.ended_at")
        >= _parse_iso_timestamp(item["started_at"], f"{location}.started_at"),
        f"{location}.ended_at cannot precede started_at",
    )


def _validate_evidence_value(value: Any, location: str, *, quiz: bool = False) -> None:
    value = _require_object(value, location)
    required = {"outcome", "recorded_at", "note"}
    if quiz:
        required.add("score_percent")
    _require_fields(value, required, location)
    _require(value["outcome"] is None or value["outcome"] in EVIDENCE_OUTCOMES, f"{location}.outcome is invalid")
    if value["outcome"] is None:
        _require(value["recorded_at"] is None and value["note"] is None, f"{location} must be empty when outcome is null")
        if quiz:
            _require(value["score_percent"] is None, f"{location}.score_percent must be null")
        return
    _validate_timestamp(value["recorded_at"], f"{location}.recorded_at")
    _require_string(value["note"], f"{location}.note")
    if quiz:
        _require_number(value["score_percent"], f"{location}.score_percent", minimum=0)
        _require(value["score_percent"] <= 100, f"{location}.score_percent cannot exceed 100")
        expected = "passed" if value["score_percent"] >= PASS_PERCENT else "failed"
        _require(value["outcome"] == expected, f"{location}.outcome must match score_percent")


def _validate_topic_evidence(item: Any, location: str) -> None:
    item = _require_object(item, location)
    required = {"week", "topic", "learning", "hands_on", "recall", "quiz"}
    _require_fields(item, required, location)
    _require_int(item["week"], f"{location}.week")
    _require(item["week"] in WEEK_NUMBERS, f"{location}.week must be 1 through 8")
    _require_string(item["topic"], f"{location}.topic")
    for dimension in ("learning", "hands_on", "recall"):
        _validate_evidence_value(item[dimension], f"{location}.{dimension}")
    _validate_evidence_value(item["quiz"], f"{location}.quiz", quiz=True)


def _validate_mastery_event(item: Any, location: str) -> None:
    item = _require_object(item, location)
    required = {"id", "week", "topic", "previous", "level", "reason", "evidence", "recorded_at"}
    _require_fields(item, required, location)
    _require_string(item["id"], f"{location}.id")
    _require_int(item["week"], f"{location}.week")
    _require(item["week"] in WEEK_NUMBERS, f"{location}.week must be 1 through 8")
    _require_string(item["topic"], f"{location}.topic")
    _require(item["previous"] is None or item["previous"] in MASTERY_LEVELS, f"{location}.previous is invalid")
    _require(item["level"] in MASTERY_LEVELS, f"{location}.level is invalid")
    _require_string(item["reason"], f"{location}.reason")
    evidence = _require_list(item["evidence"], f"{location}.evidence")
    _require(bool(evidence), f"{location}.evidence cannot be empty")
    for index, value in enumerate(evidence):
        _require_string(value, f"{location}.evidence[{index}]")
    _validate_timestamp(item["recorded_at"], f"{location}.recorded_at")


def _validate_override_event(item: Any, location: str) -> None:
    item = _require_object(item, location)
    _require_fields(item, {"id", "action", "reason", "recorded_at"}, location)
    for field in ("id", "action", "reason"):
        _require_string(item[field], f"{location}.{field}")
    _validate_timestamp(item["recorded_at"], f"{location}.recorded_at")


def _validate_active_session(item: Any, location: str) -> None:
    item = _require_object(item, location)
    required = {"id", "started_at", "start_week"}
    _require_fields(item, required, location)
    _require_string(item["id"], f"{location}.id")
    _validate_timestamp(item["started_at"], f"{location}.started_at")
    _require_int(item["start_week"], f"{location}.start_week")
    _require(item["start_week"] in WEEK_NUMBERS, f"{location}.start_week must be 1 through 8")


def _validate_session(item: Any, location: str) -> None:
    item = _require_object(item, location)
    required = {"id", "started_at", "ended_at", "start_week", "end_week", "duration_minutes", "note"}
    _require_fields(item, required, location)
    _require_string(item["id"], f"{location}.id")
    _validate_timestamp(item["started_at"], f"{location}.started_at")
    _validate_timestamp(item["ended_at"], f"{location}.ended_at")
    _require_int(item["start_week"], f"{location}.start_week")
    _require_int(item["end_week"], f"{location}.end_week")
    _require(item["start_week"] in WEEK_NUMBERS, f"{location}.start_week must be 1 through 8")
    _require(item["end_week"] in WEEK_NUMBERS, f"{location}.end_week must be 1 through 8")
    _require_number(item["duration_minutes"], f"{location}.duration_minutes", minimum=0)
    _require(item["note"] is None or isinstance(item["note"], str), f"{location}.note must be null or a string")
    started = _parse_iso_timestamp(item["started_at"], f"{location}.started_at")
    ended = _parse_iso_timestamp(item["ended_at"], f"{location}.ended_at")
    _require(ended >= started, f"{location}.ended_at cannot precede started_at")
    expected_duration = round((ended - started).total_seconds() / 60.0, 2)
    _require(
        abs(float(item["duration_minutes"]) - expected_duration) < 0.001,
        f"{location}.duration_minutes must equal {expected_duration}",
    )


def validate_progress(data: Any) -> None:
    """Validate the complete persisted document or raise ProgressError."""
    data = _require_object(data, "progress")
    required = {
        "schema_version",
        "revision",
        "current_week",
        "current_stage",
        "current_topic",
        "weeks",
        "topic_evidence",
        "mastery_events",
        "override_events",
        "active_quiz",
        "quiz_attempts",
        "wrong_questions",
        "last_study_date",
        "active_exercise",
        "exercise_history",
        "active_session",
        "sessions",
        "updated_at",
    }
    _require_fields(data, required, "progress")
    _require_int(data["schema_version"], "progress.schema_version")
    _require(data["schema_version"] == SCHEMA_VERSION, f"unsupported schema_version: {data['schema_version']!r}")
    _require_int(data["revision"], "progress.revision", minimum=0)
    _require_int(data["current_week"], "progress.current_week")
    _require(data["current_week"] in WEEK_NUMBERS, "progress.current_week must be 1 through 8")
    _require(data["current_stage"] in STUDY_STAGES, f"progress.current_stage must be one of: {', '.join(sorted(STUDY_STAGES))}")
    current_topic = data["current_topic"]
    if current_topic is not None:
        current_topic = _require_object(current_topic, "progress.current_topic")
        _require_fields(current_topic, {"week", "topic"}, "progress.current_topic")
        _require_int(current_topic["week"], "progress.current_topic.week")
        _require(current_topic["week"] in WEEK_NUMBERS, "progress.current_topic.week must be 1 through 8")
        _require_string(current_topic["topic"], "progress.current_topic.topic")

    weeks = _require_object(data["weeks"], "progress.weeks")
    expected_week_keys = {str(number) for number in WEEK_NUMBERS}
    _require(set(weeks) == expected_week_keys, "progress.weeks must contain exactly keys 1 through 8")
    for week_number in WEEK_NUMBERS:
        location = f"progress.weeks.{week_number}"
        week = _require_object(weeks[str(week_number)], location)
        _require_fields(week, {"title", "topics", "hands_on"}, location)
        _require_string(week["title"], f"{location}.title")
        topics = _require_list(week["topics"], f"{location}.topics")
        _require(bool(topics), f"{location}.topics cannot be empty")
        topic_names: list[str] = []
        for index, topic in enumerate(topics):
            _validate_topic(topic, f"{location}.topics[{index}]")
            topic_names.append(topic["name"])
        _require(len(topic_names) == len(set(topic_names)), f"{location}.topics contains duplicate names")
        hands_on = _require_list(week["hands_on"], f"{location}.hands_on")
        _require(bool(hands_on), f"{location}.hands_on cannot be empty")
        task_names: list[str] = []
        for index, item in enumerate(hands_on):
            _validate_hands_on(item, f"{location}.hands_on[{index}]")
            task_names.append(item["task"])
        _require(len(task_names) == len(set(task_names)), f"{location}.hands_on contains duplicate tasks")

    def topic_exists(week_number: int, topic_name: str) -> bool:
        return any(topic["name"] == topic_name for topic in weeks[str(week_number)]["topics"])

    if current_topic is not None:
        _require(topic_exists(current_topic["week"], current_topic["topic"]), "progress.current_topic references an unknown topic")
        _require(current_topic["week"] == data["current_week"], "progress.current_topic must belong to current_week")

    topic_evidence = _require_list(data["topic_evidence"], "progress.topic_evidence")
    evidence_keys: list[tuple[int, str]] = []
    for index, item in enumerate(topic_evidence):
        _validate_topic_evidence(item, f"progress.topic_evidence[{index}]")
        _require(topic_exists(item["week"], item["topic"]), f"progress.topic_evidence[{index}] references an unknown topic")
        evidence_keys.append((item["week"], item["topic"]))
    _require(len(evidence_keys) == len(set(evidence_keys)), "progress.topic_evidence contains duplicate topics")

    mastery_events = _require_list(data["mastery_events"], "progress.mastery_events")
    mastery_ids: list[str] = []
    for index, item in enumerate(mastery_events):
        _validate_mastery_event(item, f"progress.mastery_events[{index}]")
        _require(topic_exists(item["week"], item["topic"]), f"progress.mastery_events[{index}] references an unknown topic")
        mastery_ids.append(item["id"])
    _require(len(mastery_ids) == len(set(mastery_ids)), "progress.mastery_events contains duplicate ids")

    override_events = _require_list(data["override_events"], "progress.override_events")
    override_ids: list[str] = []
    for index, item in enumerate(override_events):
        _validate_override_event(item, f"progress.override_events[{index}]")
        override_ids.append(item["id"])
    _require(len(override_ids) == len(set(override_ids)), "progress.override_events contains duplicate ids")

    active_quiz = data["active_quiz"]
    if active_quiz is not None:
        _validate_active_quiz(active_quiz, "progress.active_quiz")
        _require(topic_exists(active_quiz["week"], active_quiz["topic"]), "progress.active_quiz references an unknown topic")

    attempts = _require_list(data["quiz_attempts"], "progress.quiz_attempts")
    attempt_ids: list[str] = []
    for index, attempt in enumerate(attempts):
        _validate_quiz_attempt(attempt, f"progress.quiz_attempts[{index}]")
        attempt_ids.append(attempt["id"])
    if active_quiz is not None:
        attempt_ids.append(active_quiz["id"])
    _require(len(attempt_ids) == len(set(attempt_ids)), "progress.quiz_attempts contains duplicate ids")

    wrong_questions = _require_list(data["wrong_questions"], "progress.wrong_questions")
    wrong_ids: list[str] = []
    wrong_by_id: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(wrong_questions):
        _validate_wrong_question(item, f"progress.wrong_questions[{index}]")
        wrong_ids.append(item["id"])
        wrong_by_id[item["id"]] = item
    _require(len(wrong_ids) == len(set(wrong_ids)), "progress.wrong_questions contains duplicate ids")
    attempt_by_id = {attempt["id"]: attempt for attempt in attempts}
    for attempt in attempts:
        if attempt["topic"] is not None:
            valid_topics = {topic["name"] for topic in weeks[str(attempt["week"])]["topics"]}
            _require(attempt["topic"] in valid_topics, f"attempt {attempt['id']} references an unknown topic")
    for item in wrong_questions:
        _require(item["attempt_id"] in attempt_by_id, f"wrong question {item['id']} references an unknown attempt")
        attempt = attempt_by_id[item["attempt_id"]]
        _require(item["id"] in attempt["wrong_question_ids"], f"wrong question {item['id']} is not linked by its attempt")
        _require(item["week"] == attempt["week"], f"wrong question {item['id']} has a mismatched week")
        _require(item["topic"] == attempt["topic"], f"wrong question {item['id']} has a mismatched topic")
        questions = {question["id"]: question for question in attempt["questions"]}
        _require(item["question_id"] in questions, f"wrong question {item['id']} references an unknown question")
        question = questions[item["question_id"]]
        for target, source in (
            ("prompt", "prompt"),
            ("learner_answer", "learner_answer"),
            ("verdict", "verdict"),
            ("expected_answer", "expected_answer"),
            ("rationale", "rationale"),
            ("verification", "verification"),
            ("misconception", "misconception"),
        ):
            _require(item[target] == question[source], f"wrong question {item['id']} has inconsistent {target}")
    for attempt in attempts:
        for wrong_id in attempt["wrong_question_ids"]:
            _require(wrong_id in wrong_by_id, f"attempt {attempt['id']} references unknown wrong question {wrong_id}")
            _require(
                wrong_by_id[wrong_id]["attempt_id"] == attempt["id"],
                f"wrong question {wrong_id} references a different attempt",
            )

    evidence_by_topic = {(item["week"], item["topic"]): item for item in topic_evidence}
    mastery_by_topic: dict[tuple[int, str], list[dict[str, Any]]] = {}
    for event in mastery_events:
        mastery_by_topic.setdefault((event["week"], event["topic"]), []).append(event)
    for week_number in WEEK_NUMBERS:
        for topic in weeks[str(week_number)]["topics"]:
            key = (week_number, topic["name"])
            if topic["status"] == "completed":
                evidence = evidence_by_topic.get(key)
                evidence_complete = evidence is not None and all(
                    evidence[dimension]["outcome"] == "passed"
                    for dimension in ("learning", "hands_on", "recall", "quiz")
                )
                forced_action = f"topic-complete:{week_number}:{topic['name']}"
                forced = any(
                    event["action"] == forced_action and event["recorded_at"] == topic["completed_at"]
                    for event in override_events
                )
                _require(evidence_complete or forced, f"completed topic {topic['name']!r} lacks passing evidence")
            if topic["mastery"] is not None:
                events = mastery_by_topic.get(key, [])
                _require(bool(events), f"topic {topic['name']!r} has mastery without a mastery event")
                _require(events[-1]["level"] == topic["mastery"], f"topic {topic['name']!r} mastery conflicts with its latest event")
                if topic["mastery"] == "strong":
                    _require(topic["status"] == "completed", f"strong topic {topic['name']!r} must be completed")
                    _require(topic["review_count"] > 0, f"strong topic {topic['name']!r} must have review evidence")

    _validate_date(data["last_study_date"], "progress.last_study_date", nullable=True)
    _validate_timestamp(data["updated_at"], "progress.updated_at", nullable=True)

    active_exercise = data["active_exercise"]
    if active_exercise is not None:
        _validate_active_exercise(active_exercise, "progress.active_exercise")
        if active_exercise["topic"] is not None:
            valid_topics = {topic["name"] for topic in weeks[str(active_exercise["week"])]["topics"]}
            _require(active_exercise["topic"] in valid_topics, "progress.active_exercise references an unknown topic")
    exercise_history = _require_list(data["exercise_history"], "progress.exercise_history")
    exercise_ids: list[str] = []
    for index, item in enumerate(exercise_history):
        _validate_exercise_history(item, f"progress.exercise_history[{index}]")
        if item["topic"] is not None:
            valid_topics = {topic["name"] for topic in weeks[str(item["week"])]["topics"]}
            _require(item["topic"] in valid_topics, f"progress.exercise_history[{index}] references an unknown topic")
        exercise_ids.append(item["id"])
    if active_exercise is not None:
        exercise_ids.append(active_exercise["id"])
    _require(len(exercise_ids) == len(set(exercise_ids)), "exercise ids must be unique")

    active_session = data["active_session"]
    if active_session is not None:
        _validate_active_session(active_session, "progress.active_session")
    sessions = _require_list(data["sessions"], "progress.sessions")
    session_ids: list[str] = []
    for index, item in enumerate(sessions):
        _validate_session(item, f"progress.sessions[{index}]")
        session_ids.append(item["id"])
    if active_session is not None:
        session_ids.append(active_session["id"])
    _require(len(session_ids) == len(set(session_ids)), "session ids must be unique")
    _require(not (active_exercise is not None and active_quiz is not None), "an exercise and quiz cannot both be active")
    if active_exercise is not None:
        _require(active_session is not None, "an active exercise requires an active session")
        expected_stage = "hands_on" if active_exercise["kind"] == "hands_on" else "recall"
        _require(data["current_stage"] == expected_stage, f"active exercise requires {expected_stage} stage")
        _require(
            current_topic == {"week": active_exercise["week"], "topic": active_exercise["topic"]},
            "active exercise must match current_topic",
        )
    if active_quiz is not None:
        _require(active_session is not None, "an active quiz requires an active session")
        _require(data["current_stage"] == "quiz", "active quiz requires quiz stage")
        _require(
            current_topic == {"week": active_quiz["week"], "topic": active_quiz["topic"]},
            "active quiz must match current_topic",
        )


def load_progress(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError as exc:
        raise ProgressError(f"progress file not found: {path}") from exc
    except OSError as exc:
        raise ProgressError(f"cannot read progress file {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ProgressError(f"invalid JSON in {path} at line {exc.lineno}, column {exc.colno}: {exc.msg}") from exc
    validate_progress(data)
    return data


def _atomic_write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    except Exception:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass
        raise


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
    descriptor: int | None = None
    while descriptor is None:
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                if time.time() - lock_path.stat().st_mtime > STALE_LOCK_SECONDS:
                    lock_path.unlink()
                    continue
            except FileNotFoundError:
                continue
            if time.monotonic() >= deadline:
                raise ProgressError(f"progress file is busy (lock: {lock_path})")
            time.sleep(0.05)
    try:
        os.write(descriptor, f"pid={os.getpid()}\n".encode("ascii"))
        os.close(descriptor)
        descriptor = None
        yield
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def _ensure_initialized(path: Path) -> bool:
    """Seed a missing live file from the validated, immutable template."""
    if path.exists():
        return False
    _require(path != TEMPLATE_PATH.resolve(), "the progress template is missing")
    with _exclusive_lock(path):
        if path.exists():
            return False
        template = load_progress(TEMPLATE_PATH.resolve())
        try:
            _atomic_write(path, template)
        except OSError as exc:
            raise ProgressError(f"cannot initialize progress file {path}: {exc}") from exc
    return True


def _mutate(path: Path, callback: Callable[[dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
    with _exclusive_lock(path):
        data = load_progress(path)
        result = callback(data)
        validate_progress(data)
        if "revision" not in result:
            result = {**result, "revision": data["revision"]}
        try:
            _atomic_write(path, data)
        except OSError as exc:
            raise ProgressError(f"cannot atomically write progress file {path}: {exc}") from exc
    return result


def _event_time(value: str | None = None) -> tuple[str, str, datetime]:
    if value:
        original = _parse_iso_timestamp(value, "--at")
    else:
        original = datetime.now().astimezone().replace(microsecond=0)
    utc_value = original.astimezone(timezone.utc).replace(microsecond=0)
    return utc_value.isoformat().replace("+00:00", "Z"), original.date().isoformat(), utc_value


def _touch(data: dict[str, Any], timestamp: str, study_date: str) -> None:
    data["revision"] += 1
    data["updated_at"] = timestamp
    data["last_study_date"] = study_date


def _week(data: dict[str, Any], week_number: int | None = None) -> tuple[int, dict[str, Any]]:
    selected = data["current_week"] if week_number is None else week_number
    _require(selected in WEEK_NUMBERS, "week must be 1 through 8")
    return selected, data["weeks"][str(selected)]


def _resolve_named(items: list[dict[str, Any]], field: str, query: str, kind: str) -> dict[str, Any]:
    clean_query = query.strip()
    _require(bool(clean_query), f"{kind} cannot be empty")
    exact = [item for item in items if item[field] == clean_query]
    if len(exact) == 1:
        return exact[0]
    folded = clean_query.casefold()
    insensitive = [item for item in items if item[field].casefold() == folded]
    if len(insensitive) == 1:
        return insensitive[0]
    partial = [item for item in items if folded in item[field].casefold()]
    if len(partial) == 1:
        return partial[0]
    if not partial:
        raise ProgressError(f"unknown {kind}: {query}")
    choices = "; ".join(item[field] for item in partial)
    raise ProgressError(f"ambiguous {kind} {query!r}; matches: {choices}")


def _next_id(prefix: str, existing: list[dict[str, Any]]) -> str:
    largest = 0
    for item in existing:
        identifier = item.get("id", "")
        if isinstance(identifier, str) and identifier.startswith(prefix + "-"):
            try:
                largest = max(largest, int(identifier.rsplit("-", 1)[1]))
            except ValueError:
                continue
    return f"{prefix}-{largest + 1:04d}"


def _topic_ref(
    data: dict[str, Any], query: str, week_number: int | None = None
) -> tuple[int, dict[str, Any], dict[str, Any]]:
    selected, week = _week(data, week_number)
    topic = _resolve_named(week["topics"], "name", query, "topic")
    return selected, topic, {"week": selected, "topic": topic["name"]}


def _find_topic_evidence(
    data: dict[str, Any], week_number: int, topic_name: str, *, create: bool = False
) -> dict[str, Any] | None:
    for item in data["topic_evidence"]:
        if item["week"] == week_number and item["topic"] == topic_name:
            return item
    if not create:
        return None
    empty = {"outcome": None, "recorded_at": None, "note": None}
    item = {
        "week": week_number,
        "topic": topic_name,
        "learning": dict(empty),
        "hands_on": dict(empty),
        "recall": dict(empty),
        "quiz": {**empty, "score_percent": None},
    }
    data["topic_evidence"].append(item)
    return item


def _completion_ready(evidence: dict[str, Any] | None) -> bool:
    return evidence is not None and all(
        evidence[dimension]["outcome"] == "passed"
        for dimension in ("learning", "hands_on", "recall", "quiz")
    )


def _record_override(data: dict[str, Any], action: str, reason: str | None, timestamp: str) -> None:
    clean_reason = (reason or "").strip()
    _require(bool(clean_reason), "--reason is required when --force is used")
    data["override_events"].append(
        {
            "id": _next_id("override", data["override_events"]),
            "action": action,
            "reason": clean_reason,
            "recorded_at": timestamp,
        }
    )


def _summary(data: dict[str, Any]) -> dict[str, Any]:
    week_number, week = _week(data)
    completed_topics = [item["name"] for item in week["topics"] if item["status"] == "completed"]
    pending_topics = [item["name"] for item in week["topics"] if item["status"] == "pending"]
    completed_hands_on = [item["task"] for item in week["hands_on"] if item["status"] == "completed"]
    pending_hands_on = [item["task"] for item in week["hands_on"] if item["status"] != "completed"]
    all_topics = [topic for candidate in data["weeks"].values() for topic in candidate["topics"]]
    all_hands_on = [item for candidate in data["weeks"].values() for item in candidate["hands_on"]]
    weak_topics = [
        {"week": number, "topic": topic["name"], "review_count": topic["review_count"]}
        for number in WEEK_NUMBERS
        for topic in data["weeks"][str(number)]["topics"]
        if topic["mastery"] == "weak"
    ]
    return {
        "revision": data["revision"],
        "current_week": week_number,
        "current_stage": data["current_stage"],
        "current_topic": data["current_topic"],
        "week_title": week["title"],
        "completed_topics": completed_topics,
        "pending_topics": pending_topics,
        "completed_hands_on": completed_hands_on,
        "pending_hands_on": pending_hands_on,
        "overall": {
            "topics_completed": sum(item["status"] == "completed" for item in all_topics),
            "topics_total": len(all_topics),
            "hands_on_completed": sum(item["status"] == "completed" for item in all_hands_on),
            "hands_on_total": len(all_hands_on),
        },
        "quiz_attempts": len(data["quiz_attempts"]),
        "unresolved_wrong_questions": sum(not item["resolved"] for item in data["wrong_questions"]),
        "weak_topics": weak_topics,
        "last_study_date": data["last_study_date"],
        "active_quiz": data["active_quiz"],
        "active_exercise": data["active_exercise"],
        "active_session": data["active_session"],
    }


def _resume(data: dict[str, Any]) -> dict[str, Any]:
    week_number, week = _week(data)
    suggested_topic = data["current_topic"] or next(
        ({"week": week_number, "topic": item["name"]} for item in week["topics"] if item["status"] == "pending"),
        None,
    )
    pending_hands_on = next((item for item in week["hands_on"] if item["status"] != "completed"), None)
    return {
        "revision": data["revision"],
        "current_week": week_number,
        "current_stage": data["current_stage"],
        "current_topic": data["current_topic"],
        "week_title": week["title"],
        "next_topic": suggested_topic["topic"] if suggested_topic else None,
        "next_hands_on": None
        if pending_hands_on is None
        else {"task": pending_hands_on["task"], "status": pending_hands_on["status"]},
        "active_quiz": data["active_quiz"],
        "active_exercise": data["active_exercise"],
        "active_session": data["active_session"],
        "week_complete": suggested_topic is None and pending_hands_on is None,
        "last_study_date": data["last_study_date"],
    }


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _print_status_human(summary: dict[str, Any]) -> None:
    overall = summary["overall"]
    print(f"Week {summary['current_week']}: {summary['week_title']}")
    print(f"Stage: {summary['current_stage']}")
    print(f"Current topic: {summary['current_topic']['topic'] if summary['current_topic'] else '-'}")
    print(f"Topics: {len(summary['completed_topics'])}/{len(summary['completed_topics']) + len(summary['pending_topics'])}")
    print(f"Hands-on: {len(summary['completed_hands_on'])}/{len(summary['completed_hands_on']) + len(summary['pending_hands_on'])}")
    print(f"Overall topics: {overall['topics_completed']}/{overall['topics_total']}")
    print(f"Quiz attempts: {summary['quiz_attempts']}")
    print(f"Weak topics: {len(summary['weak_topics'])}")
    print(f"Unresolved wrong questions: {summary['unresolved_wrong_questions']}")
    print(f"Last study date: {summary['last_study_date'] or '-'}")


def _print_resume_human(resume: dict[str, Any]) -> None:
    print(f"Week {resume['current_week']}: {resume['week_title']}")
    print(f"Stage: {resume['current_stage']}")
    if resume["active_quiz"]:
        quiz = resume["active_quiz"]
        answered = sum(question["verdict"] is not None for question in quiz["questions"])
        print(f"Active quiz: {quiz['topic']} ({answered}/{len(quiz['questions'])} graded)")
    if resume["active_exercise"]:
        exercise = resume["active_exercise"]
        print(f"Active exercise: {exercise['title']} (hint {exercise['hint_level']}/{MAX_HINT_LEVEL})")
    print(f"Next topic: {resume['next_topic'] or '-'}")
    next_hands_on = resume["next_hands_on"]
    print(f"Next hands-on: {next_hands_on['task'] if next_hands_on else '-'}")
    if resume["week_complete"]:
        print("This week's topics and hands-on tasks are complete.")


def _add_at_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--at", metavar="ISO_TIMESTAMP", help="event time including timezone (default: now)")


def _add_force_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--force", action="store_true", help="override the normal transition guard")
    parser.add_argument("--reason", help="required explanation when --force is used")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Safely inspect and update ServiceNow CSA learning progress.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--file",
        type=Path,
        default=Path(os.environ.get("SERVICENOW_CSA_PROGRESS_FILE", DEFAULT_PROGRESS_PATH)),
        help="progress JSON path",
    )
    commands = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)

    commands.add_parser("init", help="create live progress from the template if missing")
    validate = commands.add_parser("validate", help="validate the live file or template")
    validate.add_argument("--template", action="store_true", help="validate the immutable initial template")
    commands.add_parser("show", help="print the complete progress JSON")
    status = commands.add_parser("status", help="show current and overall progress")
    status.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    resume = commands.add_parser("resume", help="show where the next study session should resume")
    resume.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    todo = commands.add_parser("todo", help="show only the next topic and hands-on task")
    todo.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    weaknesses = commands.add_parser("weaknesses", help="show weak topics and unresolved wrong questions")
    weaknesses.add_argument("--json", action="store_true", help="emit machine-readable JSON")

    week = commands.add_parser("week", help="show or change the current week")
    week.add_argument("number", nargs="?", type=int, choices=WEEK_NUMBERS)
    _add_force_arguments(week)
    _add_at_argument(week)
    stage = commands.add_parser("stage", help="show or change the resumable daily study stage")
    stage.add_argument("name", nargs="?", choices=sorted(STUDY_STAGES))
    _add_force_arguments(stage)
    _add_at_argument(stage)
    focus = commands.add_parser("focus", help="show or set the current topic")
    focus.add_argument("topic", nargs="?")
    focus.add_argument("--week", type=int, choices=WEEK_NUMBERS)
    _add_at_argument(focus)
    advance = commands.add_parser("advance-week", help="advance after finishing the current week")
    _add_force_arguments(advance)
    _add_at_argument(advance)

    topic = commands.add_parser("topic", help="mark a topic completed or pending")
    topic_actions = topic.add_subparsers(dest="topic_action", metavar="ACTION", required=True)
    for action in ("complete", "reopen"):
        action_parser = topic_actions.add_parser(action)
        action_parser.add_argument("name")
        action_parser.add_argument("--week", type=int, choices=WEEK_NUMBERS)
        _add_force_arguments(action_parser)
        _add_at_argument(action_parser)

    evidence = commands.add_parser("evidence", help="record a topic completion check")
    evidence.add_argument("topic")
    evidence.add_argument("dimension", choices=sorted(EVIDENCE_DIMENSIONS))
    evidence.add_argument("outcome", choices=sorted(EVIDENCE_OUTCOMES))
    evidence.add_argument("--note", required=True)
    evidence.add_argument("--week", type=int, choices=WEEK_NUMBERS)
    _add_at_argument(evidence)

    hands_on = commands.add_parser("hands-on", help="set a hands-on task status")
    hands_on.add_argument("task")
    hands_on.add_argument("status", choices=sorted(HANDS_ON_STATUSES))
    hands_on.add_argument("--week", type=int, choices=WEEK_NUMBERS)
    _add_at_argument(hands_on)

    quiz = commands.add_parser("quiz", help="manage a resumable graded quiz")
    quiz_actions = quiz.add_subparsers(dest="quiz_action", metavar="ACTION", required=True)
    quiz_actions.add_parser("show")
    quiz_start = quiz_actions.add_parser("start")
    quiz_start.add_argument("--topic", required=True)
    quiz_start.add_argument("--question", action="append", required=True, metavar="PROMPT")
    quiz_start.add_argument("--week", type=int, choices=WEEK_NUMBERS)
    _add_at_argument(quiz_start)
    quiz_answer = quiz_actions.add_parser("answer")
    quiz_answer.add_argument("question_id")
    quiz_answer.add_argument("--verdict", choices=sorted(QUESTION_VERDICTS), required=True)
    quiz_answer.add_argument("--learner-answer", required=True)
    quiz_answer.add_argument("--expected-answer", required=True)
    quiz_answer.add_argument("--rationale", required=True)
    quiz_answer.add_argument("--verification", required=True)
    quiz_answer.add_argument("--misconception")
    _add_at_argument(quiz_answer)
    quiz_finish = quiz_actions.add_parser("finish")
    _add_at_argument(quiz_finish)
    quiz_resolve = quiz_actions.add_parser("resolve")
    quiz_resolve.add_argument("wrong_question_id")
    _add_at_argument(quiz_resolve)

    mastery = commands.add_parser("mastery", help="set topic mastery with evidence")
    mastery.add_argument("topic")
    mastery.add_argument("level", choices=sorted(MASTERY_LEVELS))
    mastery.add_argument("--reason", required=True)
    mastery.add_argument("--evidence", action="append", required=True)
    mastery.add_argument("--week", type=int, choices=WEEK_NUMBERS)
    _add_at_argument(mastery)

    review = commands.add_parser("review", help="increment a topic's review count")
    review.add_argument("topic")
    review.add_argument("--week", type=int, choices=WEEK_NUMBERS)
    review.add_argument("--count", type=int, default=1)
    _add_at_argument(review)

    exercise = commands.add_parser("exercise", help="manage a resumable exercise and hint level")
    exercise_actions = exercise.add_subparsers(dest="exercise_action", metavar="ACTION", required=True)
    exercise_start = exercise_actions.add_parser("start")
    exercise_start.add_argument("title")
    exercise_start.add_argument("--kind", choices=sorted(EXERCISE_KINDS), required=True)
    exercise_start.add_argument("--topic", required=True)
    exercise_start.add_argument("--objective", required=True)
    exercise_start.add_argument("--constraint", action="append", default=[])
    exercise_start.add_argument("--deliverable", required=True)
    exercise_start.add_argument("--acceptance", action="append", required=True)
    exercise_start.add_argument("--week", type=int, choices=WEEK_NUMBERS)
    _add_at_argument(exercise_start)
    exercise_hint = exercise_actions.add_parser("hint")
    _add_at_argument(exercise_hint)
    exercise_answer = exercise_actions.add_parser("answer")
    _add_at_argument(exercise_answer)
    exercise_end = exercise_actions.add_parser("end")
    exercise_end.add_argument("--status", choices=sorted(EXERCISE_END_STATUSES), default="completed")
    _add_at_argument(exercise_end)

    session = commands.add_parser("session", help="start or end a study session")
    session_actions = session.add_subparsers(dest="session_action", metavar="ACTION", required=True)
    session_start = session_actions.add_parser("start")
    _add_at_argument(session_start)
    session_end = session_actions.add_parser("end")
    session_end.add_argument("--note")
    _add_at_argument(session_end)
    return parser


def _handle_read(args: argparse.Namespace, data: dict[str, Any]) -> bool:
    if args.command == "validate":
        print(f"valid: {args.file.resolve()}")
        return True
    if args.command == "show":
        _print_json(data)
        return True
    if args.command == "status":
        result = _summary(data)
        _print_json(result) if args.json else _print_status_human(result)
        return True
    if args.command in {"resume", "todo"}:
        result = _resume(data)
        _print_json(result) if args.json else _print_resume_human(result)
        return True
    if args.command == "weaknesses":
        summary = _summary(data)
        result = {
            "weak_topics": summary["weak_topics"],
            "unresolved_wrong_questions": [item for item in data["wrong_questions"] if not item["resolved"]],
        }
        if args.json:
            _print_json(result)
        else:
            if not result["weak_topics"] and not result["unresolved_wrong_questions"]:
                print("No recorded weaknesses.")
            for item in result["weak_topics"]:
                print(f"Week {item['week']} topic: {item['topic']} (reviews: {item['review_count']})")
            for item in result["unresolved_wrong_questions"]:
                print(f"{item['id']}: {item['prompt']}")
        return True
    if args.command == "week" and args.number is None:
        week_number, week = _week(data)
        print(f"Week {week_number}: {week['title']}")
        return True
    if args.command == "stage" and args.name is None:
        print(data["current_stage"])
        return True
    if args.command == "focus" and args.topic is None:
        _print_json(data["current_topic"])
        return True
    if args.command == "quiz" and args.quiz_action == "show":
        _print_json(data["active_quiz"])
        return True
    return False


def _mutation_callback(args: argparse.Namespace) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def mutate(data: dict[str, Any]) -> dict[str, Any]:
        timestamp, study_date, event_datetime = _event_time(getattr(args, "at", None))

        if args.command == "week":
            previous = data["current_week"]
            _require(args.number != previous, f"already at Week {previous}")
            _require(data["active_session"] is None, "end the active study session before changing week")
            _require(data["active_exercise"] is None and data["active_quiz"] is None, "finish active work before changing week")
            _, current = _week(data)
            complete = all(item["status"] == "completed" for item in current["topics"] + current["hands_on"])
            normal = args.number == previous + 1 and complete
            if not normal:
                _require(args.force, "week change is not a completed next-week transition; use --force with --reason")
                _record_override(data, f"week-change:{previous}->{args.number}", args.reason, timestamp)
            data["current_week"] = args.number
            data["current_stage"] = "planning"
            data["current_topic"] = None
            _touch(data, timestamp, study_date)
            return {"current_week": args.number, "previous_week": previous, "forced": not normal}

        if args.command == "stage":
            previous = data["current_stage"]
            _require(args.name != previous, f"stage is already {previous}")
            if data["active_exercise"] is not None:
                raise ProgressError("end the active exercise before changing stage")
            if data["active_quiz"] is not None:
                raise ProgressError("finish the active quiz before changing stage")
            allowed = {
                "planning": {"university", "hands_on", "quiz", "review"},
                "university": {"hands_on", "quiz", "review", "planning"},
                "hands_on": {"recall", "quiz", "review", "planning"},
                "recall": {"quiz", "review", "planning"},
                "quiz": {"review", "planning"},
                "review": {"planning", "university", "hands_on", "recall", "quiz"},
            }
            if args.name not in allowed[previous]:
                _require(args.force, "nonstandard stage transition; use --force with --reason")
                _record_override(data, f"stage-change:{previous}->{args.name}", args.reason, timestamp)
            data["current_stage"] = args.name
            _touch(data, timestamp, study_date)
            return {"current_stage": args.name, "previous_stage": previous}

        if args.command == "focus":
            week_number, _, reference = _topic_ref(data, args.topic, args.week)
            _require(week_number == data["current_week"], "focus must belong to current_week")
            if data["active_exercise"] is not None or data["active_quiz"] is not None:
                _require(data["current_topic"] == reference, "finish active work before changing topic")
            data["current_topic"] = reference
            _touch(data, timestamp, study_date)
            return reference

        if args.command == "advance-week":
            current_number, week = _week(data)
            _require(current_number < 8, "already at Week 8")
            _require(data["active_session"] is None, "end the active study session before advancing")
            _require(data["active_exercise"] is None and data["active_quiz"] is None, "finish active work before advancing")
            incomplete_topics = [item["name"] for item in week["topics"] if item["status"] != "completed"]
            incomplete_hands_on = [item["task"] for item in week["hands_on"] if item["status"] != "completed"]
            forced = bool(incomplete_topics or incomplete_hands_on)
            if forced:
                _require(args.force, f"Week {current_number} is incomplete; use --force with --reason")
                _record_override(data, f"advance-week:{current_number}->{current_number + 1}", args.reason, timestamp)
            data["current_week"] = current_number + 1
            data["current_stage"] = "planning"
            data["current_topic"] = None
            _touch(data, timestamp, study_date)
            return {"previous_week": current_number, "current_week": current_number + 1, "forced": forced}

        if args.command == "topic":
            week_number, topic, reference = _topic_ref(data, args.name, args.week)
            if args.topic_action == "complete":
                _require(topic["status"] != "completed", f"topic {topic['name']!r} is already completed")
                evidence = _find_topic_evidence(data, week_number, topic["name"])
                if not _completion_ready(evidence):
                    _require(args.force, "topic requires passing learning, hands-on, recall, and quiz evidence")
                    _record_override(data, f"topic-complete:{week_number}:{topic['name']}", args.reason, timestamp)
                topic["status"] = "completed"
                topic["completed_at"] = timestamp
                if week_number == data["current_week"]:
                    next_topic = next((item for item in data["weeks"][str(week_number)]["topics"] if item["status"] == "pending"), None)
                    data["current_topic"] = None if next_topic is None else {"week": week_number, "topic": next_topic["name"]}
            else:
                _require(topic["status"] == "completed", f"topic {topic['name']!r} is already pending")
                topic["status"] = "pending"
                topic["completed_at"] = None
                topic["mastery"] = None
                if week_number == data["current_week"]:
                    data["current_topic"] = reference
            _touch(data, timestamp, study_date)
            return {"week": week_number, "topic": topic["name"], "status": topic["status"]}

        if args.command == "evidence":
            week_number, topic, reference = _topic_ref(data, args.topic, args.week)
            note = args.note.strip()
            _require(bool(note), "--note cannot be empty")
            evidence = _find_topic_evidence(data, week_number, topic["name"], create=True)
            assert evidence is not None
            evidence[args.dimension] = {"outcome": args.outcome, "recorded_at": timestamp, "note": note}
            if args.outcome == "failed":
                topic["status"] = "pending"
                topic["completed_at"] = None
                topic["mastery"] = None
            if week_number == data["current_week"]:
                data["current_topic"] = reference
            _touch(data, timestamp, study_date)
            return {"week": week_number, "topic": topic["name"], "dimension": args.dimension, "outcome": args.outcome}

        if args.command == "hands-on":
            week_number, week = _week(data, args.week)
            item = _resolve_named(week["hands_on"], "task", args.task, "hands-on task")
            item["status"] = args.status
            item["completed_at"] = timestamp if args.status == "completed" else None
            _touch(data, timestamp, study_date)
            return {"week": week_number, "task": item["task"], "status": args.status}

        if args.command == "mastery":
            week_number, topic, _ = _topic_ref(data, args.topic, args.week)
            reason = args.reason.strip()
            evidence_notes = [value.strip() for value in args.evidence]
            _require(bool(reason) and all(evidence_notes), "mastery reason and evidence cannot be empty")
            if args.level == "strong":
                _require(topic["status"] == "completed", "strong mastery requires a completed topic")
                _require(topic["review_count"] > 0, "strong mastery requires at least one review")
            event = {
                "id": _next_id("mastery", data["mastery_events"]),
                "week": week_number,
                "topic": topic["name"],
                "previous": topic["mastery"],
                "level": args.level,
                "reason": reason,
                "evidence": evidence_notes,
                "recorded_at": timestamp,
            }
            data["mastery_events"].append(event)
            topic["mastery"] = args.level
            _touch(data, timestamp, study_date)
            return event

        if args.command == "review":
            _require(args.count > 0, "--count must be positive")
            week_number, topic, _ = _topic_ref(data, args.topic, args.week)
            topic["review_count"] += args.count
            topic["last_reviewed_on"] = study_date
            _touch(data, timestamp, study_date)
            return {"week": week_number, "topic": topic["name"], "review_count": topic["review_count"]}

        if args.command == "quiz" and args.quiz_action == "start":
            _require(data["active_session"] is not None, "start a study session before starting a quiz")
            _require(data["active_quiz"] is None and data["active_exercise"] is None, "finish active work first")
            week_number, topic, reference = _topic_ref(data, args.topic, args.week)
            _require(week_number == data["current_week"], "quiz topic must belong to current_week")
            prompts = [value.strip() for value in args.question]
            _require(3 <= len(prompts) <= 5, "quiz requires 3 through 5 --question values")
            _require(all(prompts), "quiz questions cannot be empty")
            quiz_id = _next_id("quiz", data["quiz_attempts"])
            active = {
                "id": quiz_id,
                "week": week_number,
                "topic": topic["name"],
                "started_at": timestamp,
                "questions": [
                    {
                        "id": f"{quiz_id}-q{index}",
                        "prompt": prompt,
                        "learner_answer": None,
                        "verdict": None,
                        "expected_answer": None,
                        "rationale": None,
                        "verification": None,
                        "misconception": None,
                        "answered_at": None,
                    }
                    for index, prompt in enumerate(prompts, start=1)
                ],
            }
            data["active_quiz"] = active
            data["current_topic"] = reference
            data["current_stage"] = "quiz"
            _touch(data, timestamp, study_date)
            return active

        if args.command == "quiz" and args.quiz_action == "answer":
            active = data["active_quiz"]
            _require(active is not None, "no quiz is active")
            matches = [question for question in active["questions"] if question["id"] == args.question_id]
            _require(bool(matches), f"unknown active question id: {args.question_id}")
            question = matches[0]
            _require(question["verdict"] is None, f"question {args.question_id} is already graded")
            _require(event_datetime >= _parse_iso_timestamp(active["started_at"], "active_quiz.started_at"), "answer time cannot precede quiz start")
            values = {
                "learner_answer": args.learner_answer.strip(),
                "expected_answer": args.expected_answer.strip(),
                "rationale": args.rationale.strip(),
                "verification": args.verification.strip(),
            }
            _require(all(values.values()), "answer fields cannot be empty")
            misconception = args.misconception.strip() if args.misconception else None
            if args.verdict != "correct":
                _require(bool(misconception), "--misconception is required for partial or incorrect answers")
            question.update(values)
            question["verdict"] = args.verdict
            question["misconception"] = misconception
            question["answered_at"] = timestamp
            _touch(data, timestamp, study_date)
            return question

        if args.command == "quiz" and args.quiz_action == "finish":
            active = data["active_quiz"]
            _require(active is not None, "no quiz is active")
            _require(all(question["verdict"] is not None for question in active["questions"]), "grade every quiz question before finishing")
            started = _parse_iso_timestamp(active["started_at"], "active_quiz.started_at")
            _require(event_datetime >= started, "quiz finish time cannot precede start time")
            _require(all(event_datetime >= _parse_iso_timestamp(question["answered_at"], "question.answered_at") for question in active["questions"]), "quiz finish time cannot precede an answer")
            earned = sum(_question_points(question["verdict"]) for question in active["questions"])
            total = len(active["questions"])
            score = round(earned * 100.0 / total, 2)
            wrong_ids: list[str] = []
            for question in active["questions"]:
                if question["verdict"] == "correct":
                    continue
                wrong_id = _next_id("wrong", data["wrong_questions"])
                data["wrong_questions"].append(
                    {
                        "id": wrong_id,
                        "attempt_id": active["id"],
                        "question_id": question["id"],
                        "week": active["week"],
                        "topic": active["topic"],
                        "prompt": question["prompt"],
                        "learner_answer": question["learner_answer"],
                        "verdict": question["verdict"],
                        "expected_answer": question["expected_answer"],
                        "rationale": question["rationale"],
                        "verification": question["verification"],
                        "misconception": question["misconception"],
                        "recorded_at": timestamp,
                        "resolved": False,
                        "resolved_at": None,
                    }
                )
                wrong_ids.append(wrong_id)
            attempt = {
                **active,
                "completed_at": timestamp,
                "result": {"earned_points": earned, "total": total, "score_percent": score, "passed": score >= PASS_PERCENT},
                "wrong_question_ids": wrong_ids,
            }
            data["quiz_attempts"].append(attempt)
            data["active_quiz"] = None
            evidence = _find_topic_evidence(data, active["week"], active["topic"], create=True)
            assert evidence is not None
            evidence["quiz"] = {
                "outcome": "passed" if score >= PASS_PERCENT else "failed",
                "recorded_at": timestamp,
                "note": f"{active['id']}: {score}%",
                "score_percent": score,
            }
            if score < PASS_PERCENT:
                _, topic, _ = _topic_ref(data, active["topic"], active["week"])
                topic["status"] = "pending"
                topic["completed_at"] = None
                topic["mastery"] = None
            data["current_stage"] = "review"
            _touch(data, timestamp, study_date)
            return attempt

        if args.command == "quiz" and args.quiz_action == "resolve":
            matches = [item for item in data["wrong_questions"] if item["id"] == args.wrong_question_id]
            _require(bool(matches), f"unknown wrong question id: {args.wrong_question_id}")
            item = matches[0]
            _require(not item["resolved"], f"wrong question {item['id']} is already resolved")
            item["resolved"] = True
            item["resolved_at"] = timestamp
            _touch(data, timestamp, study_date)
            return {"wrong_question_id": item["id"], "resolved": True}

        if args.command == "exercise" and args.exercise_action == "start":
            _require(data["active_session"] is not None, "start a study session before starting an exercise")
            _require(data["active_exercise"] is None and data["active_quiz"] is None, "finish active work first")
            week_number, topic, reference = _topic_ref(data, args.topic, args.week)
            _require(week_number == data["current_week"], "exercise topic must belong to current_week")
            active = {
                "id": _next_id("exercise", data["exercise_history"]),
                "title": args.title.strip(),
                "kind": args.kind,
                "topic": topic["name"],
                "week": week_number,
                "objective": args.objective.strip(),
                "constraints": [value.strip() for value in args.constraint],
                "deliverable": args.deliverable.strip(),
                "acceptance_criteria": [value.strip() for value in args.acceptance],
                "started_at": timestamp,
                "hint_level": 0,
                "answer_revealed": False,
            }
            _require(all([active["title"], active["objective"], active["deliverable"]]), "exercise text fields cannot be empty")
            _require(all(active["constraints"]), "exercise constraints cannot be empty strings")
            _require(all(active["acceptance_criteria"]), "acceptance criteria cannot be empty")
            data["active_exercise"] = active
            data["current_topic"] = reference
            data["current_stage"] = "hands_on" if args.kind == "hands_on" else "recall"
            _touch(data, timestamp, study_date)
            return active

        if args.command == "exercise" and args.exercise_action == "hint":
            active = data["active_exercise"]
            _require(active is not None, "no exercise is active")
            _require(active["hint_level"] < MAX_HINT_LEVEL, f"hint level is already {MAX_HINT_LEVEL}")
            active["hint_level"] += 1
            _touch(data, timestamp, study_date)
            return {"exercise_id": active["id"], "hint_level": active["hint_level"], "max_hint_level": MAX_HINT_LEVEL}

        if args.command == "exercise" and args.exercise_action == "answer":
            active = data["active_exercise"]
            _require(active is not None, "no exercise is active")
            _require(not active["answer_revealed"], "the answer was already revealed")
            active["answer_revealed"] = True
            _touch(data, timestamp, study_date)
            return {"exercise_id": active["id"], "answer_revealed": True}

        if args.command == "exercise" and args.exercise_action == "end":
            active = data["active_exercise"]
            _require(active is not None, "no exercise is active")
            started = _parse_iso_timestamp(active["started_at"], "active_exercise.started_at")
            _require(event_datetime >= started, "exercise end time cannot precede start time")
            history_item = {
                "id": active["id"],
                "title": active["title"],
                "kind": active["kind"],
                "topic": active["topic"],
                "week": active["week"],
                "objective": active["objective"],
                "constraints": active["constraints"],
                "deliverable": active["deliverable"],
                "acceptance_criteria": active["acceptance_criteria"],
                "started_at": active["started_at"],
                "ended_at": timestamp,
                "status": args.status,
                "final_hint_level": active["hint_level"],
                "answer_revealed": active["answer_revealed"],
            }
            data["exercise_history"].append(history_item)
            data["active_exercise"] = None
            _touch(data, timestamp, study_date)
            return history_item

        if args.command == "session" and args.session_action == "start":
            _require(data["active_session"] is None, "a study session is already active")
            active = {
                "id": _next_id("session", data["sessions"]),
                "started_at": timestamp,
                "start_week": data["current_week"],
            }
            data["active_session"] = active
            _touch(data, timestamp, study_date)
            return active

        if args.command == "session" and args.session_action == "end":
            active = data["active_session"]
            _require(active is not None, "no study session is active")
            _require(data["active_exercise"] is None and data["active_quiz"] is None, "finish active work before ending the session")
            started = _parse_iso_timestamp(active["started_at"], "active_session.started_at")
            _require(event_datetime >= started, "session end time cannot precede start time")
            session_item = {
                "id": active["id"],
                "started_at": active["started_at"],
                "ended_at": timestamp,
                "start_week": active["start_week"],
                "end_week": data["current_week"],
                "duration_minutes": round((event_datetime - started).total_seconds() / 60.0, 2),
                "note": args.note,
            }
            data["sessions"].append(session_item)
            data["active_session"] = None
            _touch(data, timestamp, study_date)
            return session_item

        raise ProgressError(f"unsupported command: {args.command}")

    return mutate


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.file = args.file.expanduser().resolve()
    try:
        if args.command == "validate" and args.template:
            args.file = TEMPLATE_PATH.resolve()
        if args.command == "init":
            _require(args.file != TEMPLATE_PATH.resolve(), "the template is immutable and cannot be initialized in place")
            created = _ensure_initialized(args.file)
            load_progress(args.file)
            _print_json({"path": str(args.file), "created": created})
            return 0
        _ensure_initialized(args.file)
        data = load_progress(args.file)
        if _handle_read(args, data):
            return 0
        _require(args.file != TEMPLATE_PATH.resolve(), "the template is immutable; update the live progress file instead")
        result = _mutate(args.file, _mutation_callback(args))
        _print_json(result)
        return 0
    except ProgressError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
