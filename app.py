import streamlit as st
from agent import research_company

st.set_page_config(
    page_title="Sales Research Agent",
    page_icon="🔍",
    layout="wide"
)

st.title("🔍 Sales Research Agent")
st.caption("Enter a company name to generate a real-time AI research brief")

company_name = st.text_input("Company Name", placeholder="e.g. Salesforce, Stripe, Notion")

if st.button("Research", type="primary"):
    if company_name:
        with st.spinner(f"Researching {company_name}..."):
            result = research_company(company_name)
        
        if "raw" in result:
            st.markdown(result["raw"])
        else:
            # Score at the top
            score = result.get("opportunity_score", 0)
            confidence = result.get("confidence", "unknown")
            
            col1, col2, col3 = st.columns(3)
            col1.metric("Opportunity Score", f"{score}/10")
            col2.metric("Confidence", confidence.title())
            col3.metric("Company", result.get("company"))
            
            st.divider()
            
            # Main content in two columns
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
    else:
        st.warning("Please enter a company name")
