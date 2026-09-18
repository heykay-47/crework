import json
from collections.abc import Iterator
from typing import Any, cast

import pytest

from feedback_triage.github import GitHubApiError, GitHubIssueClient, GitHubTransportError
from feedback_triage.models import IssuePayload


class Response:
    def __init__(self, payload: Any, status: int = 200) -> None:
        self.payload = payload
        self.status = status

    def __enter__(self) -> "Response":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode()


def issue(number: int, *, marker: str, state: str = "open") -> dict[str, Any]:
    return {
        "number": number,
        "title": f"Issue {number}",
        "body": f"Details\n{marker}",
        "html_url": f"https://github.com/demo/feedback/issues/{number}",
        "repository_url": "https://api.github.com/repos/demo/feedback",
        "state": state,
    }


def test_marker_search_covers_all_issue_pages_and_closed_issues(monkeypatch: pytest.MonkeyPatch) -> None:
    marker = "<!-- crework:v1 source_sha256=" + "a" * 64 + " candidate_id=cand_aaaaaaaaaaaaaaaa -->"
    first_page = [issue(index, marker="unrelated") for index in range(1, 100)]
    first_page.append(issue(100, marker=marker))
    second_page = [issue(101, marker=marker, state="closed")]
    responses: Iterator[Response] = iter([Response(first_page), Response(second_page)])
    requests: list[str] = []

    def open_request(request: Any, timeout: float) -> Response:
        requests.append(request.full_url)
        return next(responses)

    monkeypatch.setattr("urllib.request.urlopen", open_request)
    client = GitHubIssueClient("secret-token")

    matches = client.find_marker("demo/feedback", marker)

    assert [match.number for match in matches] == [100, 101]
    assert "state=all" in requests[0]
    assert "page=2" in requests[1]


def test_marker_search_skips_issues_without_text_bodies(monkeypatch: pytest.MonkeyPatch) -> None:
    marker = "<!-- crework:v1 source_sha256=" + "a" * 64 + " candidate_id=cand_aaaaaaaaaaaaaaaa -->"
    responses: Iterator[Response] = iter(
        [
            Response(
                [
                    {
                        "number": 1,
                        "title": "Empty issue",
                        "body": None,
                        "html_url": "https://github.com/demo/feedback/issues/1",
                        "repository_url": "https://api.github.com/repos/demo/feedback",
                        "state": "open",
                    },
                    issue(2, marker=marker),
                ]
            )
        ]
    )
    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout: next(responses))

    matches = GitHubIssueClient("secret-token").find_marker("demo/feedback", marker)

    assert [match.number for match in matches] == [2]


def test_create_sends_authenticated_json_and_parses_destination(monkeypatch: pytest.MonkeyPatch) -> None:
    marker = "<!-- crework:v1 source_sha256=" + "a" * 64 + " candidate_id=cand_aaaaaaaaaaaaaaaa -->"
    body = f"Body\n{marker}"
    response = Response(issue(4, marker=marker))
    seen: dict[str, Any] = {}

    def open_request(request: Any, timeout: float) -> Response:
        seen["method"] = request.method
        seen["authorization"] = request.headers["Authorization"]
        seen["version"] = request.headers["X-github-api-version"]
        seen["payload"] = json.loads(request.data)
        return response

    monkeypatch.setattr("urllib.request.urlopen", open_request)
    client = GitHubIssueClient("secret-token")
    payload = IssuePayload(title="A title", body=body)

    created = client.create_issue("demo/feedback", payload)

    assert created.number == 4
    assert created.destination_repository == "demo/feedback"
    assert seen == {
        "method": "POST",
        "authorization": "Bearer secret-token",
        "version": "2022-11-28",
        "payload": {"title": "A title", "body": body},
    }


def test_definitive_http_errors_are_not_transport_uncertainty(monkeypatch: pytest.MonkeyPatch) -> None:
    def open_request(request: Any, timeout: float) -> Response:
        from urllib.error import HTTPError

        raise HTTPError(request.full_url, 422, "invalid", cast(Any, {}), None)

    monkeypatch.setattr("urllib.request.urlopen", open_request)
    client = GitHubIssueClient("secret-token")

    with pytest.raises(GitHubApiError) as raised:
        client.create_issue("demo/feedback", IssuePayload(title="A", body="B"))

    assert raised.value.status_code == 422
    assert raised.value.definitive is True


def test_transport_errors_are_uncertain(monkeypatch: pytest.MonkeyPatch) -> None:
    def open_request(request: Any, timeout: float) -> Response:
        raise TimeoutError("timed out")

    monkeypatch.setattr("urllib.request.urlopen", open_request)
    client = GitHubIssueClient("secret-token")

    with pytest.raises(GitHubTransportError):
        client.create_issue("demo/feedback", IssuePayload(title="A", body="B"))
