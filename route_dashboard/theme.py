"""Streamlit theme styles for the QuantumRoute logistics dashboard."""

import streamlit as st


def apply_theme() -> None:
    st.markdown(
        """
        <style>
        :root {
            color-scheme: dark;
            --qr-bg: #08111F;
            --qr-panel: #101D30;
            --qr-card: #14243A;
            --qr-panel-raised: #192B43;
            --qr-border: #2A405D;
            --qr-text: #F1F5F9;
            --qr-muted: #A7B5C8;
            --qr-blue: #3B82F6;
            --qr-cyan: #22D3EE;
            --qr-purple: #A78BFA;
            --qr-green: #34D399;
            --qr-orange: #FBBF24;
            --qr-red: #FB7185;
        }
        html, body, .stApp, [data-testid="stAppViewContainer"] {
            background: var(--qr-bg);
            color: var(--qr-text);
        }
        [data-testid="stMain"] {
            background:
                radial-gradient(ellipse at 72% 0%, rgba(59, 130, 246, .08), transparent 34rem),
                var(--qr-bg);
        }
        [data-testid="stHeader"] {
            background: rgba(8, 17, 31, .96);
            border-bottom: 1px solid rgba(42, 64, 93, .72);
        }
        [data-testid="stSidebar"] {
            background: linear-gradient(180deg, #101D30 0%, #0B1728 100%);
            border-right: 1px solid var(--qr-border);
        }
        [data-testid="stSidebar"] > div:first-child { padding-top: 1rem; }
        .block-container {
            width: min(100%, 1680px);
            padding: 3.6rem clamp(1rem, 3vw, 2.5rem) 3.25rem;
        }
        h1, h2, h3, h4, h5, h6 {
            color: var(--qr-text);
            letter-spacing: -.015em;
            line-height: 1.24;
            text-wrap: balance;
        }
        h1 {
            font-family: 'Aptos Display', 'Segoe UI', sans-serif;
            font-size: clamp(1.75rem, 3vw, 2.35rem);
            font-weight: 760;
        }
        h2 { font-size: 1.5rem; margin-top: 1.7rem; font-weight: 730; }
        h3 { font-size: 1.17rem; font-weight: 720; }
        h4, h5, h6 { font-weight: 680; }
        p, label, [data-testid="stMarkdownContainer"] { color: var(--qr-text); }
        [data-testid="stMarkdownContainer"] strong { color: #FFFFFF; }
        small, [data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p {
            color: var(--qr-muted) !important;
        }
        [data-testid="stCaptionContainer"] { line-height: 1.5; }
        .qr-brand {
            display: flex; align-items: center; gap: .68rem; margin: .1rem 0 .85rem;
            color: var(--qr-text); font-size: 1.18rem; font-weight: 760;
        }
        .qr-mark {
            display: grid; place-items: center; width: 2.4rem; height: 2.4rem;
            border: 1px solid rgba(34, 211, 238, .72); border-radius: 10px;
            background: linear-gradient(135deg, #1D4ED8 0%, #0891B2 100%);
            color: #F1F5F9; font-size: .88rem; font-weight: 800;
            box-shadow: 0 4px 14px rgba(8, 145, 178, .18);
        }
        .qr-subtitle { margin-top: -.2rem; color: var(--qr-muted); font-size: .88rem; }
        .qr-kicker {
            display: inline-flex; max-width: 100%; margin-bottom: .4rem;
            padding: .3rem .52rem; border: 1px solid rgba(34, 211, 238, .42);
            border-radius: 6px; background: rgba(34, 211, 238, .08);
            color: var(--qr-cyan) !important; -webkit-text-fill-color: var(--qr-cyan); opacity: 1;
            font-size: .82rem; font-weight: 760; line-height: 1.4;
            letter-spacing: .06em; overflow-wrap: anywhere;
        }
        .qr-status {
            display: inline-flex; align-items: center; gap: .5rem; max-width: 100%;
            padding: .48rem .72rem; border: 1px solid var(--qr-border);
            border-radius: 999px; background: var(--qr-panel-raised);
            color: var(--qr-text); font-size: .8rem; font-weight: 620;
            overflow-wrap: anywhere;
        }
        .qr-status-dot {
            flex: 0 0 auto; width: .5rem; height: .5rem; border-radius: 50%;
            background: var(--qr-green); box-shadow: 0 0 0 3px rgba(52, 211, 153, .16);
        }
        .qr-traffic {
            display: inline-flex; align-items: center; gap: .55rem; max-width: 100%;
            margin: .1rem 0 .8rem; padding: .52rem .76rem;
            border: 1px solid var(--qr-border); border-radius: 9px;
            background: var(--qr-panel); font-size: .86rem; font-weight: 680;
            overflow-wrap: anywhere;
        }
        .qr-traffic-calm { color: var(--qr-green); }
        .qr-traffic-normal { color: var(--qr-cyan); }
        .qr-traffic-moderate { color: var(--qr-orange); }
        .qr-traffic-heavy, .qr-traffic-storm { color: var(--qr-red); }
        [data-testid="stMetric"] {
            min-width: 0; min-height: 120px; padding: .95rem 1.05rem;
            border: 1px solid var(--qr-border); border-radius: 12px;
            background: linear-gradient(145deg, #192B43 0%, #14243A 85%);
            box-shadow: 0 5px 16px rgba(0, 0, 0, .2);
        }
        [data-testid="stMetricLabel"], [data-testid="stMetricLabel"] p {
            color: var(--qr-muted) !important; font-size: .8rem; font-weight: 560;
        }
        [data-testid="stMetricValue"] {
            color: var(--qr-text) !important; font-size: clamp(1.1rem, 2vw, 1.5rem);
            font-weight: 720; overflow-wrap: anywhere;
        }
        [data-testid="stMetricValue"] [data-testid="stMarkdownContainer"],
        [data-testid="stMetricValue"] p {
            white-space: normal !important; overflow: visible !important;
            text-overflow: clip !important; overflow-wrap: anywhere; line-height: 1.2;
        }
        .st-key-dynamic_traffic_metrics [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {
            min-width: 0;
        }
        [data-testid="stMetricDelta"] { font-size: .78rem; }
        [data-testid="stVerticalBlockBorderWrapper"] > div {
            border-color: var(--qr-border) !important; border-radius: 12px !important;
            background: var(--qr-card); box-shadow: 0 4px 14px rgba(0, 0, 0, .14);
        }
        [data-testid="stForm"] {
            padding: clamp(.8rem, 2vw, 1.25rem); border: 1px solid var(--qr-border);
            border-radius: 12px;
            background: linear-gradient(155deg, #14243A 0%, #101D30 100%);
            box-shadow: 0 4px 16px rgba(0, 0, 0, .14);
        }
        [data-testid="stDataFrame"], [data-testid="stTable"] {
            border: 1px solid var(--qr-border); border-radius: 9px;
            background: var(--qr-panel); overflow: hidden;
        }
        [data-testid="stTabs"] [data-baseweb="tab-list"] {
            gap: .35rem; border-bottom: 1px solid var(--qr-border);
        }
        [data-testid="stTabs"] button[role="tab"] {
            min-height: 2.7rem; border-radius: 7px 7px 0 0;
            color: var(--qr-muted); background: transparent;
        }
        [data-testid="stTabs"] button[aria-selected="true"] {
            color: var(--qr-cyan); border-bottom-color: var(--qr-cyan); font-weight: 700;
        }
        div[data-testid="stRadio"] [role="radiogroup"] { gap: .25rem; }
        div[data-testid="stRadio"] label {
            padding: .5rem .62rem; border: 1px solid transparent;
            border-radius: 8px; color: var(--qr-muted);
        }
        div[data-testid="stRadio"] label:has(input:checked) {
            background: rgba(59, 130, 246, .14); color: var(--qr-text);
            border-color: rgba(59, 130, 246, .4);
        }
        div.stButton > button, div.stFormSubmitButton > button {
            min-height: 2.55rem; padding-inline: 1rem;
            border: 1px solid var(--qr-blue); border-radius: 8px;
            background: var(--qr-blue); color: #FFFFFF; font-weight: 660;
            box-shadow: 0 3px 8px rgba(0, 0, 0, .18);
            transition: background-color .15s ease, border-color .15s ease;
        }
        div.stButton > button:hover, div.stFormSubmitButton > button:hover {
            border-color: #60A5FA; background: #2563EB; color: #FFFFFF;
        }
        div.stButton > button:focus-visible, div.stFormSubmitButton > button:focus-visible {
            outline: 2px solid var(--qr-cyan); outline-offset: 2px;
        }
        div.stButton > button[kind="secondary"],
        div.stFormSubmitButton > button[kind="secondary"] {
            border-color: var(--qr-border); background: var(--qr-panel-raised);
            color: var(--qr-text); box-shadow: none;
        }
        div.stButton > button[kind="secondary"]:hover,
        div.stFormSubmitButton > button[kind="secondary"]:hover {
            border-color: var(--qr-cyan); background: #1C3450;
        }
        [data-testid="stSelectbox"] [data-baseweb="select"] > div,
        [data-testid="stMultiSelect"] [data-baseweb="select"] > div,
        [data-testid="stNumberInput"] input, [data-testid="stTextInput"] input,
        [data-testid="stTimeInput"] input, [data-testid="stTextArea"] textarea {
            background-color: #0D1A2C; border-color: var(--qr-border); color: var(--qr-text);
            box-shadow: inset 0 0 0 1px rgba(167, 181, 200, .04);
        }
        [data-testid="stSelectbox"] [data-baseweb="select"] > div:hover,
        [data-testid="stMultiSelect"] [data-baseweb="select"] > div:hover,
        [data-testid="stNumberInput"] input:hover, [data-testid="stTextInput"] input:hover,
        [data-testid="stTimeInput"] input:hover, [data-testid="stTextArea"] textarea:hover {
            border-color: var(--qr-cyan);
        }
        [data-testid="stSelectbox"] [data-baseweb="select"] *,
        [data-testid="stMultiSelect"] [data-baseweb="select"] * {
            color: var(--qr-text);
        }
        [data-baseweb="popover"], [data-baseweb="menu"], [role="listbox"],
        [role="option"] { background: var(--qr-panel-raised); color: var(--qr-text); }
        [role="option"]:hover, [role="option"][aria-selected="true"] {
            background: #263D5B; color: #FFFFFF;
        }
        input::placeholder, textarea::placeholder { color: var(--qr-muted) !important; opacity: 1; }
        [data-testid="stCheckbox"] label, [data-testid="stToggle"] label {
            color: var(--qr-text);
        }
        [data-testid="stAlert"] { border-radius: 10px; }
        [data-testid="stAlertContainer"] {
            border: 1px solid var(--qr-border);
            border-left: 3px solid var(--qr-cyan);
            border-radius: 10px;
            background: rgba(34, 211, 238, .07) !important;
            color: var(--qr-text) !important;
        }
        [data-testid="stAlertContainer"] [data-testid^="stAlertContent"] {
            color: var(--qr-text) !important;
        }
        [data-testid="stAlertContainer"] svg { color: var(--qr-cyan) !important; }
        [data-testid="stAlertContainer"]:has([data-testid="stAlertContentSuccess"]) {
            border-left-color: var(--qr-green);
            background: rgba(52, 211, 153, .08) !important;
        }
        [data-testid="stAlertContainer"]:has([data-testid="stAlertContentWarning"]) {
            border-left-color: var(--qr-orange);
            background: rgba(251, 191, 36, .08) !important;
        }
        [data-testid="stAlertContainer"]:has([data-testid="stAlertContentError"]) {
            border-left-color: var(--qr-red);
            background: rgba(251, 113, 133, .08) !important;
        }
        [data-testid="stAlert"] p, [data-testid="stAlert"] li { color: inherit; }
        [data-testid="stProgress"] > div > div > div > div { background: var(--qr-cyan); }
        [data-testid="stExpander"] {
            border: 1px solid var(--qr-border); border-radius: 9px; background: var(--qr-card);
        }
        [data-testid="stExpander"] summary, [data-testid="stExpander"] summary p {
            color: var(--qr-text);
        }
        [data-testid="stFileUploader"] section {
            border: 1px dashed #45627F; border-radius: 10px; background: rgba(16, 29, 48, .72);
        }
        [data-testid="stFileUploader"] section:hover { border-color: var(--qr-cyan); }
        [data-testid="stFileUploader"] small { color: var(--qr-muted) !important; }
        [data-testid="stDataFrame"] button { color: var(--qr-cyan); }
        [data-testid="stSidebar"] hr { border-color: var(--qr-border); }
        [data-testid="stSidebar"] [data-testid="stRadio"] label {
            width: 100%; margin: .12rem 0; transition: background-color .15s ease, border-color .15s ease;
        }
        [data-testid="stSidebar"] [data-testid="stRadio"] label:hover {
            background: rgba(59, 130, 246, .08); color: var(--qr-text);
        }
        .qr-demo-pipeline {
            display: flex; flex-direction: column; align-items: center; gap: .35rem;
            margin: 1rem 0 1.25rem; padding: 1rem;
            border: 1px solid var(--qr-border); border-radius: 10px;
            background: var(--qr-panel);
        }
        .qr-demo-flow-step {
            width: min(100%, 620px); padding: .55rem .8rem;
            border: 1px solid var(--qr-border); border-radius: 7px;
            background: var(--qr-panel-raised); color: var(--qr-text);
            text-align: center; font-size: .9rem; font-weight: 650;
            overflow-wrap: anywhere;
        }
        .qr-demo-flow-step:nth-of-type(4n + 1) { border-left: 3px solid var(--qr-purple); }
        .qr-demo-flow-arrow { color: var(--qr-cyan); font-size: 1.05rem; line-height: 1; }
        a { color: var(--qr-cyan); text-underline-offset: .16em; }
        a:hover { color: #67E8F9; }
        iframe { border: 1px solid var(--qr-border) !important; border-radius: 12px !important; }
        @media (max-width: 1200px) {
            .st-key-dynamic_traffic_metrics [data-testid="stHorizontalBlock"] {
                display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .8rem;
            }
            .st-key-dynamic_traffic_metrics [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {
                width: auto !important; flex: initial !important;
            }
        }
        @media (max-width: 900px) {
            .block-container { padding: 4rem 1.1rem 2.5rem; }
            [data-testid="stMetric"] { min-height: 120px; padding: .78rem; }
            [data-testid="stSidebar"] { min-width: min(18rem, 82vw); }
        }
        @media (max-width: 600px) {
            .block-container { padding: 4rem .8rem 2rem; }
            h1 { font-size: 1.7rem; }
            h2 { font-size: 1.3rem; }
            [data-testid="stMetricValue"] { font-size: 1.12rem; }
            [data-testid="stMetricLabel"] { font-size: .74rem; }
            .st-key-dynamic_traffic_metrics [data-testid="stHorizontalBlock"] {
                grid-template-columns: minmax(0, 1fr);
            }
            div.stButton > button, div.stFormSubmitButton > button {
                white-space: normal; height: auto; min-height: 2.7rem;
            }
            .qr-status { font-size: .74rem; }
            [data-testid="stFileUploader"] section { padding: .65rem; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
