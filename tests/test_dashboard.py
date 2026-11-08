from pathlib import Path

from streamlit.testing.v1 import AppTest


APP = Path(__file__).resolve().parents[1] / "visualization" / "app.py"
EXPECTED_TABS = {
    "Company & Agent Profile",
    "Overview & Fleet Health",
    "Live Agent Fleet",
    "Execution Inspector",
    "Policy Control Center",
    "Quarantine & HITL",
    "Observability & Benchmarks",
    "Company & Agent Setup",
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
    session_selector = next(widget for widget in app.selectbox if widget.key == "inspector_session::aster_finance")
    app = session_selector.select("FinOps-Worker-42").run()
    assert not app.exception, app.exception
    assert any(metric.label == "SHA-256 integrity digest" for metric in app.metric)

    app = next(widget for widget in app.selectbox if widget.key == "company_selector").select("Northstar Commerce").run()
    assert not app.exception, app.exception
    assert next(widget for widget in app.selectbox if widget.key == "selected_agent::northstar_commerce").value == "FinOps-Worker-42"
    assert "Commerce Payments Agent 42" in [header.value for header in app.header]
    buttons = {button.label: button for button in app.button}
    assert buttons["Push canary"].disabled
    app = buttons["Replay 500 historical sessions against draft"].click().run()
    assert not app.exception, app.exception
    assert not {button.label: button for button in app.button}["Push canary"].disabled


def test_company_and_agent_can_be_created_and_opened_in_the_profile(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP), default_timeout=45).run()
    next(widget for widget in app.text_input if widget.label == "Company name").set_value("Contoso Logistics")
    next(widget for widget in app.text_area if widget.label == "Company description").set_value("Logistics workflow demo")
    next(widget for widget in app.selectbox if widget.label == "Industry").select("Technology")
    app = next(button for button in app.button if button.label == "Create company").click().run()
    assert not app.exception, app.exception
    assert next(widget for widget in app.selectbox if widget.key == "company_selector").value == "Contoso Logistics"

    next(widget for widget in app.text_input if widget.label == "Agent display name").set_value("Fulfillment Agent")
    next(widget for widget in app.text_input if widget.label == "Agent role").set_value("fulfillment_operator")
    next(widget for widget in app.selectbox if widget.key == "new_agent_domain::contoso_logistics").select("Commerce Operations")
    app = next(button for button in app.button if button.label == "Add agent").click().run()
    assert not app.exception, app.exception
    assert next(widget for widget in app.selectbox if widget.key == "selected_agent::contoso_logistics").value == "Fulfillment-Agent"
    assert "Fulfillment Agent" in [header.value for header in app.header]
    profile = app.session_state["company_fleets"]["Contoso Logistics"].agent_profile("Fulfillment-Agent")
    assert profile["identity"]["domain"] == "Commerce Operations"
    assert profile["identity"]["user_role"] == "fulfillment_operator"
