import pandas as pd
import streamlit as st

from agent import monitor_watchlist, research_company
from batch import results_to_dataframe
from database import (
    company_exists,
    create_or_update_watchlist,
    get_all_research_runs,
    get_company_run_history,
    get_watchlist_by_company,
    get_watchlist_events,
    get_watchlists,
    init_db,
    save_seller_profile,
)
from limiter import check_rate_limit, increment_request_count, requests_remaining

init_db()

st.set_page_config(
    page_title="Sales Research Agent",
    page_icon="🔍",
    layout="wide",
)

st.title("🔍 Sales Research Agent")
st.caption("Commercial intelligence agent for ranked account research")


def _reset_context():
    for key in [
        "seller_id",
        "seller_profile",
        "batch_results",
        "selected_batch_company",
        "last_single_result",
        "watchlist_refresh_result",
        "selected_watchlist_company",
    ]:
        st.session_state.pop(key, None)


def _save_profile_from_form(
    seller_name: str,
    product_description: str,
    target_company_size: str,
    target_industries: str,
    ideal_customer_profile: str,
    past_wins: str,
    disqualifiers: str,
):
    profile = {
        "name": seller_name.strip(),
        "product_description": product_description.strip(),
        "target_company_size": target_company_size,
        "target_industries": target_industries.strip(),
        "ideal_customer_profile": ideal_customer_profile.strip(),
        "past_wins": past_wins.strip(),
        "disqualifiers": disqualifiers.strip(),
    }
    seller_id = st.session_state.get("seller_id")
    saved_id = save_seller_profile(profile, seller_id=seller_id)
    st.session_state.seller_id = saved_id
    st.session_state.seller_profile = profile


def render_brief(result: dict, key_prefix: str = "brief"):
    if result.get("error"):
        st.error(result["error"])

    company = result.get("company", "Unknown")
    confidence = (result.get("confidence") or "unknown").title()
    opportunity_score = result.get("opportunity_score", 0)
    trigger_score = result.get("trigger_score", 0)
    fit_score = result.get("fit_score", 0)
    timing_score = result.get("timing_score", 0)

    metrics = st.columns(5)
    metrics[0].metric("Company", company)
    metrics[1].metric("Opportunity", f"{opportunity_score}/10")
    metrics[2].metric("Trigger", f"{trigger_score}/10")
    metrics[3].metric("Fit", f"{fit_score}/10")
    metrics[4].metric("Timing", f"{timing_score}/10")

    if result.get("from_cache"):
        st.info("Showing a fresh cached result for this seller profile.")

    st.caption(f"Confidence: {confidence}")
    st.divider()

    left, right = st.columns(2)
    with left:
        st.subheader("What They Do")
        st.write(result.get("what_they_do") or "No summary available.")

        st.subheader("Key Customers")
        st.write(result.get("key_customers") or "No customer summary available.")

        st.subheader("Recent News")
        st.write(result.get("recent_news") or "No recent news summary available.")

        st.subheader("Pain Points")
        pain_points = result.get("pain_points") or []
        if pain_points:
            for pain in pain_points:
                st.write(f"- {pain}")
        else:
            st.write("No clear pain points identified.")

    with right:
        st.subheader("Why This Account Now")
        why_now_signals = result.get("why_now_signals") or []
        if why_now_signals:
            for signal in why_now_signals:
                st.write(f"- {signal}")
        else:
            st.write("No strong why-now triggers identified.")

        st.subheader("Outreach Angle")
        st.write(result.get("outreach_angle") or "No outreach angle available.")

        st.subheader("Score Rationale")
        st.write(result.get("score_rationale") or "No score rationale available.")

        st.subheader("Confidence Reason")
        st.write(result.get("confidence_reason") or "No confidence detail available.")

        st.subheader("Score Explanation")
        score_explanation = result.get("score_explanation") or []
        if score_explanation:
            for line in score_explanation:
                st.write(f"- {line}")
        else:
            st.write("No score explanation available.")

    with st.expander("Resolved Company"):
        st.json(result.get("resolved_company", {}))

    with st.expander("Resolution Trace"):
        resolved_company = result.get("resolved_company", {})
        st.json(resolved_company.get("resolution_trace", []))

    with st.expander("Sources"):
        sources = result.get("sources") or []
        if not sources:
            st.write("No sources captured.")
        for source in sources:
            title = source.get("title") or source.get("url")
            st.markdown(f"- [{title}]({source.get('url')}) via `{source.get('tool_name', 'unknown')}`")

    with st.expander("Score Breakdown"):
        st.json(
            {
                "opportunity_score": result.get("opportunity_score"),
                "fit_score": result.get("fit_score"),
                "timing_score": result.get("timing_score"),
                "evidence_score": result.get("evidence_score"),
                "trigger_score": result.get("trigger_score"),
                "confidence_score": result.get("confidence_score"),
            }
        )

    with st.expander("Account Snapshot"):
        st.json(result.get("account_snapshot", {}))

    with st.expander("Agent Trace"):
        st.write("Tool Trace")
        st.json(result.get("tool_trace", []))
        st.write("Decision Trace")
        st.json(result.get("decision_trace", []))

    seller_id = st.session_state.get("seller_id")
    if seller_id and company:
        existing_watchlist = get_watchlist_by_company(seller_id, company)
        button_label = "Already on Watchlist" if existing_watchlist else "Add to Watchlist"
        if st.button(button_label, key=f"{key_prefix}_watchlist_button", disabled=bool(existing_watchlist)):
            resolved_company = result.get("resolved_company") or {}
            watchlist_id = create_or_update_watchlist(
                seller_id=seller_id,
                company_name=company,
                notes=f"Added from {key_prefix} view",
            )
            st.success(f"Added {company} to watchlist (ID {watchlist_id}).")


remaining = requests_remaining()
if remaining == 0:
    st.error("Session limit reached. Refresh to start a new session.")
elif remaining <= 2:
    st.warning(f"⚠️ {remaining} fresh researches remaining this session")

if "seller_profile" not in st.session_state:
    st.divider()
    st.subheader("Define Seller Profile")
    st.caption("This profile scopes ranking, memory retrieval, and account recommendations.")
    with st.form("seller_profile_form"):
        seller_name = st.text_input("Your Name or Team (optional)", placeholder="e.g. Henry / Mid-Market AE")
        product_description = st.text_input(
            "What do you sell?",
            placeholder="e.g. AI sales automation software",
        )
        target_company_size = st.selectbox(
            "Target company size",
            ["Startup", "SMB", "Mid-Market", "Enterprise"],
        )
        target_industries = st.text_input(
            "Target industries",
            placeholder="e.g. fintech, healthcare, B2B SaaS",
        )
        ideal_customer_profile = st.text_area(
            "Ideal customer profile",
            placeholder="What traits make a company a strong fit?",
        )
        past_wins = st.text_area(
            "Past wins (optional)",
            placeholder="Examples of similar companies or proof points that tend to convert.",
        )
        disqualifiers = st.text_area(
            "Disqualifiers (optional)",
            placeholder="Signals that make an account a poor fit.",
        )
        submitted = st.form_submit_button("Start Researching", type="primary")

    if submitted:
        if not product_description.strip():
            st.error("Please tell the agent what you sell.")
        else:
            _save_profile_from_form(
                seller_name,
                product_description,
                target_company_size,
                target_industries,
                ideal_customer_profile,
                past_wins,
                disqualifiers,
            )
            st.rerun()

    st.stop()

profile = st.session_state["seller_profile"]
profile_label = profile["product_description"]
if profile.get("target_company_size"):
    profile_label += f" → {profile['target_company_size']}"
if profile.get("target_industries"):
    profile_label += f" · {profile['target_industries']}"

with st.expander(f"Seller Profile: {profile_label}"):
    st.write(f"**Name/Team:** {profile.get('name') or '—'}")
    st.write(f"**Product:** {profile.get('product_description')}")
    st.write(f"**Target Size:** {profile.get('target_company_size') or '—'}")
    st.write(f"**Target Industries:** {profile.get('target_industries') or '—'}")
    st.write(f"**ICP:** {profile.get('ideal_customer_profile') or '—'}")
    st.write(f"**Past Wins:** {profile.get('past_wins') or '—'}")
    st.write(f"**Disqualifiers:** {profile.get('disqualifiers') or '—'}")
    st.button("Reset seller profile", type="secondary", on_click=_reset_context)

tab1, tab2, tab3, tab4 = st.tabs(["Single Company", "Batch Upload", "Watchlists", "All Results"])

with tab1:
    st.subheader("Research a single company")
    company_name = st.text_input("Company Name", placeholder="e.g. Stripe, Datadog, Snowflake")
    force_refresh_single = st.checkbox("Force refresh", key="force_refresh_single")

    if st.button("Research", type="primary"):
        if not company_name.strip():
            st.warning("Please enter a company name.")
        else:
            cached_exists = company_exists(
                company_name,
                seller_id=st.session_state["seller_id"],
            )
            requires_fresh_run = force_refresh_single or not cached_exists
            if requires_fresh_run and not check_rate_limit():
                st.error("Session limit reached. Refresh to start a new session.")
            else:
                with st.spinner(f"Researching {company_name}..."):
                    result = research_company(
                        company_name=company_name,
                        seller_profile=st.session_state["seller_profile"],
                        seller_id=st.session_state["seller_id"],
                        force_refresh=force_refresh_single,
                    )
                if requires_fresh_run and not result.get("from_cache"):
                    increment_request_count()
                st.session_state.last_single_result = result

    if st.session_state.get("last_single_result"):
        render_brief(st.session_state["last_single_result"], key_prefix="single")

with tab2:
    st.subheader("Research multiple companies")
    st.caption("Upload a CSV with a `company` column.")
    force_refresh_batch = st.checkbox("Force refresh all batch accounts", key="force_refresh_batch")

    with st.expander("Example CSV"):
        st.code("company\nStripe\nNotion\nFigma\nSnowflake")

    uploaded_file = st.file_uploader("Upload CSV", type="csv")

    if uploaded_file:
        df_input = pd.read_csv(uploaded_file)
        if "company" not in df_input.columns:
            st.error("CSV must contain a `company` column.")
        else:
            companies = [str(company).strip() for company in df_input["company"].tolist() if str(company).strip()]
            st.success(f"Found {len(companies)} companies.")
            st.write(", ".join(companies))

            if st.button("Research All", type="primary"):
                results = []
                progress = st.progress(0)
                status = st.empty()

                for i, company in enumerate(companies):
                    cached_exists = company_exists(
                        company,
                        seller_id=st.session_state["seller_id"],
                    )
                    requires_fresh_run = force_refresh_batch or not cached_exists

                    if requires_fresh_run and not check_rate_limit():
                        status.warning(f"Session limit reached after {i} companies.")
                        break

                    status.text(f"Researching {company}... ({i + 1}/{len(companies)})")
                    result = research_company(
                        company_name=company,
                        seller_profile=st.session_state["seller_profile"],
                        seller_id=st.session_state["seller_id"],
                        force_refresh=force_refresh_batch,
                    )
                    if requires_fresh_run and not result.get("from_cache"):
                        increment_request_count()
                    results.append(result)
                    progress.progress((i + 1) / len(companies))

                status.success("Batch research complete.")
                st.session_state.batch_results = results
                st.session_state.selected_batch_company = None

    batch_results = st.session_state.get("batch_results")
    if batch_results:
        st.subheader("Ranked Opportunities")

        sorted_results = sorted(
            batch_results,
            key=lambda item: (
                item.get("opportunity_score", 0),
                item.get("trigger_score", 0),
            ),
            reverse=True,
        )

        header = st.columns([3, 1, 1, 1, 2])
        header[0].markdown("**Company**")
        header[1].markdown("**Opp.**")
        header[2].markdown("**Trigger**")
        header[3].markdown("**Confidence**")
        header[4].markdown("")

        for result in sorted_results:
            company = result.get("company", "")
            row = st.columns([3, 1, 1, 1, 2])
            row[0].write(company)
            row[1].write(f"{result.get('opportunity_score', 0)}/10")
            row[2].write(f"{result.get('trigger_score', 0)}/10")
            row[3].write((result.get("confidence") or "").title())
            if row[4].button("Inspect", key=f"inspect_{company}"):
                st.session_state.selected_batch_company = company

        df_results = results_to_dataframe(sorted_results)
        st.download_button(
            label="Download Batch Results as CSV",
            data=df_results.to_csv(index=False),
            file_name="research_results_v2.csv",
            mime="text/csv",
        )

        selected_company = st.session_state.get("selected_batch_company")
        if selected_company:
            selected_result = next(
                (item for item in sorted_results if item.get("company") == selected_company),
                None,
            )
            if selected_result:
                st.divider()
                st.subheader(f"Full Brief: {selected_company}")
                render_brief(selected_result, key_prefix=f"batch_{selected_company}")

with tab3:
    st.subheader("Watchlists")
    watchlists = get_watchlists(st.session_state["seller_id"])
    if not watchlists:
        st.info("No watched accounts yet. Add one from a company brief.")
    else:
        header = st.columns([3, 2, 2, 2])
        header[0].markdown("**Company**")
        header[1].markdown("**Domain**")
        header[2].markdown("**Last Checked**")
        header[3].markdown("")

        for watchlist in watchlists:
            company = watchlist.get("input_name")
            row = st.columns([3, 2, 2, 2])
            row[0].write(company)
            row[1].write(watchlist.get("domain") or "—")
            row[2].write(watchlist.get("last_checked_at") or "Never")
            if row[3].button("Refresh Signals", key=f"refresh_watchlist_{watchlist['id']}"):
                if not check_rate_limit():
                    st.error("Session limit reached. Refresh to start a new session.")
                else:
                    with st.spinner(f"Refreshing watchlist for {company}..."):
                        result = monitor_watchlist(
                            company_name=company,
                            seller_profile=st.session_state["seller_profile"],
                            seller_id=st.session_state["seller_id"],
                            watchlist_id=watchlist["id"],
                        )
                    increment_request_count()
                    st.session_state.watchlist_refresh_result = result
                    st.session_state.selected_watchlist_company = company

        refreshed = st.session_state.get("watchlist_refresh_result")
        selected_watchlist_company = st.session_state.get("selected_watchlist_company")
        if refreshed and selected_watchlist_company:
            st.divider()
            st.subheader(f"Watchlist Update: {selected_watchlist_company}")
            if refreshed.get("events"):
                st.write("Detected changes")
                for event in refreshed["events"]:
                    st.write(f"- **{event['title']}**: {event['summary']}")
            else:
                st.write("No major signal or score changes detected on the latest refresh.")

            for watchlist in watchlists:
                if watchlist.get("input_name") == selected_watchlist_company:
                    recent_events = get_watchlist_events(watchlist["id"])
                    run_history = get_company_run_history(
                        seller_id=st.session_state["seller_id"],
                        company_name=selected_watchlist_company,
                        limit=8,
                    )
                    if recent_events:
                        st.write("Recent watchlist events")
                        for event in recent_events:
                            st.write(f"- `{event['created_at']}` {event['title']}")
                    if run_history:
                        st.write("Score history")
                        history_df = pd.DataFrame(
                            [
                                {
                                    "Created At": item["created_at"],
                                    "Opportunity Score": item["opportunity_score"],
                                    "Trigger Score": item["trigger_score"],
                                    "Confidence": item["confidence"],
                                    "Signals": " | ".join(item["why_now_signals"]),
                                }
                                for item in run_history
                            ]
                        )
                        st.dataframe(history_df, use_container_width=True)
                        latest_snapshot = run_history[0].get("account_snapshot") or {}
                        if latest_snapshot:
                            st.write("Latest account snapshot")
                            st.json(latest_snapshot)
                    break

            render_brief(refreshed["brief"], key_prefix=f"watchlist_{selected_watchlist_company}")

with tab4:
    st.subheader("All Research Runs")
    all_runs = get_all_research_runs()
    if all_runs:
        df_runs = pd.DataFrame(all_runs)
        st.dataframe(df_runs, use_container_width=True)
        st.download_button(
            label="Download All Runs as CSV",
            data=df_runs.to_csv(index=False),
            file_name="all_research_runs.csv",
            mime="text/csv",
        )
    else:
        st.info("No research runs yet.")
