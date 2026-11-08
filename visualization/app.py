"""Interactive AgentBridge workflow and benchmark dashboard.

Launch with: python -m streamlit run visualization/app.py
The project root is added to the import path automatically.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import streamlit as st

from core.models import RecoveryStrategy
from evaluation.metrics import summarize_results
from simulation.environment import SimulationConfig, run_simulation


STRATEGY_LABELS = {
    "FAIL_CLOSED": "Fail-Closed · stop on drift",
    "FAIL_OPEN": "Fail-Open · pin legacy policy",
    "HITL": "HITL · operator review",
    "COMPENSATING": "Compensating · constrain future spend",
}

st.set_page_config(page_title="AgentBridge Runtime Governance", page_icon="AB", layout="wide")
st.title("AgentBridge")
st.caption("Runtime policy-version consistency and compensating semantics for autonomous agent fleets")
st.markdown("A deterministic, in-memory event simulation. The dashboard does not call external tools or brokers.")

with st.sidebar:
    st.header("Simulation controls")
    strategy_value = st.selectbox("Recovery strategy", list(STRATEGY_LABELS), format_func=lambda value: STRATEGY_LABELS[value])
    seed = st.number_input("Random seed", min_value=0, max_value=2_147_483_647, value=20261108, step=1)
    node_count = st.slider("Governance edge nodes", min_value=1, max_value=20, value=10)
    inject_update = st.toggle("Inject v2 policy update before step 3", value=True)
    approval_probability = st.slider("HITL approval probability", 0.0, 1.0, 0.92, 0.01)
    run_clicked = st.button("Run workflow", type="primary", use_container_width=True)

if run_clicked:
    result = run_simulation(
        RecoveryStrategy(strategy_value),
        seed=int(seed),
        config=SimulationConfig(
            node_count=int(node_count),
            inject_policy_update=inject_update,
            hitl_approval_probability=approval_probability,
        ),
    )
    st.session_state["agentbridge_result"] = result
    st.session_state["agentbridge_strategy"] = strategy_value

result = st.session_state.get("agentbridge_result")
if result is None:
    st.info("Choose a recovery strategy and run a scenario. Each run uses a fresh event queue, node cache, agent passport, and seeded random generators.")
    st.markdown("**Scenario:** five-step corporate travel procurement; policy cap falls from $1,000 to $650 after the flight charge. The workflow contains irreversible booking and invite actions.")
    st.stop()

left, mid, right, extra = st.columns(4)
left.metric("Task completion", f"{result.completed * 100:.0f}%", f"{result.steps_completed}/5 steps")
mid.metric("Committed spend", f"${result.total_spend:,.2f}", f"cap ${result.policy_cap:,.2f}")
right.metric("Unauthorized spend", f"${result.unauthorized_spend:,.2f}", "updated-policy excess")
extra.metric("Recovery overhead", f"{result.recovery_latency_ms:,.1f} ms", f"interceptor {result.interceptor_overhead_ms:.2f} ms")

trace_frame = pd.DataFrame(result.trace)
st.subheader("Execution timeline")
if not trace_frame.empty:
    show_cols = [column for column in ["step_id", "tool", "action_type", "node_id", "sim_time_ms", "preflight_conflict", "commit_conflict", "decision", "executed"] if column in trace_frame]
    st.dataframe(trace_frame[show_cols], hide_index=True)
    step_options = [int(item["step_id"]) for item in result.trace]
    selected_step = st.slider("Inspect workflow step", min_value=min(step_options), max_value=max(step_options), value=min(step_options), step=1)
    detail = next(item for item in result.trace if int(item["step_id"]) == selected_step)
    detail_left, detail_right = st.columns([1, 1])
    with detail_left:
        st.markdown(f"**Step {selected_step}: `{detail['tool']}`**")
        st.write(detail.get("recovery_message") or detail.get("decision"))
        st.json(detail.get("arguments", {}), expanded=True)
    with detail_right:
        st.markdown("**Preflight governance envelope**")
        st.json(detail.get("envelope", {}), expanded=False)
        if detail.get("commit_envelope"):
            st.markdown("**Commit-boundary envelope**")
            st.json(detail["commit_envelope"], expanded=False)
else:
    st.warning("The trace is empty; inspect the simulation configuration.")

st.subheader("Distributed node topology")
node_rows = result.topology
nodes = "\n".join(
    f'  {item["node_id"]} [label="{item["node_id"]}\\n{item["policy_version"]}\\n{("pending " + ",".join(item["pending_updates"])) if item["pending_updates"] else "current"}", style=filled, fillcolor="{("#dbeafe" if item["policy_version"] == result.final_policy_version else "#fee2e2")}"];'
    for item in node_rows
)
dot = f'''digraph AgentBridge {{\n  rankdir=LR; bgcolor="transparent"; node [shape=box, style="rounded,filled", fontname="sans-serif", color="#334155"];\n  authority [label="Governance\\nAuthority\\n{result.final_policy_version}", fillcolor="#ccfbf1"];\n{nodes}\n  authority -> {{ {" ".join(item["node_id"] for item in node_rows)} }} [label="stochastic policy delivery", color="#64748b"];\n}}'''
st.graphviz_chart(dot)
st.dataframe(pd.DataFrame(node_rows), hide_index=True)

st.subheader("Monte Carlo benchmark results")
summary_path = ROOT / "results" / "benchmark_summary.csv"
if summary_path.exists():
    summary = pd.read_csv(summary_path)
    st.dataframe(summary, hide_index=True)
    chart_path = ROOT / "results" / "pareto_frontier.png"
    if chart_path.exists():
        st.image(str(chart_path), caption="Safety–utility comparison generated from benchmark_summary.csv")
else:
    st.caption("No benchmark artifacts found yet. Run `python -m evaluation.run_benchmarks --output-dir results` from the repository root.")

with st.expander("Simulation result payload"):
    st.json(result.to_dict(), expanded=False)
