import streamlit as st

MAX_REQUESTS_PER_SESSION = 5

def check_rate_limit() -> bool:
    """Returns True if user is allowed to make a request"""
    if "request_count" not in st.session_state:
        st.session_state.request_count = 0
    return st.session_state.request_count < MAX_REQUESTS_PER_SESSION

def increment_request_count():
    """Call this after every successful research request"""
    if "request_count" not in st.session_state:
        st.session_state.request_count = 0
    st.session_state.request_count += 1

def requests_remaining() -> int:
    if "request_count" not in st.session_state:
        return MAX_REQUESTS_PER_SESSION
    return MAX_REQUESTS_PER_SESSION - st.session_state.request_count
