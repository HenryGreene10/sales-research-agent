import streamlit as st
import pandas as pd
import io
from agent import research_company
from database import init_db, save_research, company_exists, get_all_companies
from batch import process_batch, results_to_dataframe
from limiter import check_rate_limit, increment_request_count, requests_remaining

# Initialize database
init_db()

st.set_page_config(
    page_title="Sales Research Agent",
    page_icon="🔍",
    layout="wide"
)

st.title("🔍 Sales Research Agent")
st.caption("AI-powered company intelligence for sales teams")

remaining = requests_remaining()
if remaining == 0:
    st.error("Session limit reached. Refresh to start a new session.")
elif remaining <= 2:
    st.warning(f"⚠️ {remaining} free researches remaining this session")

# ── CONTEXT GATE ───────────────────────────────────────────────
# Show setup form until the user has defined their selling context.
if "user_context" not in st.session_state:
    st.divider()
    st.subheader("Before we start — who are you selling to?")
    st.caption("This lets the agent score and frame every brief from your specific perspective.")
    with st.form("context_form"):
        what_you_sell = st.text_input("What do you sell?", placeholder="e.g. sales automation software")
        target_size = st.selectbox(
            "Target company size",
            ["Startup", "SMB", "Mid-Market", "Enterprise"]
        )
        target_industry = st.text_input("Target industry (optional)", placeholder="e.g. fintech, healthcare")
        submitted = st.form_submit_button("Start Researching", type="primary")

    if submitted:
        if not what_you_sell.strip():
            st.error("Please tell us what you sell.")
        else:
            st.session_state.user_context = {
                "what_you_sell": what_you_sell.strip(),
                "target_size": target_size,
                "target_industry": target_industry.strip(),
            }
            st.rerun()

    st.stop()  # don't render tabs until context is saved

# Show current context with option to reset
def _reset_context():
    for key in ["user_context", "batch_results", "selected_batch_company"]:
        st.session_state.pop(key, None)

ctx = st.session_state.user_context
ctx_label = f"{ctx['what_you_sell']} → {ctx['target_size']}"
if ctx.get("target_industry"):
    ctx_label += f" · {ctx['target_industry']}"
with st.expander(f"Selling context: {ctx_label}"):
    st.write(f"**What you sell:** {ctx['what_you_sell']}")
    st.write(f"**Target size:** {ctx['target_size']}")
    st.write(f"**Target industry:** {ctx.get('target_industry') or '—'}")
    st.button("Reset context", type="secondary", on_click=_reset_context)


def render_brief(result: dict):
    if "raw" in result:
        st.markdown(result["raw"])
        return

    score = result.get("opportunity_score", 0)
    confidence = result.get("confidence", "unknown")

    col1, col2, col3 = st.columns(3)
    col1.metric("Opportunity Score", f"{score}/10")
    col2.metric("Confidence", confidence.title())
    col3.metric("Company", result.get("company"))

    st.divider()

    left, right = st.columns(2)

    with left:
        st.subheader("🏢 What They Do")
        st.write(result.get("what_they_do"))

        st.subheader("👥 Key Customers")
        st.write(result.get("key_customers"))

        st.subheader("📰 Recent News")
        st.write(result.get("recent_news"))

    with right:
        st.subheader("⚠️ Pain Point")
        st.write(result.get("pain_point"))

        st.subheader("🎯 Outreach Angle")
        st.write(result.get("outreach_angle"))

        st.subheader("📊 Confidence Reasoning")
        st.write(result.get("confidence_reason"))


# Tabs for single vs batch
tab1, tab2, tab3 = st.tabs(["Single Company", "Batch Upload", "All Results"])

# ── TAB 1: Single Company ──────────────────────────────────────
with tab1:
    st.subheader("Research a single company")
    company_name = st.text_input("Company Name", placeholder="e.g. Salesforce, Stripe, Notion")

    if st.button("Research", type="primary"):
        if company_name:
            if not check_rate_limit():
                st.error("Session limit reached. Refresh to start a new session.")
            else:
                with st.spinner(f"Researching {company_name}..."):
                    result = research_company(company_name, user_context=st.session_state.user_context)
                    save_research(result)
                    increment_request_count()

                render_brief(result)
        else:
            st.warning("Please enter a company name")

# ── TAB 2: Batch Upload ────────────────────────────────────────
with tab2:
    st.subheader("Research multiple companies at once")
    st.caption("Upload a CSV with a column called 'company'")

    with st.expander("See example CSV format"):
        st.code("company\nStripe\nNotion\nFigma\nSnowflake")

    uploaded_file = st.file_uploader("Upload CSV", type="csv")

    if uploaded_file:
        df_input = pd.read_csv(uploaded_file)

        if "company" not in df_input.columns:
            st.error("CSV must have a column called 'company'")
        else:
            companies = df_input["company"].tolist()
            st.success(f"Found {len(companies)} companies: {', '.join(companies)}")

            if st.button("Research All", type="primary"):
                if not check_rate_limit():
                    st.error("Session limit reached. Refresh to start a new session.")
                else:
                    results = []
                    progress = st.progress(0)
                    status = st.empty()

                    for i, company in enumerate(companies):
                        status.text(f"Researching {company}... ({i+1}/{len(companies)})")

                        if company_exists(company):
                            status.text(f"⚡ {company} already researched today, skipping...")
                        elif not check_rate_limit():
                            status.text(f"⚠️ Session limit reached. Stopping after {i} companies.")
                            break
                        else:
                            result = research_company(company, user_context=st.session_state.user_context)
                            save_research(result)
                            results.append(result)
                            increment_request_count()

                        progress.progress((i + 1) / len(companies))

                    status.text("✅ Done!")
                    st.session_state.batch_results = results
                    st.session_state.selected_batch_company = None

    # Results table — rendered from session state so it persists across button reruns
    batch_results = st.session_state.get("batch_results")
    if batch_results:
        st.subheader("📊 Results — Ranked by Opportunity Score")

        # Column header row
        h = st.columns([3, 1, 2, 2])
        h[0].markdown("**Company**")
        h[1].markdown("**Score**")
        h[2].markdown("**Confidence**")
        h[3].markdown("")

        sorted_results = sorted(
            batch_results,
            key=lambda x: x.get("opportunity_score", 0),
            reverse=True
        )
        for res in sorted_results:
            company = res.get("company", "")
            row = st.columns([3, 1, 2, 2])
            row[0].write(company)
            row[1].write(f"{res.get('opportunity_score', 0)}/10")
            row[2].write((res.get("confidence") or "").title())
            if row[3].button("View Full Brief", key=f"brief_{company}"):
                st.session_state.selected_batch_company = company

        df_results = results_to_dataframe(batch_results)
        csv = df_results.to_csv(index=False)
        st.download_button(
            label="📥 Download Results as CSV",
            data=csv,
            file_name="research_results.csv",
            mime="text/csv"
        )

        # Expanded brief below the table
        selected = st.session_state.get("selected_batch_company")
        if selected:
            brief = next((r for r in batch_results if r.get("company") == selected), None)
            if brief:
                st.divider()
                st.subheader(f"📋 Full Brief: {selected}")
                render_brief(brief)

# ── TAB 3: All Results ─────────────────────────────────────────
with tab3:
    st.subheader("All researched companies")

    rows = get_all_companies()

    if rows:
        df_all = pd.DataFrame(rows, columns=[
            "Company", "Score", "Confidence",
            "Pain Point", "Outreach Angle", "Researched At"
        ])
        df_all = df_all.sort_values("Score", ascending=False)
        st.dataframe(df_all, use_container_width=True)

        # Export all
        csv = df_all.to_csv(index=False)
        st.download_button(
            label="📥 Download All as CSV",
            data=csv,
            file_name="all_research.csv",
            mime="text/csv"
        )
    else:
        st.info("No companies researched yet. Use the Single or Batch tab to get started.")
