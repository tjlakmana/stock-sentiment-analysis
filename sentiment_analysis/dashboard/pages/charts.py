"""
Module: charts.py
Purpose: Dash page embedding a full-screen TradingView advanced chart widget
Part of: Stock Sentiment Analysis Dashboard
Author: Tjoet Aliya Lakmana

Charts page — full-screen TradingView advanced chart.
"""
import dash
from dash import html

dash.register_page(__name__, path="/charts", name="Charts", title="Charts")

_TV_SRC = (
    "https://www.tradingview.com/widgetembed/"
    "?frameElementId=tradingview_chart"
    "&symbol=AAPL"
    "&interval=D"
    "&theme=dark"
    "&style=1"
    "&locale=en"
    "&toolbar_bg=%23131722"
    "&enable_publishing=false"
    "&hide_top_toolbar=false"
    "&hide_legend=false"
    "&save_image=false"
    "&hide_side_toolbar=false"
    "&allow_symbol_change=true"
    "&studies=%5B%5D"
    "&container_id=tradingview_chart"
)

layout = html.Div(
    style={"padding": "0", "margin": "0", "height": "calc(100vh - 60px)"},
    children=[
        html.Iframe(
            src=_TV_SRC,
            style={"width": "100%", "height": "100%", "border": "none", "display": "block"},
        ),
    ],
)
