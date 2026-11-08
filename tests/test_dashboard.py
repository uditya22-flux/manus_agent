from pathlib import Path

from streamlit.testing.v1 import AppTest


APP = Path(__file__).resolve().parents[1] / "visualization" / "app.py"
EXPECTED_TABS = {
    "Overview & Fleet Health",
    "Live Agent Fleet",
    "Execution Inspector",
    "Policy Control Center",
    "Quarantine & HITL",
    "Observability & Benchmarks",
}


def test_mission_control_dashboard_end_to_end_from_outside_repository(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP), default_timeout=45).run()
    assert not app.exception, app.exception
    assert {tab.label for tab in app.tabs} == EXPECTED_TABS

    buttons = {button.label: button for button in app.button}
    assert buttons["Push canary"].disabled
    app = buttons["Replay 500 historical sessions against draft"].click().run()
    assert not app.exception, app.exception
    buttons = {button.label: button for button in app.button}
    assert not buttons["Push canary"].disabled

    app = buttons["Push canary"].click().run()
    assert not app.exception, app.exception
    buttons = {button.label: button for button in app.button}
    assert not buttons["Advance +200 ms"].disabled
    app = buttons["Advance +200 ms"].click().run()
    assert not app.exception, app.exception

    app = next(button for button in app.button if button.label == "Apply Compensating Clamp").click().run()
    assert not app.exception, app.exception
    session_selector = next(widget for widget in app.selectbox if widget.key == "inspector_session")
    app = session_selector.select("FinOps-Worker-42").run()
    assert not app.exception, app.exception
    assert any(metric.label == "SHA-256 integrity digest" for metric in app.metric)
