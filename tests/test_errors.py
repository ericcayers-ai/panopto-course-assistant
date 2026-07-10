"""Tests for the shared error envelope (§17).

Before §17 each integration raised a bare ``Exception`` subclass, so callers
special-cased six unrelated failure shapes. These tests pin the contract that
replaced that: one base class, one category taxonomy, one JSON body.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.errors import CATEGORIES, AppError, install_error_handler
from app.imports.moodle_api import MoodleApiError
from app.imports.moodle_resources import ResourceError
from app.imports.moodle_web import MoodleWebError
from app.integrations.anki import AnkiError
from app.integrations.notion import NotionError
from app.jobs import classify_failure
from app.llm import LLMError

# Every integration error the app can raise out of a route.
ALL_ERRORS = [MoodleApiError, MoodleWebError, ResourceError,
              AnkiError, NotionError, LLMError]


@pytest.mark.parametrize("cls", ALL_ERRORS)
def test_every_integration_error_shares_the_base(cls):
    assert issubclass(cls, AppError)


@pytest.mark.parametrize("cls", ALL_ERRORS)
def test_every_error_declares_a_known_category_and_status(cls):
    exc = cls("boom")
    assert exc.category in CATEGORIES
    assert 400 <= exc.status_code <= 599


@pytest.mark.parametrize("cls", ALL_ERRORS)
def test_every_error_renders_one_envelope(cls):
    """A single handler serves all six; the frontend needs one error path."""
    app = FastAPI()
    install_error_handler(app)

    @app.get("/boom")
    def boom():
        raise cls("it broke")

    r = TestClient(app, raise_server_exceptions=False).get("/boom")
    assert r.status_code == cls.status_code
    body = r.json()
    # `detail` stays for back-compat with FastAPI's own HTTPException shape.
    assert body["detail"] == "it broke"
    assert body["error"] == {"message": "it broke",
                             "category": cls.category, "detail": {}}


def test_detail_dict_travels_with_the_error():
    exc = NotionError("rejected", detail={"database_id": "abc"})
    assert exc.payload()["error"]["detail"] == {"database_id": "abc"}


def test_category_and_status_are_overridable_per_raise():
    exc = LLMError("no key", category="authentication", status_code=401)
    assert (exc.category, exc.status_code) == ("authentication", 401)


@pytest.mark.parametrize("cls", ALL_ERRORS)
def test_job_queue_trusts_the_declared_category(cls):
    """§3's classifier defers to an AppError rather than re-guessing from text."""
    assert classify_failure(cls("some opaque message")) == cls.category


def test_classify_failure_still_guesses_for_plain_exceptions():
    assert classify_failure(ModuleNotFoundError("no whisper")) == "dependency"
    assert classify_failure(FileNotFoundError("gone")) == "filesystem"
    assert classify_failure(Exception("connection timed out")) == "network"
    assert classify_failure(Exception("something odd")) == "unknown"
