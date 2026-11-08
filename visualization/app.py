"""Interactive AI governance mission-control dashboard.

Launch with ``python -m streamlit run visualization/app.py``. The UI bootstraps
its repository root and uses simulation-only, process-local demo fleet state.
"""
from __future__ import annotations

import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import streamlit as st

from core.models import GovernanceEnvelope, PolicyRule, RecoveryStrategy, RuleSeverity
from simulation.environment import SimulationConfig, SimulationResult, run_simulation
from simulation.mission_control import MissionControl


STRATEGY_LABELS = {
    "FAIL_CLOSED": "Fail-Closed · stop on drift",
    "FAIL_OPEN": "Fail-Open · pin legacy policy",
    "HITL": "HITL · operator review",
    "COMPENSATING": "Compensating · constrain future spend",
}


def money(value: Any) -> str:
    return f"${Decimal(str(value)):,.2f}"


def policy_rows(policy) -> list[dict[str, str]]:
    return [
        {
            "rule_id": rule.rule_id,
            "target_tool": rule.target_tool,
            "parameter": rule.parameter,
            "operator": rule.operator,
            "threshold": str(rule.threshold),
            "severity": rule.severity.value,
        }
        for _, rule in sorted(policy.rules.items())
    ]


def json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return value


st.set_page_config(page_title="AgentBridge Mission Control", page_icon="AB", layout="wide")
if "mission_control" not in st.session_state:
    st.session_state["mission_control"] = MissionControl()
if "latest_simulation" not in st.session_state:
    st.session_state["latest_simulation"] = run_simulation(RecoveryStrategy.COMPENSATING, seed=20261108)
mc: MissionControl = st.session_state["mission_control"]
latest: SimulationResult = st.session_state["latest_simulation"]

with st.sidebar:
    st.title("AgentBridge")
    environment = st.selectbox("Environment", ["Staging · simulated", "Production · simulated"], index=0)
    st.caption("No real agents, tools, customers, or approval systems are connected.")
    st.divider()
    st.subheader("Workflow replay")
    strategy_name = st.selectbox("Recovery strategy", list(STRATEGY_LABELS), format_func=lambda value: STRATEGY_LABELS[value])
    trial_seed = st.number_input("Seed", min_value=0, max_value=2_147_483_647, value=20261108, step=1)
    if st.button("Run 5-step scenario", type="primary", use_container_width=True):
        st.session_state["latest_simulation"] = run_simulation(
            RecoveryStrategy(strategy_name),
            seed=int(trial_seed),
            config=SimulationConfig(),
        )
        st.session_state["flash_notice"] = f"Scenario replayed with {STRATEGY_LABELS[strategy_name]} and seed {int(trial_seed)}."
        st.rerun()
    st.caption("The replay uses a fresh agent passport, node cache, event queue, and seeded RNG.")

st.title("AI Engineer Mission Control")
st.caption(f"Company AI Governance Portal · {environment} · deterministic in-memory simulation")
notice = st.session_state.pop("flash_notice", None)
if notice:
    st.success(notice)

overview = mc.overview()
if overview["attention_required"]:
    st.warning(f"Incident attention required: {overview['attention_required']} agent session(s) are in conflict or awaiting human review.")

overview_tab, fleet_tab, inspector_tab, policy_tab, intervention_tab, observability_tab = st.tabs([
    "Overview & Fleet Health",
    "Live Agent Fleet",
    "Execution Inspector",
    "Policy Control Center",
    "Quarantine & HITL",
    "Observability & Benchmarks",
])

with overview_tab:
    st.subheader("Fleet health")
    columns = st.columns(6)
    columns[0].metric("Online agents", overview["online_agents"])
    columns[1].metric("Idle agents", overview["idle_agents"])
    columns[2].metric("Halted agents", overview["halted_agents"])
    columns[3].metric("Cluster policy", overview["cluster_policy_version"])
    adoption = overview["policy_adoption"].get(overview["cluster_policy_version"], 0.0)
    columns[4].metric("Current-version adoption", f"{adoption:.0f}%", f"{overview['stale_nodes']} stale node(s)")
    columns[5].metric("Divergence incidents · 24h", overview["divergence_incidents_24h"])

    health_left, health_right = st.columns([1, 1])
    with health_left:
        st.markdown("#### Policy adoption across edge nodes")
        adoption_frame = pd.DataFrame([
            {"policy_version": version, "nodes": count}
            for version, count in sorted(overview["node_versions"].items())
        ])
        if not adoption_frame.empty:
            st.bar_chart(adoption_frame.set_index("policy_version"), height=220)
        st.caption("The canary rollout controls in Policy Control Center change this simulated cache distribution.")
    with health_right:
        st.markdown("#### Aggregate token spend")
        spend_ratio = min(1.0, overview["token_spend"] / overview["token_budget"])
        st.progress(spend_ratio, text=f"{overview['token_spend']:,} / {overview['token_budget']:,} modeled tokens")
        token_metrics = st.columns(2)
        token_metrics[0].metric("Budget remaining", f"{overview['token_budget'] - overview['token_spend']:,} tokens")
        token_metrics[1].metric("Tainted sessions", overview["tainted_agents"])
        st.caption("Token counters are synthetic demo telemetry, not measured model usage.")

    st.markdown("#### Fleet topology monitor")
    graph_nodes = []
    for node_id, state in sorted(mc.nodes.items()):
        color = "#dbeafe" if state["version"] == mc.cluster_policy.version else "#fde68a"
        pending = ", ".join(state["pending"]) or "no pending push"
        graph_nodes.append(f'  {node_id} [label="{node_id}\\n{state["version"]}\\n{pending}", fillcolor="{color}"];')
    graph = "\n".join(graph_nodes)
    dot = f'''digraph Fleet {{
      rankdir=LR; bgcolor="transparent"; node [shape=box, style="rounded,filled", fontname="sans-serif", color="#475569"];
      authority [label="Governance Authority\\n{mc.cluster_policy.version}", fillcolor="#ccfbf1"];
      agent_tier [label="Agent Fleet\\n{overview['total_agents']} simulated sessions", fillcolor="#e0e7ff"];
      authority -> {{{" ".join(mc.nodes)}}} [label="policy push · virtual latency"];
      agent_tier -> {{{" ".join(sorted({agent.node_id for agent in mc.agents.values()}))}}} [label="intercepted actions"];
    {graph}
    }}'''
    st.graphviz_chart(dot)

    st.markdown("#### Incident warning queue")
    if overview["attention_required"]:
        urgent = [row for row in mc.fleet_records() if row["status"] in {"CONFLICT_DETECTED", "PENDING_HUMAN_REVIEW"}]
        st.dataframe(pd.DataFrame(urgent)[["agent_name", "status", "consistency", "taint_status", "active_step"]], hide_index=True)
    else:
        st.success("No simulated conflict or pending-human-review sessions.")
    st.markdown("#### Recent simulated audit events")
    st.dataframe(pd.DataFrame(mc.audit_log[:8]), hide_index=True)

with fleet_tab:
    st.subheader("Live Agent Fleet Matrix")
    st.caption("Fleet records are seeded simulation fixtures. Status controls mutate only this browser session's in-memory demo state.")
    fleet = pd.DataFrame(mc.fleet_records())
    consistency_colors = {"IN-SYNC": "background-color: #dcfce7; color: #166534", "STALE-NODE": "background-color: #fef3c7; color: #92400e", "MID-FLIGHT-CONFLICT": "background-color: #fee2e2; color: #991b1b"}
    taint_colors = {"CLEAN": "background-color: #dcfce7; color: #166534", "TAINTED": "background-color: #fee2e2; color: #991b1b"}
    styled_fleet = fleet.style.map(lambda value: consistency_colors.get(value, ""), subset=["consistency"]).map(lambda value: taint_colors.get(value, ""), subset=["taint_status"])
    st.dataframe(styled_fleet, hide_index=True)
    selected_agent_id = st.selectbox("Inspect or control agent", list(mc.agents), format_func=lambda item: f"{mc.agents[item].agent_name} · {item}")
    control_columns = st.columns(3)
    if control_columns[0].button("Pause thread", key="fleet_pause"):
        mc.pause_agent(selected_agent_id)
        st.session_state["flash_notice"] = f"Paused {selected_agent_id}."
        st.rerun()
    if control_columns[1].button("Force update policy", key="fleet_force"):
        mc.force_update_agent(selected_agent_id)
        st.session_state["flash_notice"] = f"Forced {selected_agent_id}'s node cache to {mc.cluster_policy.version}."
        st.rerun()
    if control_columns[2].button("Terminate thread", key="fleet_terminate"):
        mc.terminate_agent(selected_agent_id)
        st.session_state["flash_notice"] = f"Terminated {selected_agent_id}."
        st.rerun()
    st.markdown("#### Context passport")
    st.json(mc.passport_for(selected_agent_id), expanded=True)
    selected_record = mc.agents[selected_agent_id]
    st.caption(f"Scope: {selected_record.domain} · role: {selected_record.user_role} · session: `{selected_record.session_id}`")

with inspector_tab:
    st.subheader("Execution Trace & Session Inspector")
    st.caption("Trace notes are concise operational summaries, not hidden chain-of-thought. All tool outputs are mock commits.")
    session_options = ["Latest 5-step workflow"] + list(mc.agents)
    selected_session = st.selectbox("Session", session_options, key="inspector_session")
    if selected_session == "Latest 5-step workflow":
        trace = latest.trace
        st.markdown(f"**Strategy:** `{latest.strategy}` · **Result:** {'completed' if latest.completed else 'halted'} · **Policy conflict:** `{latest.conflict_type}`")
        st.progress(min(1.0, latest.steps_completed / 5), text=f"Plan progress: {latest.steps_completed}/5 committed actions")
        if trace:
            trace_frame = pd.DataFrame(trace)
            display_cols = [column for column in ["step_id", "tool", "action_type", "node_id", "node_policy_version", "cluster_policy_version", "preflight_conflict", "commit_conflict", "decision", "executed"] if column in trace_frame]
            st.dataframe(trace_frame[display_cols], hide_index=True)
            steps = [int(item["step_id"]) for item in trace]
            inspect_step = st.select_slider("Trace step", options=steps, value=steps[0])
            item = next(row for row in trace if int(row["step_id"]) == inspect_step)
            detail_left, detail_right = st.columns(2)
            with detail_left:
                st.markdown(f"**Step {inspect_step}: `{item['tool']}`**")
                st.caption(f"Action classification: {item['action_type']}")
                st.write("**Plan note:** Proposed action passed through the runtime interceptor before its mock endpoint decision.")
                st.write(f"**Governance verdict:** `{item['decision']}`")
                st.write(f"**Tool output:** `{item.get('receipt', {}).get('status', 'NOT COMMITTED')}`")
                st.json(item.get("arguments", {}), expanded=True)
                st.markdown("**Policy rule(s) tripped**")
                if item.get("policy_violations"):
                    st.json(item["policy_violations"], expanded=True)
                else:
                    st.caption("No rule violations reported for this action.")
            with detail_right:
                st.markdown("**Context passport**")
                st.json(item.get("envelope", {}).get("passport", {}), expanded=False)
                st.markdown("**Governance envelope**")
                raw_envelope = item.get("envelope", {})
                try:
                    parsed = GovernanceEnvelope.model_validate(raw_envelope)
                    signature_valid = parsed.verify_signature()
                except Exception as exc:
                    signature_valid = False
                    st.error(f"Envelope validation failed: {exc}")
                st.metric("SHA-256 integrity digest", "VALID" if signature_valid else "INVALID")
                st.json(raw_envelope, expanded=False)
                if item.get("commit_envelope"):
                    st.markdown("**Commit-boundary envelope**")
                    st.json(item["commit_envelope"], expanded=False)
        else:
            st.info("No trace events in this run.")
    else:
        st.markdown(f"**Selected session:** `{selected_session}`")
        st.json(mc.passport_for(selected_session), expanded=True)
        agent_record = mc.agents[selected_session]
        st.write(f"**Current status:** `{agent_record.status}` · **Domain:** {agent_record.domain} · **Active plan step:** {agent_record.active_step}")
        agent_trace = mc.session_trace(selected_session)
        completed = sum(item["executed"] for item in agent_trace)
        st.progress(completed / len(agent_trace), text=f"Plan progression: {completed}/{len(agent_trace)} mock actions committed")
        st.dataframe(pd.DataFrame(agent_trace)[["step_id", "tool", "action_type", "decision", "conflict_type", "executed"]], hide_index=True)
        agent_step = st.select_slider("Agent trace step", options=[item["step_id"] for item in agent_trace], value=1, key="agent_trace_step")
        agent_item = next(item for item in agent_trace if item["step_id"] == agent_step)
        detail_left, detail_right = st.columns(2)
        with detail_left:
            st.caption(f"Action classification: {agent_item['action_type']}")
            st.write(f"**Plan note:** {agent_item['plan_note']}")
            st.write(f"**Governance verdict:** `{agent_item['decision']}`")
            st.write(f"**Tool output:** {agent_item['tool_output']}")
            st.json(agent_item["arguments"], expanded=True)
            st.markdown("**Policy rule(s) tripped**")
            st.json(agent_item["policy_violations"] or {"result": "No rule violations reported for this action."}, expanded=True)
        with detail_right:
            st.markdown("**Context passport**")
            st.json(agent_item["envelope"]["passport"], expanded=False)
            st.markdown("**Governance envelope**")
            try:
                parsed = GovernanceEnvelope.model_validate(agent_item["envelope"])
                signature_valid = parsed.verify_signature()
            except Exception as exc:
                signature_valid = False
                st.error(f"Envelope validation failed: {exc}")
            st.metric("SHA-256 integrity digest", "VALID" if signature_valid else "INVALID")
            st.json(agent_item["envelope"], expanded=False)

    st.markdown("#### Prompt-to-policy compiler mutation history")
    st.caption("Illustrative audit samples only; the simulator does not call an LLM or compile live prompts.")
    st.dataframe(pd.DataFrame(mc.prompt_mutations), hide_index=True)

with policy_tab:
    st.subheader("Policy Version Control & Deployment Center")
    st.caption("Rule edits, shadow replay, and canary pushes alter only the in-memory demo fleet.")
    st.markdown(f"**Active cluster head:** `{mc.cluster_policy.version}` · hash `{mc.cluster_policy.policy_hash}`")
    st.dataframe(pd.DataFrame(policy_rows(mc.cluster_policy)), hide_index=True)

    with st.expander("Rule Builder & Schema Editor", expanded=True):
        with st.form("rule_builder"):
            col_a, col_b, col_c = st.columns(3)
            rule_id = col_a.text_input("Rule ID", value=f"R{len(mc.draft_rules) + 1}", key=f"rule_id_{len(mc.draft_rules)}")
            target_tool = col_b.text_input("Target tool filter", value="*", help="Use '*' for all tools or a specific tool name.")
            parameter_kind = col_c.selectbox("Constraint parameter", ["aggregate_budget", "allow_pii_export", "custom"])
            custom_parameter = st.text_input("Custom parameter (if selected)", value="record_limit")
            col_d, col_e, col_f = st.columns(3)
            operator = col_d.selectbox("Operator", ["<=", "==", "!=", ">=", "<", ">", "in", "not_in"])
            threshold_input = col_e.text_input("Threshold / value", value="350.00", help="Numeric values use decimal arithmetic; booleans accept true/false.")
            severity_name = col_f.selectbox("Severity", [item.value for item in RuleSeverity], index=2)
            add_rule = st.form_submit_button("Validate and add / replace rule")
        if add_rule:
            parameter = custom_parameter.strip() if parameter_kind == "custom" else parameter_kind
            raw = threshold_input.strip()
            try:
                if parameter == "allow_pii_export" and raw.lower() in {"true", "false"}:
                    threshold: Any = raw.lower() == "true"
                elif operator in {"in", "not_in"}:
                    threshold = [value.strip() for value in raw.split(",") if value.strip()]
                else:
                    threshold = Decimal(raw)
                new_rule = PolicyRule(
                    rule_id=rule_id.strip(), target_tool=target_tool.strip(), parameter=parameter,
                    operator=operator, threshold=threshold, severity=RuleSeverity(severity_name),
                )
                mc.draft_rules = [item for item in mc.draft_rules if item.rule_id != new_rule.rule_id] + [new_rule]
                mc._audit("Draft policy rule edited", "Policy Editor", f"{new_rule.rule_id}: {new_rule.target_tool}.{new_rule.parameter} {new_rule.operator} {new_rule.threshold}")
                st.success(f"Validated rule {new_rule.rule_id}.")
            except (ValueError, InvalidOperation) as exc:
                st.error(f"Rule was not accepted: {exc}")

    draft_left, draft_right = st.columns([1, 2])
    with draft_left:
        draft_version = st.text_input("Draft policy version", value="2.1.0", key="draft_policy_version")
        draft_domain = st.text_input("Domain", value="company", key="draft_policy_domain")
        canary_percent = st.slider("Canary rollout target", min_value=10, max_value=100, value=10, step=10)
        draft_policy = None
        try:
            draft_policy = mc.build_policy(draft_version, draft_domain, mc.draft_rules)
            st.success(f"Draft valid · SHA-256 `{draft_policy.policy_hash}`")
        except Exception as exc:
            st.warning(f"Draft needs attention: {exc}")
        prior_shadow = st.session_state.get("last_shadow_result")
        shadow_matches_draft = bool(draft_policy and prior_shadow and prior_shadow.get("policy_hash") == draft_policy.policy_hash and prior_shadow.get("sample_size") == 500)
        if not shadow_matches_draft:
            st.info("Run a 500-session shadow replay for this exact policy hash before deployment; canary buttons remain disabled until it passes.")
        push_col, full_col, advance_col = st.columns(3)
        if push_col.button("Push canary", disabled=draft_policy is None or not shadow_matches_draft, key="push_canary"):
            try:
                rollout = mc.canary_rollout(draft_policy, canary_percent)
                st.success(f"Scheduled {rollout['scheduled_count']} delivery event(s) for {rollout['version']}; {rollout['pending_count']} pending.")
            except Exception as exc:
                st.error(f"Canary push failed: {exc}")
        if full_col.button("Roll out to 100%", disabled=draft_policy is None or not shadow_matches_draft, key="push_full"):
            try:
                rollout = mc.canary_rollout(draft_policy, 100)
                st.success(f"Scheduled {rollout['scheduled_count']} additional node delivery event(s); {rollout['pending_count']} pending.")
            except Exception as exc:
                st.error(f"Rollout failed: {exc}")
        current_rollout = mc.rollout_status()
        if advance_col.button("Advance +200 ms", disabled=not current_rollout or current_rollout["pending_count"] == 0, key="advance_propagation"):
            advanced = mc.advance_propagation(200.0)
            st.success(f"Virtual clock: {advanced['virtual_time_ms']:.0f} ms · delivered {len(advanced['delivered'])} update(s).")
        rollout_status = mc.rollout_status()
        if rollout_status:
            st.progress(rollout_status["progress_percent"] / 100, text=f"{rollout_status['version']} adoption: {rollout_status['updated_count']}/{rollout_status['total_nodes']} nodes")
            st.caption(f"Virtual time {rollout_status['virtual_time_ms']:.0f} ms · {rollout_status['pending_count']} pending event(s). Sampled delivery delays (ms): " + (", ".join(f"{value:.1f}" for value in rollout_status["delays_ms"].values()) or "no remaining nodes"))
    with draft_right:
        st.markdown("#### Proposed rules")
        st.dataframe(pd.DataFrame(policy_rows(draft_policy)) if draft_policy else pd.DataFrame(policy_rows(mc.cluster_policy)), hide_index=True)
        if draft_policy:
            st.markdown("#### Version diff")
            st.dataframe(pd.DataFrame(mc.policy_diff(draft_policy)), hide_index=True)

    st.markdown("#### Pre-deployment shadow simulator · last 500 demo sessions")
    if st.button("Replay 500 historical sessions against draft", disabled=draft_policy is None, key="shadow_replay"):
        st.session_state["last_shadow_result"] = mc.shadow_replay(draft_policy, 500)
        st.rerun()
    shadow = st.session_state.get("last_shadow_result")
    if shadow:
        if draft_policy and shadow.get("policy_hash") == draft_policy.policy_hash:
            shadow_cols = st.columns(4)
            shadow_cols[0].metric("Sampled sessions", shadow["sample_size"])
            shadow_cols[1].metric("Predicted halts", shadow["predicted_halts"], f"{shadow['halt_rate_percent']:.1f}%")
            shadow_cols[2].metric("Compensation candidates", shadow["compensation_candidates"])
            shadow_cols[3].metric("Already over cap", shadow["already_over_cap_irreversible"])
            st.dataframe(pd.DataFrame([{"rule_id": key, "violating_sessions": value} for key, value in shadow["violations_by_rule"].items()]), hide_index=True)
            if shadow.get("unsupported_rules"):
                st.warning("Replay lacks historical fields for these custom rules; results are incomplete: " + ", ".join(shadow["unsupported_rules"]))
            st.caption(shadow["history_type"] + ". This deterministic fixture is not a production session archive.")
        else:
            st.warning("The saved replay is for a different policy hash. Re-run shadow simulation before deployment.")
    else:
        st.info("Run the shadow replay before pushing to inspect predicted halt and compensation counts.")

with intervention_tab:
    st.subheader("Irreversible Actions & Human Intervention Queue")
    st.caption("These decisions mutate only synthetic request records in this browser session; no payment, email, or API action is sent.")
    requests = mc.hitl_queue()
    pending = [request for request in requests if request["status"] == "PENDING"]
    st.dataframe(pd.DataFrame([{key: str(value) if isinstance(value, Decimal) else value for key, value in request.items() if key not in {"decision_note"}} for request in requests]), hide_index=True)
    if pending:
        request_id = st.selectbox("Escalation inbox", [item["request_id"] for item in pending], format_func=lambda item: f"{item} · {next(row['agent_id'] for row in pending if row['request_id'] == item)}")
        request = next(item for item in pending if item["request_id"] == request_id)
        st.error(
            f"{request['agent_id']} spent {money(request['spent'])} under {request['session_version']} (limit {money(request['old_cap'])}). "
            f"New {request['cluster_version']} limit is {money(request['new_cap'])}. Proposed {request['tool']} action: {money(request['proposed_cost'])} ({request['purpose']})."
        )
        st.caption(f"Action classification: {request['action_type']}")
        action_cols = st.columns(3)
        if action_cols[0].button("Override & Approve", key=f"override_{request_id}"):
            decision = mc.decide_hitl(request_id, "override")
            st.session_state["flash_notice"] = decision["decision_note"]
            st.rerun()
        if action_cols[1].button("Apply Compensating Clamp", key=f"compensate_{request_id}"):
            decision = mc.decide_hitl(request_id, "compensate")
            st.session_state["flash_notice"] = decision["decision_note"]
            st.rerun()
        if action_cols[2].button("Abort & Freeze", key=f"abort_{request_id}"):
            decision = mc.decide_hitl(request_id, "abort")
            st.session_state["flash_notice"] = decision["decision_note"]
            st.rerun()
    else:
        st.success("No pending intervention requests in this simulated queue.")

    st.markdown("#### Quarantine queue")
    quarantined = [row for row in mc.fleet_records() if row["status"] in {"CONFLICT_DETECTED", "HALTED", "PENDING_HUMAN_REVIEW"} or row["consistency"] != "IN-SYNC"]
    st.dataframe(pd.DataFrame(quarantined), hide_index=True)
    st.markdown("#### Latest intervention and audit decisions")
    st.dataframe(pd.DataFrame(mc.audit_log[:10]), hide_index=True)

with observability_tab:
    st.subheader("Observability, Latency & Benchmark Metrics")
    summary_path = ROOT / "results" / "benchmark_summary.csv"
    raw_path = ROOT / "results" / "benchmark_raw_results.csv"
    if summary_path.exists():
        summary = pd.read_csv(summary_path)
        st.markdown("#### Safety vs. utility")
        chart_left, chart_right = st.columns([1, 1])
        with chart_left:
            st.scatter_chart(summary, x="Task_Completion_Rate", y="Governance_Violation_Rate", color="strategy", size="Trials")
            st.caption("Increase task completion (right); reduce violations (down). The saved Pareto figure uses 100 − GVR as the upward safety axis.")
        with chart_right:
            chart_path = ROOT / "results" / "pareto_frontier.png"
            if chart_path.exists():
                st.image(str(chart_path), caption="Generated from the seeded benchmark summary CSV")
        st.dataframe(summary[["strategy", "Trials", "Task_Completion_Rate", "Governance_Violation_Rate", "Mean_Unauthorized_Cost", "Mean_Recovery_Latency_ms"]], hide_index=True)
        st.markdown("#### Recovery and edge proxy latency")
        latency_frame = summary[["strategy", "Mean_Recovery_Latency_ms"]].set_index("strategy")
        st.bar_chart(latency_frame, height=220)
        latency_samples = mc.latency_samples(250)
        proxy_values = pd.Series(latency_samples["automated_proxy_ms"])
        human_values = pd.Series(latency_samples["human_escalation_ms"])
        latency_cols = st.columns(2)
        with latency_cols[0]:
            st.metric("Automated proxy p50 / p95", f"{proxy_values.quantile(0.50):.2f} ms", f"p95 {proxy_values.quantile(0.95):.2f} ms · target <3 ms")
            proxy_hist = pd.cut(proxy_values, bins=[0, 0.15, 0.25, 0.35, 0.45, 0.55, 0.7, 1.0, 3.0], right=False).value_counts(sort=False).rename("requests").to_frame()
            proxy_hist.index = proxy_hist.index.map(str)
            st.bar_chart(proxy_hist, height=180)
        with latency_cols[1]:
            st.metric("Human escalation p50 / p95", f"{human_values.quantile(0.50):.0f} ms", f"p95 {human_values.quantile(0.95):.0f} ms")
            human_hist = pd.cut(human_values, bins=[0, 250, 300, 350, 400, 450, 500, 550, 600, 750], right=False).value_counts(sort=False).rename("requests").to_frame()
            human_hist.index = human_hist.index.map(str)
            st.bar_chart(human_hist, height=180)
        st.caption("Latency distributions are seeded models for the demo, not production observations; the benchmark recovery chart includes the modeled HITL penalty.")
        if raw_path.exists():
            raw = pd.read_csv(raw_path)
            st.caption(f"Benchmark archive: {len(raw):,} trial rows across {raw['strategy'].nunique()} strategies.")
    else:
        st.warning("Benchmark CSVs are missing. Run `python -m evaluation.run_benchmarks --output-dir results` from the repository root.")

    st.markdown("#### Policy propagation latency distribution")
    propagation = pd.DataFrame({"delay_ms": mc.propagation_samples(100)})
    propagation["bucket_ms"] = pd.cut(propagation["delay_ms"], bins=[0, 50, 100, 150, 200, 250, 300, 500, float("inf")], right=False)
    histogram = propagation.groupby("bucket_ms", observed=False).size().rename("deliveries").to_frame()
    histogram.index = histogram.index.map(str)
    st.bar_chart(histogram, height=220)
    st.caption("Delay model: max(10 ms, Gaussian(180 ms, 40 ms)); samples are seeded demo values.")

    st.markdown("#### Token economy tracker")
    economy = mc.token_economy()
    token_cols = st.columns(3)
    token_cols[0].metric("Estimated tokens saved", f"{economy['estimated_tokens_saved']:,}")
    token_cols[1].metric("Raw exception-dump baseline", f"{economy['raw_exception_tokens_per_case']:,} / case")
    token_cols[2].metric("Concise policy repair", f"{economy['policy_repair_tokens_per_case']:,} / case")
    st.caption(f"Estimated savings = {economy['resolved_interventions']} resolved cases × {economy['estimated_saved_per_case']:,} tokens. {economy['status']}.")

    with st.expander("Raw latest workflow result"):
        st.json(json_safe(latest.to_dict()), expanded=False)
