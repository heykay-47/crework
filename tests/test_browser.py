from __future__ import annotations

import socket
import time
from collections.abc import Iterator
from pathlib import Path
from threading import Thread
from typing import cast

import httpx
import pytest
import uvicorn

from feedback_triage import web
from feedback_triage.gemini_video import GeminiClient
from tests.test_web import BrowserGeminiGateway, FakeGateway, install_fake_analysis

try:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import expect, sync_playwright
except ModuleNotFoundError:
    pytest.skip("browser test skipped: install the dev dependencies to provide Playwright", allow_module_level=True)


@pytest.fixture
def running_web_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[str, FakeGateway, BrowserGeminiGateway]]:
    install_fake_analysis(monkeypatch, duplicate_span=True)
    gateway = FakeGateway()
    gemini = BrowserGeminiGateway()
    settings = web.WebSettings(output_root=tmp_path, github_repository="Demo/Feedback")
    app = web.create_app(settings, gemini_client=cast(GeminiClient, gemini), github_gateway=gateway)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = Thread(target=server.run, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                if httpx.get(f"{base_url}/api/health", timeout=0.2).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.05)
        else:
            raise AssertionError("local web server did not become healthy")
        yield base_url, gateway, gemini
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_real_browser_review_workflow(
    running_web_app: tuple[str, FakeGateway, BrowserGeminiGateway], tmp_path: Path
) -> None:
    base_url, gateway, gemini = running_web_app
    source = tmp_path / "browser-workflow.mp4"
    source.write_bytes(Path("fixtures/canonical/feedback-recording.mp4").read_bytes())

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except PlaywrightError as error:
            pytest.skip(f"browser test skipped: Chromium executable unavailable ({error})")
        with browser:
            page = browser.new_page()
            page.goto(base_url, wait_until="domcontentloaded")
            expect(page.locator("#config-badge")).to_contain_text("Destination")

            page.locator("#video-file").set_input_files(str(source))
            expect(page.locator("#upload-button")).to_be_enabled()
            expect(page.locator("#global-announcement")).to_contain_text("Selected browser-workflow.mp4")
            page.locator("#upload-button").press("Enter")
            expect(page.locator("#global-announcement")).to_contain_text("Recording validated")
            expect(page.locator("#analysis-panel")).to_be_visible()
            page.locator("#analyze-button").press("Enter")
            expect(page.locator("#global-announcement")).to_have_text("Analysis started.")
            expect(page.locator("#review-panel")).to_be_visible(timeout=15_000)
            expect(page.locator("#recording-status")).to_have_text("ready for review", timeout=15_000)

            expect(page.locator("#route-groups .route-group")).to_have_count(4)
            expect(page.locator("#routed-result-count")).to_have_text("4 routed results")
            assert page.locator("#route-groups").inner_text().count("Candidate") >= 1
            assert "Clarification Request" in page.locator("#route-groups").inner_text()
            assert "Manual Review" in page.locator("#route-groups").inner_text()
            assert "Withheld Result" in page.locator("#route-groups").inner_text()

            markers = page.locator("#timeline-track .timeline-marker")
            expect(markers).to_have_count(5)
            candidate_marker = page.locator(".timeline-marker.candidate").first
            expect(candidate_marker).to_have_attribute("data-seconds", "1.0")
            assert page.locator(".timeline-marker.candidate").nth(0).get_attribute("data-seconds") == page.locator(
                ".timeline-marker.candidate"
            ).nth(1).get_attribute("data-seconds")
            fallback = page.locator(".timeline-marker.manual_review.frame-fallback")
            expect(fallback).to_have_count(1)
            expect(fallback).to_have_attribute("data-seconds", "1.5")
            assert "Evidence Frame" in (fallback.get_attribute("aria-label") or "")

            video = page.locator("#review-video")
            page.wait_for_function("document.getElementById('review-video').readyState >= 1", timeout=5_000)
            video.focus()
            video.press("Space")
            page.wait_for_timeout(100)
            assert page.evaluate("!document.getElementById('review-video').paused")
            video.press("Space")
            page.wait_for_timeout(100)
            assert page.evaluate("document.getElementById('review-video').paused")

            page.evaluate(
                """
                () => {
                  const video = document.getElementById('review-video');
                  let current = 0;
                  Object.defineProperty(video, 'currentTime', {
                    configurable: true,
                    get: () => current,
                    set: (value) => { current = value; },
                  });
                }
                """
            )
            candidate_marker.click()
            assert page.evaluate("document.getElementById('review-video').currentTime") == 1
            candidate_id = page.locator(".queue-card.candidate").first.get_attribute("data-candidate-id")
            assert candidate_id is not None
            selected_card = page.locator(f'.queue-card[data-candidate-id="{candidate_id}"]')
            assert "is-selected" in (selected_card.get_attribute("class") or "")

            manual_card = page.locator(".queue-card.manual_review")
            candidate_marker.focus()
            candidate_marker.press("Enter")
            fallback.focus()
            fallback.press("Enter")
            assert page.evaluate("document.getElementById('review-video').currentTime") == 1.5
            assert "is-selected" in (manual_card.get_attribute("class") or "")
            expect(page.locator("#detail-route")).to_have_text("Manual Review")
            expect(page.locator("#global-announcement")).to_contain_text("Selected Manual Review")
            frame_button = page.locator(".evidence-frame-button")
            expect(frame_button).to_have_count(1)
            frame_button.press("Enter")
            assert page.evaluate("document.getElementById('review-video').currentTime") == 1.5

            manual_title = page.locator("#edit-title").input_value()
            page.locator("#edit-title").press("Control+A")
            page.keyboard.type(f"  {manual_title}  ")
            page.locator("[data-action=preview]").press("Enter")
            expect(page.locator("#analysis-error")).to_contain_text("Manual Review requires explicit confirmation")
            expect(page.locator("#global-announcement")).to_contain_text("Manual Review requires explicit confirmation")

            page.locator("[data-action=decline]").press("Enter")
            expect(page.locator(".queue-card.manual_review")).to_contain_text("declined")
            expect(page.locator("#global-announcement")).to_contain_text("Decline saved locally")
            page.locator("#edit-title").press("Control+A")
            page.keyboard.type("Browser-confirmed Manual Review")
            page.locator("#manual-review-confirmed").focus()
            page.locator("#manual-review-confirmed").press("Space")
            page.locator("[data-action=preview]").press("Enter")
            expect(page.locator("#approval-dialog")).to_be_visible()
            page.locator("#confirm-approval").press("Enter")
            expect(page.locator(".queue-card.manual_review")).to_contain_text("approved")
            expect(page.locator("#global-announcement")).to_contain_text("Approval saved locally")

            page.locator(".queue-card.candidate").first.focus()
            page.locator(".queue-card.candidate").first.press("Enter")
            page.locator("[data-action=preview]").press("Enter")
            expect(page.locator("#approval-dialog")).to_be_visible()
            page.locator("#confirm-approval").press("Enter")
            expect(page.locator(".queue-card.candidate").first).to_contain_text("approved")

            page.on("dialog", lambda dialog: dialog.accept())
            page.locator("#publish-button").press("Enter")
            expect(page.locator("#recording-status")).to_have_text("published", timeout=15_000)
            expect(page.locator("#global-announcement")).to_contain_text("Publishing completed")
            assert gateway.create_calls == 2
            assert gemini.interactions.created_ids == ["browser-interaction-1"]
