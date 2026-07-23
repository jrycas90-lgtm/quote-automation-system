"""
themes.py

Defines the selectable UI color themes and injects the corresponding
CSS. Two themes are available:

  - "AA": an ASSA ABLOY-inspired palette (Access Blue for the sidebar/
    navigation and dropdowns, Deep Blue for text and buttons, Steel Blue
    for the near-white page background). Hex values below are a close
    visual approximation pulled from a reference swatch, not
    pixel-sampled -- treat them as a first pass and fine-tune once you've
    seen it live.
A dark theme was previously offered here and has been removed. It could
not be made to work correctly: .streamlit/config.toml pins Streamlit's
base theme to light, which is required so that canvas-rendered elements
(st.dataframe grids, hover toolbars, tooltips) render consistently for
every viewer instead of following each person's browser dark-mode
setting. Those elements ignore any runtime theme switch, so a dark page
with light-themed grids and toolbars was the unavoidable result.
"""

from __future__ import annotations
import altair as alt
import pandas as pd
import streamlit as st

THEMES = {
    "AA": {
        "background": "#EAF2F6",    # Steel Blue -- very light, near-white
        "sidebar_bg": "#00AEEF",    # Access Blue
        "sidebar_text": "#0A1F44",  # Deep Blue
        "text": "#0A1F44",          # Deep Blue
        "input_bg": "#00AEEF",      # dropdowns / text inputs -- Access Blue
        "input_text": "#0A1F44",    # Deep Blue
        "button_bg": "#0A1F44",     # Deep Blue
        "button_text": "#FFFFFF",
        "chart_bar_color": "#00AEEF",  # Access Blue -- reads well against a light card
    },
}

DEFAULT_THEME = "AA"


def apply_theme(theme_name: str) -> None:
    """Injects CSS for the given theme. Safe to call on every page load
    (it's just a <style> block, not a stateful operation)."""
    theme = THEMES.get(theme_name, THEMES[DEFAULT_THEME])

    st.markdown(
        f"""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

        html, body, [class^="css"], [class*=" css"] {{
            font-family: 'Inter', -apple-system, sans-serif;
        }}

        /* Main page background + default text color */
        .stApp {{
            background-color: {theme['background']};
        }}
        .stApp p, .stApp span, .stApp label, .stApp li,
        .stApp h1, .stApp h2, .stApp h3, .stApp h4, .stApp h5, .stApp h6,
        .stMarkdown {{
            color: {theme['text']};
        }}

        /* Sidebar / navigation */
        section[data-testid="stSidebar"] {{
            background-color: {theme['sidebar_bg']};
        }}
        section[data-testid="stSidebar"] * {{
            color: {theme['sidebar_text']} !important;
        }}

        /* Dropdowns, text inputs, number inputs, date inputs -- "the
        drop downs and other things" */
        .stSelectbox > div > div,
        .stTextInput > div > div,
        .stNumberInput > div > div,
        .stDateInput > div > div,
        .stMultiSelect > div > div {{
            background-color: {theme['input_bg']};
            color: {theme['input_text']};
        }}

        /* The actual <input> elements need their color set directly, not
        just on the wrapper div above. Streamlit's own base theme (pinned
        light in .streamlit/config.toml) colors the input element itself,
        and that wins over a color inherited from the wrapper -- so with
        a dark theme selected the text would render dark-on-dark and be
        invisible. */
        .stTextInput input,
        .stNumberInput input,
        .stDateInput input,
        .stSelectbox input,
        .stMultiSelect input,
        .stTextArea textarea {{
            color: {theme['input_text']} !important;
            -webkit-text-fill-color: {theme['input_text']} !important;
        }}

        /* The dropdown's OPEN options list is a separate popover
        component, not part of the closed box styled above. Verified via
        actual browser inspection of the rendered DOM: this Streamlit
        version renders selectboxes with React Aria's ComboBox, using
        role="listbox" for the popup and role="option" per item -- NOT
        the older BaseWeb data-baseweb attributes that most Streamlit
        CSS guides reference, which is why targeting those did nothing. */
        [role="listbox"] {{
            background-color: {theme['input_bg']} !important;
        }}
        [role="option"] {{
            background-color: {theme['input_bg']} !important;
            color: {theme['input_text']} !important;
        }}
        [role="option"] * {{
            color: {theme['input_text']} !important;
        }}
        [role="option"]:hover,
        [role="option"][data-focused="true"],
        [role="option"][aria-selected="true"] {{
            background-color: rgba(0, 0, 0, 0.15) !important;
        }}

        /* "Real" action buttons: regular buttons and download buttons.
        Text color uses !important + descendant selectors because
        Streamlit nests the label in a child element that would
        otherwise win on specificity and make it invisible. */
        .stButton > button,
        .stDownloadButton > button,
        .stFormSubmitButton > button {{
            background-color: {theme['button_bg']};
            border-color: {theme['button_bg']};
            font-weight: 600;
        }}
        .stButton > button,
        .stButton > button *,
        .stDownloadButton > button,
        .stDownloadButton > button *,
        .stFormSubmitButton > button,
        .stFormSubmitButton > button * {{
            color: {theme['button_text']} !important;
        }}
        .stButton > button:hover,
        .stDownloadButton > button:hover,
        .stFormSubmitButton > button:hover {{
            opacity: 0.85;
        }}

        /* File uploader: the whole dropzone (not just its "Browse files"
        button) gets the same background as other inputs, so the border
        and helper text ("Drag and drop... / Limit 200MB per file") read
        clearly instead of sitting in Streamlit's own default dark box. */
        [data-testid="stFileUploaderDropzone"] {{
            background-color: {theme['input_bg']} !important;
            border: 1px solid {theme['input_bg']} !important;
            border-radius: 10px !important;
        }}
        [data-testid="stFileUploaderDropzone"] *:not(button):not(button *) {{
            color: {theme['input_text']} !important;
        }}
        [data-testid="stFileUploaderDropzone"] button,
        [data-testid="stFileUploaderDropzone"] button * {{
            color: {theme['button_text']} !important;
        }}
        [data-testid="stFileUploaderDropzone"] button {{
            background-color: {theme['button_bg']} !important;
            border-color: {theme['button_bg']} !important;
        }}

        /* NOTE: the st.dataframe grid, its hover toolbar, and tooltips
        ("Show/hide columns" etc.) are deliberately NOT styled here --
        they can't be. The dataframe is drawn on an HTML <canvas>, and
        the toolbars/tooltips render from Streamlit's own internal theme,
        so injected CSS never reaches any of them. Those are controlled
        by .streamlit/config.toml instead, which pins the base theme so
        they render consistently for every viewer regardless of their
        browser's dark-mode preference. */

        /* Metric cards on the Dashboard */
        div[data-testid="stMetric"] {{
            background-color: rgba(151, 166, 195, 0.12);
            border: 1px solid rgba(151, 166, 195, 0.28);
            border-radius: 10px;
            padding: 14px 16px 10px 16px;
        }}

        /* Chart containers (bar charts, etc.) -- same rounded card look
        as the metric tiles above, with extra padding so axis labels and
        values aren't pressed right up against the edge. Targets any
        Streamlit chart element regardless of chart type/version, since
        the internal testid naming varies (stVegaLiteChart,
        stArrowVegaLiteChart, etc.) but all contain "Chart". */
        div[data-testid*="Chart"] {{
            background-color: rgba(151, 166, 195, 0.12);
            border: 1px solid rgba(151, 166, 195, 0.28);
            border-radius: 10px;
            overflow: hidden;
        }}

        h1, h2, h3 {{
            font-weight: 700;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def build_themed_bar_chart(df: pd.DataFrame, x_col: str, y_col: str, theme_name: str) -> alt.Chart:
    """Builds a bar chart with every color explicitly set from the theme,
    instead of using st.bar_chart directly. Streamlit's native charts pick
    their own background/text colors server-side based on the browser's
    light/dark preference (or an actual .streamlit/config.toml [theme]
    section) -- neither of which our CSS injection can reach or override,
    which is why the chart interior stayed dark even after the AA theme
    was applied everywhere else. Building the chart explicitly here, with
    a transparent background so the surrounding card shows through, is
    the reliable fix."""
    theme = THEMES.get(theme_name, THEMES[DEFAULT_THEME])
    bar_color = theme["chart_bar_color"]
    text_color = theme["text"]

    chart = (
        alt.Chart(df)
        .mark_bar(color=bar_color)
        .encode(
            x=alt.X(f"{x_col}:N", sort=None, title=None,
                    axis=alt.Axis(labelColor=text_color, labelAngle=-45)),
            y=alt.Y(f"{y_col}:Q", title=None,
                    axis=alt.Axis(labelColor=text_color, gridColor="rgba(151,166,195,0.25)")),
        )
        .properties(height=380, background="transparent", padding={"left": 12, "right": 24, "top": 16, "bottom": 8})
        .configure_view(strokeWidth=0)
        .configure_axis(domainColor=text_color, labelFontWeight="bold", labelFontSize=12)
    )
    return chart
