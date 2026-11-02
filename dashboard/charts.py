"""Shared chart styling: one palette, thin marks, hairline axes."""
import altair as alt
import streamlit as st

# Categorical slots in fixed order; slot 1 is the single-series color.
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
MISSING = "#898781"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'


def show(chart, height=280):
    chart = (chart.properties(height=height)
             .configure_axis(gridColor=GRID, domainColor=AXIS, tickColor=AXIS, labelColor=MUTED,
                             titleColor=INK_2, labelFont=FONT, titleFont=FONT, titleFontWeight="normal",
                             labelFontSize=11, titleFontSize=12)
             .configure_legend(labelColor=INK_2, titleColor=INK_2, labelFont=FONT, titleFont=FONT,
                               orient="top", title=None, labelFontSize=12, symbolStrokeWidth=2)
             .configure_view(stroke=None))
    st.altair_chart(chart, width="stretch", theme=None)


def bars(df, x, y, x_title, y_title, tooltip, sort=None, x_type="ordinal", label_angle=0):
    """Single-series vertical bars, rounded at the data end."""
    many = len(df) > 24
    return alt.Chart(df).mark_bar(
        color=SERIES[0], cornerRadiusTopLeft=4 if not many else 2, cornerRadiusTopRight=4 if not many else 2,
    ).encode(
        x=alt.X(f"{x}:{x_type[0].upper()}", title=x_title, sort=sort,
                scale=alt.Scale(paddingInner=0.15 if many else 0.3),
                axis=alt.Axis(labelAngle=label_angle, labelOverlap="greedy", grid=False)),
        y=alt.Y(f"{y}:Q", title=y_title, axis=alt.Axis(format="~s", tickCount=5)),
        tooltip=tooltip,
    )


def hbars(df, y, x, y_title, x_title, tooltip, x_format="~s", domain=None):
    """Single-series horizontal bars, sorted largest first."""
    scale = alt.Scale(domain=domain) if domain else alt.Undefined
    return alt.Chart(df).mark_bar(color=SERIES[0], cornerRadiusTopRight=4, cornerRadiusBottomRight=4).encode(
        y=alt.Y(f"{y}:N", title=y_title, sort="-x", axis=alt.Axis(labelLimit=260)),
        x=alt.X(f"{x}:Q", title=x_title, scale=scale, axis=alt.Axis(format=x_format, tickCount=5)),
        tooltip=tooltip,
    )
