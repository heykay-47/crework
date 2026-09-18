"""Small, non-retrying GitHub Issues adapter used by the write coordinator."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from .approval import normalize_destination
from .models import IssuePayload

GITHUB_API_VERSION = "2022-11-28"


class GitHubApiError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, definitive: bool = False) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.definitive = definitive


class GitHubTransportError(GitHubApiError):
    def __init__(self, message: str) -> None:
        super().__init__(message, definitive=False)


class GitHubResponseError(GitHubApiError):
    """GitHub returned a response that cannot be safely interpreted."""


@dataclass(frozen=True, slots=True)
class GitHubIssue:
    destination_repository: str
    number: int
    title: str
    body: str
    html_url: str
    state: str


class GitHubIssueClient:
    """Adapter for the small set of GitHub REST calls needed for safe writes.

    The adapter intentionally never retries a POST.  A caller that cannot
    classify the result must reconcile the exact marker before taking any
    further write action.
    """

    def __init__(self, token: str, *, api_url: str = "https://api.github.com", timeout: float = 30.0) -> None:
        if not token.strip():
            raise ValueError("GitHub token must not be blank")
        self._token = token
        self._api_url = api_url.rstrip("/")
        self._timeout = timeout

    def find_marker(self, destination_repository: str, marker: str) -> tuple[GitHubIssue, ...]:
        destination = normalize_destination(destination_repository)
        owner, repository = destination.split("/", 1)
        matches: list[GitHubIssue] = []
        page = 1
        while True:
            data = self._request(
                "GET",
                f"/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(repository)}"
                f"/issues?state=all&per_page=100&page={page}",
            )
            if not isinstance(data, list):
                raise GitHubResponseError("GitHub issue listing was not a JSON array")
            for raw_issue in data:
                if not isinstance(raw_issue, dict) or "pull_request" in raw_issue:
                    continue
                body = raw_issue.get("body")
                if not isinstance(body, str) or marker not in body:
                    continue
                issue = self._parse_issue(raw_issue, destination)
                matches.append(issue)
            if len(data) < 100:
                break
            page += 1
        return tuple(matches)

    def get_issue(self, destination_repository: str, issue_number: int) -> GitHubIssue:
        destination = normalize_destination(destination_repository)
        owner, repository = destination.split("/", 1)
        data = self._request(
            "GET",
            f"/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(repository)}"
            f"/issues/{issue_number}",
        )
        if not isinstance(data, dict):
            raise GitHubResponseError("GitHub issue response was not a JSON object")
        return self._parse_issue(data, destination)

    def create_issue(self, destination_repository: str, payload: IssuePayload) -> GitHubIssue:
        destination = normalize_destination(destination_repository)
        owner, repository = destination.split("/", 1)
        request_payload: dict[str, Any] = {"title": payload.title, "body": payload.body}
        if payload.labels:
            request_payload["labels"] = payload.labels
        data = self._request(
            "POST",
            f"/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(repository)}/issues",
            request_payload,
        )
        if not isinstance(data, dict):
            raise GitHubResponseError("GitHub create response was not a JSON object")
        return self._parse_issue(data, destination)

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
            "Authorization": f"Bearer {self._token}",
            "User-Agent": "client-feedback-triage",
        }
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload, separators=(",", ":")).encode()
        request = urllib.request.Request(
            f"{self._api_url}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            try:
                detail = error.read().decode(errors="replace")
            except OSError:
                detail = error.reason or "HTTP error"
            definitive = error.code < 500 and error.code not in {408, 429}
            raise GitHubApiError(
                f"GitHub returned HTTP {error.code}: {detail[:500]}",
                status_code=error.code,
                definitive=definitive,
            ) from error
        except (OSError, TimeoutError, urllib.error.URLError) as error:
            raise GitHubTransportError(f"GitHub request failed: {error}") from error
        try:
            return json.loads(raw)
        except (TypeError, json.JSONDecodeError) as error:
            raise GitHubResponseError("GitHub returned invalid JSON") from error

    @staticmethod
    def _parse_issue(raw_issue: dict[str, Any], requested_destination: str) -> GitHubIssue:
        number = raw_issue.get("number")
        title = raw_issue.get("title")
        body = raw_issue.get("body")
        html_url = raw_issue.get("html_url")
        state = raw_issue.get("state")
        if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
            raise GitHubResponseError("GitHub issue response has an invalid Issue number")
        if not isinstance(title, str) or not isinstance(body, str):
            raise GitHubResponseError("GitHub issue response has an invalid title or body")
        if not isinstance(html_url, str) or not html_url:
            raise GitHubResponseError("GitHub issue response has no Issue URL")
        if not isinstance(state, str) or not state:
            raise GitHubResponseError("GitHub issue response has no Issue state")
        destination = GitHubIssueClient._response_destination(raw_issue)
        if destination is None:
            raise GitHubResponseError("GitHub issue response has no repository identity")
        if destination != requested_destination:
            raise GitHubResponseError("GitHub issue response belongs to a different repository")
        return GitHubIssue(
            destination_repository=destination,
            number=number,
            title=title,
            body=body,
            html_url=html_url,
            state=state,
        )

    @staticmethod
    def _response_destination(raw_issue: dict[str, Any]) -> str | None:
        repository = raw_issue.get("repository")
        if isinstance(repository, dict) and isinstance(repository.get("full_name"), str):
            return GitHubIssueClient._normalize_response_destination(repository["full_name"])
        if isinstance(raw_issue.get("full_name"), str):
            return GitHubIssueClient._normalize_response_destination(raw_issue["full_name"])
        repository_url = raw_issue.get("repository_url")
        if isinstance(repository_url, str):
            parsed = urllib.parse.urlparse(repository_url)
            parts = parsed.path.strip("/").split("/")
            if len(parts) >= 3 and parts[0] == "repos":
                return GitHubIssueClient._normalize_response_destination(f"{parts[1]}/{parts[2]}")
        return None

    @staticmethod
    def _normalize_response_destination(value: str) -> str:
        try:
            return normalize_destination(value)
        except ValueError as error:
            raise GitHubResponseError("GitHub issue response has an invalid repository identity") from error
