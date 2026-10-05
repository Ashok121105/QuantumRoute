"""Streamlit theme styles for the QuantumRoute logistics dashboard."""

import streamlit as st


def apply_theme() -> None:
    st.markdown(
        """
        <style>
        :root {
            color-scheme: light;
            --qr-bg: #F7F1E8;
            --qr-panel: #FFFDF9;
            --qr-panel-raised: #FFF9F0;
            --qr-border: #D8B98E;
            --qr-text: #2B0D19;
            --qr-muted: #6A3F4A;
            --qr-blue: #7B1E2D;
            --qr-purple: #8B5E3C;
            --qr-green: #2B6A4B;
            --qr-orange: #D08C1F;
            --qr-red: #A63B3B;
            --qr-gold: #D9B267;
        }
        html, body, [data-testid="stAppViewContainer"] {
            background: linear-gradient(180deg, #F8F3EA 0%, #F5EFE7 100%);
            color: var(--qr-text);
        }
        [data-testid="stHeader"] { background: rgba(247, 241, 232, .96); }
        [data-testid="stSidebar"] {
            background: linear-gradient(180deg, #FFF9F3 0%, #F7F2EA 100%);
            border-right: 1px solid var(--qr-border);
            box-shadow: 4px 0 18px rgba(63, 15, 27, .06);
        }
        [data-testid="stSidebar"] > div:first-child { padding-top: 1rem; }
        .block-container { max-width: 1600px; padding: 1.55rem 2rem 3.25rem; }
        h1, h2, h3, h4, h5, h6 {
            color: var(--qr-text);
            letter-spacing: 0;
            line-height: 1.22;
            text-wrap: balance;
        }
        h1 { font-family: 'Aptos Display', 'Segoe UI', sans-serif; font-size: 2.2rem; font-weight: 800; }
        h2 { font-size: 1.55rem; margin-top: 1.75rem; font-weight: 760; }
        h3 { font-size: 1.18rem; font-weight: 760; }
        h4, h5, h6 { font-weight: 700; }
        p, label, [data-testid="stMarkdownContainer"] { color: var(--qr-text); }
        small, [data-testid="stCaptionContainer"] { color: var(--qr-muted) !important; }
        .qr-brand {
            display: flex; align-items: center; gap: .68rem; margin: .1rem 0 .85rem;
            color: var(--qr-text); font-size: 1.18rem; font-weight: 760;
        }
        .qr-mark {
            display: grid; place-items: center; width: 2.4rem; height: 2.4rem;
            border: 1px solid rgba(217, 178, 103, .8); border-radius: 10px;
            background: linear-gradient(135deg, var(--qr-blue) 0%, #8F2A3C 100%); color: #F8E7B7;
            font-size: .88rem; font-weight: 800;
            box-shadow: 0 4px 12px rgba(123, 30, 45, .18);
        }
        .qr-subtitle { margin-top: -.2rem; color: var(--qr-muted); font-size: .88rem; }
        .qr-kicker { color: var(--qr-blue); font-size: .72rem; font-weight: 750; letter-spacing: .08em; }
        .qr-status {
            display: inline-flex; align-items: center; gap: .5rem; padding: .48rem .72rem;
            border: 1px solid var(--qr-border); border-radius: 999px; background: rgba(255, 249, 240, .9);
            color: var(--qr-text); font-size: .8rem; font-weight: 620;
        }
        .qr-status-dot { width: .5rem; height: .5rem; border-radius: 50%; background: var(--qr-green); box-shadow: 0 0 0 3px rgba(43, 106, 75, .15); }
        .qr-traffic {
            display: inline-flex; align-items: center; gap: .55rem; margin: .1rem 0 .8rem;
            padding: .52rem .76rem; border: 1px solid var(--qr-border); border-radius: 9px;
            background: var(--qr-panel); font-size: .86rem; font-weight: 680;
        }
        .qr-traffic-calm { color: var(--qr-green); }
        .qr-traffic-normal { color: var(--qr-blue); }
        .qr-traffic-moderate { color: #B45309; }
        .qr-traffic-heavy { color: var(--qr-red); }
        .qr-traffic-storm { color: #B91C1C; }
        [data-testid="stMetric"] {
            min-height: 108px; padding: .95rem 1.05rem; border: 1px solid var(--qr-border);
            border-radius: 12px; background: linear-gradient(180deg, #FFFDF9 0%, #FDF7EF 100%);
            box-shadow: 0 3px 12px rgba(63, 15, 27, .05);
        }
        [data-testid="stMetricLabel"] { color: var(--qr-muted) !important; font-size: .8rem; }
        [data-testid="stMetricValue"] { color: var(--qr-text) !important; font-size: 1.48rem; font-weight: 720; }
        [data-testid="stMetricDelta"] { font-size: .78rem; }
        [data-testid="stVerticalBlockBorderWrapper"] > div {
            border-color: var(--qr-border) !important; border-radius: 12px !important;
            background: var(--qr-panel); box-shadow: 0 3px 12px rgba(63, 15, 27, .035);
        }
        [data-testid="stForm"] {
            padding: 1.15rem; border: 1px solid var(--qr-border); border-radius: 12px;
            background: linear-gradient(180deg, #FFFDF9 0%, #F7F2EA 100%); box-shadow: 0 3px 12px rgba(63, 15, 27, .04);
        }
        [data-testid="stDataFrame"] { border: 1px solid var(--qr-border); border-radius: 9px; background: var(--qr-panel); }
        [data-testid="stTabs"] [data-baseweb="tab-list"] { gap: .35rem; border-bottom-color: var(--qr-border); }
        [data-testid="stTabs"] button[role="tab"] {
            border-radius: 7px 7px 0 0; color: var(--qr-muted); background: transparent;
        }
        [data-testid="stTabs"] button[aria-selected="true"] {
            color: var(--qr-blue); border-bottom-color: var(--qr-blue); font-weight: 700;
        }
        [data-testid="stTabs"] button[role="tab"]:nth-child(2) { color: var(--qr-purple); }
        [data-testid="stTabs"] button[role="tab"]:nth-child(2)[aria-selected="true"] {
            border-bottom-color: var(--qr-purple);
        }
        div[data-testid="stRadio"] [role="radiogroup"] { gap: .25rem; }
        div[data-testid="stRadio"] label {
            padding: .5rem .62rem; border-radius: 8px; color: var(--qr-muted);
        }
        div[data-testid="stRadio"] label:has(input:checked) {
            background: rgba(217, 178, 103, .16); color: var(--qr-blue); border: 1px solid rgba(217, 178, 103, .45);
        }
        div.stButton > button, div.stFormSubmitButton > button {
            min-height: 2.55rem; border: 1px solid var(--qr-blue); border-radius: 8px;
            background: linear-gradient(180deg, var(--qr-blue) 0%, #8F2A3C 100%); color: #FBEED0; font-weight: 650;
            box-shadow: 0 2px 5px rgba(123, 30, 45, .18);
        }
        div.stButton > button:hover, div.stFormSubmitButton > button:hover {
            border-color: #5E0E1C; background: linear-gradient(180deg, #5E0E1C 0%, var(--qr-blue) 100%); color: #FFF5DA;
        }
        [data-testid="stSelectbox"] [data-baseweb="select"] > div,
        [data-testid="stNumberInput"] input, [data-testid="stTextInput"] input,
        [data-testid="stTimeInput"] input, [data-testid="stTextArea"] textarea {
            background-color: #FFFDFB; border-color: var(--qr-border); color: var(--qr-text);
            box-shadow: inset 0 0 0 1px rgba(217, 178, 103, .15);
        }
        [data-testid="stAlert"] { border-radius: 10px; }
        iframe { border: 1px solid var(--qr-border) !important; border-radius: 12px !important; }
        [data-testid="stSidebar"] hr { border-color: var(--qr-border); }
        .qr-demo-pipeline {
            display: flex; flex-direction: column; align-items: center; gap: .35rem;
            margin: 1rem 0 1.25rem; padding: 1rem;
            border: 1px solid var(--qr-border); border-radius: 10px;
            background: var(--qr-panel);
        }
        .qr-demo-flow-step {
            width: min(100%, 620px); padding: .55rem .8rem;
            border: 1px solid var(--qr-border); border-radius: 7px;
            background: #F8FAFC; color: var(--qr-text);
            text-align: center; font-size: .9rem; font-weight: 650;
        }
        .qr-demo-flow-step:nth-of-type(4n + 1) { border-left: 3px solid var(--qr-blue); }
        .qr-demo-flow-arrow { color: var(--qr-purple); font-size: 1.05rem; line-height: 1; }
        [data-testid="stExpander"] { border-color: var(--qr-border); border-radius: 9px; }
        a { color: var(--qr-blue); }
        @media (max-width: 800px) {
            .block-container { padding: 1rem 1rem 2.5rem; }
            h1 { font-size: 1.72rem; }
            [data-testid="stMetric"] { min-height: 96px; padding: .78rem; }
            [data-testid="stMetricValue"] { font-size: 1.22rem; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )