import dash
from dash import html

dash.register_page(__name__, path="/momentum", name="Momentum", title="Momentum")

layout = html.Div(
    className="page-content",
    children=[
        html.Div(
            className="placeholder-page",
            children=[
                html.Span("📈", style={"fontSize": "48px"}),
                html.Div("Momentum", className="placeholder-title"),
                html.Div("Coming soon — Phase 5B", className="placeholder-sub"),
            ],
        )
    ],
)
