"""Streamlit theme styles for the QuantumRoute logistics dashboard."""

import streamlit as st


def apply_theme() -> None:
    st.markdown(
        """
        <style>
        :root {
            color-scheme: dark;
            --qr-bg: #080d19;
            --qr-panel: #111a2a;
            --qr-panel-raised: #162237;
            --qr-border: #26334a;
            --qr-text: #edf4ff;
            --qr-muted: #9aa9bf;
            --qr-blue: #5794ff;
            --qr-cyan: #3dd6d0;
            --qr-purple: #b48aff;
            --qr-green: #63d6a2;
            --qr-orange: #ffad66;
            --qr-red: #ff707e;
        }
        html, body, [data-testid="stAppViewContainer"] {
            background: radial-gradient(ellipse at 82% -12%, rgba(45, 91, 167, .22), transparent 34%),
                        radial-gradient(ellipse at 4% 36%, rgba(55, 186, 195, .08), transparent 28%),
                        var(--qr-bg);
            color: var(--qr-text);
        }
        [data-testid="stHeader"] { background: rgba(8, 13, 25, .78); }
        [data-testid="stSidebar"] {
            background: linear-gradient(180deg, #101a2b 0%, #0c1423 100%);
            border-right: 1px solid var(--qr-border);
        }
        [data-testid="stSidebar"] > div:first-child { padding-top: 1.2rem; }
        .block-container { max-width: 1560px; padding: 1.7rem 2.2rem 3.5rem; }
        h1, h2, h3, h4 { color: var(--qr-text); letter-spacing: 0; }
        h1 { font-family: 'Aptos Display', 'Segoe UI', sans-serif; font-size: 2.15rem; font-weight: 650; }
        h2 { font-size: 1.25rem; margin-top: 1.7rem; }
        h3 { font-size: 1.08rem; }
        p, label, [data-testid="stMarkdownContainer"] { color: var(--qr-text); }
        small, [data-testid="stCaptionContainer"] { color: var(--qr-muted) !important; }
        .qr-brand {
            display: flex; align-items: center; gap: .72rem; margin: .1rem 0 1rem;
            color: var(--qr-text); font-size: 1.18rem; font-weight: 750;
        }
        .qr-mark {
            display: grid; place-items: center; width: 2.35rem; height: 2.35rem;
            border: 1px solid rgba(87, 148, 255, .7); border-radius: 10px;
            background: linear-gradient(145deg, rgba(87, 148, 255, .25), rgba(61, 214, 208, .12));
            color: var(--qr-cyan); font-size: .88rem; font-weight: 800;
        }
        .qr-subtitle { margin-top: -.25rem; color: var(--qr-muted); font-size: .86rem; }
        .qr-kicker { color: var(--qr-cyan); font-size: .74rem; font-weight: 700; }
        .qr-status {
            display: inline-flex; align-items: center; gap: .5rem; padding: .48rem .7rem;
            border: 1px solid var(--qr-border); border-radius: 999px; background: rgba(17, 26, 42, .8);
            color: var(--qr-muted); font-size: .82rem;
        }
        .qr-status-dot { width: .52rem; height: .52rem; border-radius: 50%; background: var(--qr-green); }
        .qr-traffic {
            display: inline-flex; align-items: center; gap: .55rem; margin: .1rem 0 .8rem;
            padding: .55rem .8rem; border: 1px solid var(--qr-border); border-radius: 9px;
            background: rgba(17, 26, 42, .82); font-size: .88rem; font-weight: 650;
        }
        .qr-traffic-calm { color: var(--qr-green); }
        .qr-traffic-normal { color: var(--qr-cyan); }
        .qr-traffic-moderate { color: var(--qr-orange); }
        .qr-traffic-heavy { color: var(--qr-orange); }
        .qr-traffic-storm { color: var(--qr-red); }
        [data-testid="stMetric"] {
            min-height: 116px; padding: 1rem 1.1rem; border: 1px solid var(--qr-border);
            border-radius: 12px; background: linear-gradient(145deg, rgba(22, 34, 55, .95), rgba(15, 24, 40, .95));
            box-shadow: 0 8px 24px rgba(0, 0, 0, .16);
        }
        [data-testid="stMetricLabel"] { color: var(--qr-muted) !important; font-size: .82rem; }
        [data-testid="stMetricValue"] { color: var(--qr-text) !important; font-size: 1.55rem; }
        [data-testid="stMetricDelta"] { font-size: .78rem; }
        [data-testid="stVerticalBlockBorderWrapper"] > div {
            border-color: var(--qr-border) !important; border-radius: 12px !important;
            background: rgba(17, 26, 42, .64);
        }
        [data-testid="stForm"] {
            padding: 1.2rem; border: 1px solid var(--qr-border); border-radius: 12px;
            background: rgba(17, 26, 42, .72);
        }
        [data-testid="stDataFrame"] { border: 1px solid var(--qr-border); border-radius: 10px; }
        [data-testid="stTabs"] [data-baseweb="tab-list"] { gap: .4rem; border-bottom-color: var(--qr-border); }
        [data-testid="stTabs"] button[role="tab"] {
            border-radius: 8px 8px 0 0; color: var(--qr-muted); background: transparent;
        }
        [data-testid="stTabs"] button[aria-selected="true"] {
            color: var(--qr-cyan); border-bottom-color: var(--qr-cyan);
        }
        [data-testid="stTabs"] button[role="tab"]:nth-child(2) { color: var(--qr-purple); }
        [data-testid="stTabs"] button[role="tab"]:nth-child(2)[aria-selected="true"] {
            border-bottom-color: var(--qr-purple);
        }
        div[data-testid="stRadio"] [role="radiogroup"] { gap: .25rem; }
        div[data-testid="stRadio"] label {
            padding: .55rem .65rem; border-radius: 8px; color: var(--qr-muted);
        }
        div[data-testid="stRadio"] label:has(input:checked) {
            background: rgba(87, 148, 255, .14); color: var(--qr-text);
        }
        div.stButton > button, div.stFormSubmitButton > button {
            min-height: 2.65rem; border: 1px solid #38517a; border-radius: 9px;
            background: linear-gradient(110deg, #2563c7, #237eaa); color: #fff; font-weight: 650;
        }
        div.stButton > button:hover, div.stFormSubmitButton > button:hover {
            border-color: var(--qr-cyan); color: #fff; filter: brightness(1.1);
        }
        [data-testid="stSelectbox"] [data-baseweb="select"] > div,
        [data-testid="stNumberInput"] input, [data-testid="stTextInput"] input,
        [data-testid="stTimeInput"] input, [data-testid="stTextArea"] textarea {
            background-color: #101a2a; border-color: var(--qr-border); color: var(--qr-text);
        }
        [data-testid="stAlert"] { border-radius: 10px; }
        iframe { border: 1px solid var(--qr-border) !important; border-radius: 12px !important; }
        [data-testid="stSidebar"] hr { border-color: var(--qr-border); }
        .qr-demo-pipeline {
            display: flex; flex-direction: column; align-items: center; gap: .35rem;
            margin: 1rem 0 1.25rem; padding: 1rem;
            border: 1px solid var(--qr-border); border-radius: 10px;
            background: linear-gradient(135deg, rgba(61, 214, 208, .08), rgba(180, 138, 255, .08));
        }
        .qr-demo-flow-step {
            width: min(100%, 620px); padding: .55rem .8rem;
            border: 1px solid rgba(87, 148, 255, .28); border-radius: 7px;
            background: rgba(8, 13, 25, .62); color: var(--qr-text);
            text-align: center; font-size: .9rem; font-weight: 650;
        }
        .qr-demo-flow-step:nth-of-type(4n + 1) { border-left: 3px solid var(--qr-cyan); }
        .qr-demo-flow-arrow { color: var(--qr-purple); font-size: 1.05rem; line-height: 1; }
        @media (max-width: 800px) {
            .block-container { padding: 1rem 1rem 2.5rem; }
            h1 { font-size: 1.8rem; }
            [data-testid="stMetric"] { min-height: 100px; padding: .8rem; }
            [data-testid="stMetricValue"] { font-size: 1.25rem; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )