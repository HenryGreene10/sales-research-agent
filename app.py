import streamlit as st
from agent import research_company

st.set_page_config(page_title="Sales Research Agent", page_icon="🔍")

st.title("🔍 Sales Research Agent")
st.subheader("Enter a company name to generate a real-time research brief")

company_name = st.text_input("Company Name", placeholder="e.g. Salesforce, Stripe, Notion")

if st.button("Research"):
    if company_name:
        with st.spinner("Searching the web and analyzing..."):
            result = research_company(company_name)
        st.markdown(result)
    else:
        st.warning("Please enter a company name")
