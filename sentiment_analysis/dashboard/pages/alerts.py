"""
Module: alerts.py
Purpose: Dash page for managing price/sentiment alert rules and viewing their firing history
Part of: Stock Sentiment Analysis Dashboard
Author: Tjoet Aliya Lakmana

Alerts page — create, edit, toggle, and delete price/sentiment alert rules
with a live history of every firing.
"""
from __future__ import annotations

import dash
import dash_bootstrap_components as dbc
from dash import Input, Output, State, callback, dcc, html, ALL, ctx
from dash.exceptions import PreventUpdate

from sentiment_analysis.dashboard.db import (
    create_alert,
    update_alert,
    delete_alert,
    toggle_alert,
    list_alerts,
    list_alert_history,
    count_triggered_today,
    ticker_exists,
)

dash.register_page(__name__, path="/alerts", name="Alerts", title="Alerts")

_TYPE_ICONS = {
    "price":         "📈",
    "sentiment":     "😊",
    "breaking_news": "📰",
    "volume_spike":  "📊",
}

_NO_THRESHOLD_TYPES = {"breaking_news", "volume_spike"}

# ── Layout ────────────────────────────────────────────────────────────────────

layout = html.Div(
    className="page-content",
    children=[
        dcc.Store(id="al-store", data=0),
        dcc.Store(id="al-edit-store", data=None),   # None=hidden  {}=create  {id,..}=edit
        dcc.Store(id="al-delete-pending", data=None),
        dcc.Interval(id="al-interval", interval=60_000, n_intervals=0),
        dcc.ConfirmDialog(
            id="al-confirm-delete",
            message="Delete this alert and all its history? This cannot be undone.",
        ),

        # Page header
        html.Div(className="al-page-header", children=[
            html.Div("Alerts", className="al-title"),
            html.Button(
                "+ Create Alert", id="al-create-btn",
                className="al-create-btn", n_clicks=0,
            ),
        ]),

        # Feedback message (shown after create/edit/error)
        html.Div(id="al-msg", className="al-msg", style={"display": "none"}),

        # Create / Edit form
        html.Div(
            id="al-form-wrap", className="al-form-wrap",
            style={"display": "none"},
            children=[
                html.Div(id="al-form-title", children="New Alert Rule", className="al-form-title"),
                html.Div(className="al-form-row", children=[
                    html.Div(className="al-form-field", children=[
                        html.Label("Ticker", className="al-form-label"),
                        dcc.Input(
                            id="al-ticker", className="al-input",
                            placeholder="e.g. AAPL", type="text", debounce=False,
                        ),
                        html.Div(id="al-ticker-err", className="al-field-err",
                                 style={"display": "none"}),
                    ]),
                    html.Div(className="al-form-field", children=[
                        html.Label("Type", className="al-form-label"),
                        dbc.Select(
                            id="al-type",
                            className="filter-select",
                            style={"height": "36px", "fontSize": "13px"},
                            options=[
                                {"label": "📈  Price",          "value": "price"},
                                {"label": "😊  Sentiment",      "value": "sentiment"},
                                {"label": "📰  Breaking News",  "value": "breaking_news"},
                                {"label": "📊  Volume Spike",   "value": "volume_spike"},
                            ],
                            value="price",
                        ),
                    ]),
                    html.Div(id="al-operator-wrap", className="al-form-field", children=[
                        html.Label("Condition", className="al-form-label"),
                        dbc.Select(
                            id="al-operator",
                            className="filter-select",
                            style={"height": "36px", "fontSize": "13px"},
                            options=[
                                {"label": "Above  (>)", "value": ">"},
                                {"label": "Below  (<)", "value": "<"},
                            ],
                            value=">",
                        ),
                    ]),
                    html.Div(id="al-threshold-wrap", className="al-form-field", children=[
                        html.Label("Threshold", className="al-form-label"),
                        dcc.Input(
                            id="al-threshold", className="al-input",
                            placeholder="e.g. 150.00", type="number",
                            step=0.01, debounce=False,
                        ),
                        html.Div(id="al-threshold-err", className="al-field-err",
                                 style={"display": "none"}),
                    ]),
                ]),
                html.Div(className="al-form-row", children=[
                    html.Button("Add Alert", id="al-submit-btn",
                                className="al-submit-btn", n_clicks=0),
                    html.Button("Cancel",   id="al-cancel-btn",
                                className="al-cancel-btn", n_clicks=0),
                ]),
            ],
        ),

        # Dynamic data area (summary + tables)
        html.Div(id="al-body"),
    ],
)

# ── Callbacks ─────────────────────────────────────────────────────────────────

@callback(
    Output("al-edit-store",    "data"),
    Output("al-form-wrap",     "style"),
    Output("al-form-title",    "children"),
    Output("al-submit-btn",    "children"),
    Output("al-ticker",        "value"),
    Output("al-type",          "value"),
    Output("al-operator",      "value"),
    Output("al-threshold",     "value"),
    Output("al-ticker-err",    "style"),
    Output("al-threshold-err", "style"),
    Input("al-create-btn",               "n_clicks"),
    Input("al-cancel-btn",               "n_clicks"),
    Input({"type": "al-edit", "index": ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def _handle_form_open(create_clicks, cancel_clicks, edit_clicks):
    triggered_id = ctx.triggered_id
    hidden  = {"display": "none"}
    visible = {"display": "block"}

    if triggered_id == "al-cancel-btn":
        return None, hidden, "New Alert Rule", "Add Alert", "", "price", ">", None, hidden, hidden

    if triggered_id == "al-create-btn":
        return {}, visible, "New Alert Rule", "Add Alert", "", "price", ">", None, hidden, hidden

    # Edit button clicked
    if isinstance(triggered_id, dict) and triggered_id.get("type") == "al-edit":
        alert_id = triggered_id["index"]
        df = list_alerts()
        if df.empty:
            raise PreventUpdate
        match = df[df["id"] == alert_id]
        if match.empty:
            raise PreventUpdate
        row = match.iloc[0]
        return (
            {"id": int(alert_id)},
            visible,
            "Edit Alert Rule",
            "Save Changes",
            str(row["ticker"]),
            str(row["alert_type"]),
            str(row["operator"]),
            float(row["threshold"]),
            hidden,
            hidden,
        )

    raise PreventUpdate


@callback(
    Output("al-operator-wrap",  "style"),
    Output("al-threshold-wrap", "style"),
    Input("al-type", "value"),
)
def _toggle_field_visibility(alert_type):
    """Hide the Condition/Threshold fields for types that don't need them."""
    if alert_type in _NO_THRESHOLD_TYPES:
        return {"display": "none"}, {"display": "none"}
    return {}, {}


@callback(
    Output("al-store",         "data",     allow_duplicate=True),
    Output("al-msg",           "children"),
    Output("al-msg",           "style"),
    Output("al-msg",           "className"),
    Output("al-edit-store",    "data",     allow_duplicate=True),
    Output("al-form-wrap",     "style",    allow_duplicate=True),
    Output("al-ticker-err",    "children"),
    Output("al-ticker-err",    "style",    allow_duplicate=True),
    Output("al-threshold-err", "children"),
    Output("al-threshold-err", "style",    allow_duplicate=True),
    Input("al-submit-btn",  "n_clicks"),
    State("al-ticker",      "value"),
    State("al-type",        "value"),
    State("al-operator",    "value"),
    State("al-threshold",   "value"),
    State("al-edit-store",  "data"),
    State("al-store",       "data"),
    prevent_initial_call=True,
)
def _submit(n_clicks, ticker, alert_type, operator, threshold, edit_store, version):
    if not n_clicks:
        raise PreventUpdate

    hidden  = {"display": "none"}
    visible = {"display": "block"}

    ticker_err = ""
    ticker_err_v = hidden
    threshold_err = ""
    threshold_err_v = hidden
    has_error = False

    # Validate ticker
    if not ticker or not ticker.strip():
        ticker_err = "Ticker is required."
        ticker_err_v = visible
        has_error = True
    else:
        ticker = ticker.strip().upper()
        if not ticker_exists(ticker):
            ticker_err = f"'{ticker}' was not found in price data."
            ticker_err_v = visible
            has_error = True

    # Validate threshold (only for types that use it)
    needs_threshold = alert_type not in _NO_THRESHOLD_TYPES
    if needs_threshold:
        if threshold is None:
            threshold_err = "Threshold is required."
            threshold_err_v = visible
            has_error = True
        else:
            threshold = float(threshold)
            if alert_type == "sentiment" and not (-1.0 <= threshold <= 1.0):
                threshold_err = "Sentiment threshold must be between -1 and 1."
                threshold_err_v = visible
                has_error = True
            elif alert_type == "price" and threshold <= 0:
                threshold_err = "Price threshold must be greater than 0."
                threshold_err_v = visible
                has_error = True
    else:
        # Placeholder values — not evaluated by the scheduler for these types
        threshold = 0.0
        operator  = ">"

    if has_error:
        return (
            version, "", hidden, "al-msg",
            edit_store, visible,
            ticker_err, ticker_err_v,
            threshold_err, threshold_err_v,
        )

    # Persist
    is_edit = isinstance(edit_store, dict) and "id" in edit_store
    if is_edit:
        ok = update_alert(int(edit_store["id"]), ticker, alert_type, operator, threshold)
        msg = f"Alert for {ticker} updated."
    else:
        ok = create_alert(ticker, alert_type, operator, threshold)
        msg = f"Alert created for {ticker}."

    if ok:
        return (
            version + 1, msg, visible, "al-msg al-msg-ok",
            None, hidden,
            "", hidden, "", hidden,
        )
    return (
        version, "Failed — check server logs.", visible, "al-msg al-msg-error",
        edit_store, visible,
        "", hidden, "", hidden,
    )


@callback(
    Output("al-delete-pending", "data"),
    Output("al-confirm-delete", "displayed"),
    Output("al-confirm-delete", "message"),
    Input({"type": "al-delete", "index": ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def _request_delete(delete_clicks):
    if not any(c for c in (delete_clicks or []) if c):
        raise PreventUpdate
    triggered_id = ctx.triggered_id
    if not isinstance(triggered_id, dict) or triggered_id.get("type") != "al-delete":
        raise PreventUpdate
    alert_id = triggered_id["index"]
    return (
        alert_id,
        True,
        f"Delete alert #{alert_id} and all its history? This cannot be undone.",
    )


@callback(
    Output("al-store",          "data",  allow_duplicate=True),
    Output("al-delete-pending", "data",  allow_duplicate=True),
    Input("al-confirm-delete",  "submit_n_clicks"),
    Input("al-confirm-delete",  "cancel_n_clicks"),
    State("al-delete-pending",  "data"),
    State("al-store",           "data"),
    prevent_initial_call=True,
)
def _confirm_delete(submit_n, cancel_n, pending_id, version):
    if not ctx.triggered:
        raise PreventUpdate
    prop = ctx.triggered[0]["prop_id"]
    if "submit_n_clicks" in prop and submit_n and pending_id is not None:
        delete_alert(int(pending_id))
        return version + 1, None
    return version, None


@callback(
    Output("al-store", "data", allow_duplicate=True),
    Input({"type": "al-toggle", "index": ALL}, "n_clicks"),
    State("al-store", "data"),
    prevent_initial_call=True,
)
def _handle_toggle(n_clicks_list, version):
    if not any(c for c in (n_clicks_list or []) if c):
        raise PreventUpdate
    triggered_id = ctx.triggered_id
    if not isinstance(triggered_id, dict) or triggered_id.get("type") != "al-toggle":
        raise PreventUpdate
    toggle_alert(int(triggered_id["index"]))
    return version + 1


@callback(
    Output("al-body", "children"),
    Input("al-store",    "data"),
    Input("al-interval", "n_intervals"),
)
def _render_body(version, _n):
    alerts_df   = list_alerts()
    history_df  = list_alert_history(limit=50)
    today_count = count_triggered_today()

    total  = len(alerts_df)
    active = int(alerts_df["is_active"].sum()) if not alerts_df.empty else 0

    # Summary cards
    cards = html.Div(className="al-summary", children=[
        _card("Total Alerts",    str(total)),
        _card("Active",          str(active)),
        _card("Triggered Today", str(today_count)),
    ])

    # Alert rules table
    if alerts_df.empty:
        alert_section = _empty("No alerts yet", "Click '+ Create Alert' to add one.")
    else:
        rows = []
        for _, row in alerts_df.iterrows():
            is_active  = bool(row["is_active"])
            atype      = str(row["alert_type"])
            icon       = _TYPE_ICONS.get(atype, "")
            condition  = f"{row['operator']} {_fmt_threshold(atype, row['threshold'])}"

            rows.append(html.Tr(className="al-tr", children=[
                html.Td(
                    html.Span(
                        "Active" if is_active else "Paused",
                        className="al-badge al-badge-active" if is_active else "al-badge al-badge-inactive",
                    ),
                    className="al-td",
                ),
                html.Td(html.Strong(str(row["ticker"])), className="al-td"),
                html.Td(f"{icon}  {atype.capitalize()}", className="al-td"),
                html.Td(condition, className="al-td al-td-mono"),
                html.Td(_fmt_dt(row.get("created_at")), className="al-td al-td-muted"),
                html.Td(
                    html.Button(
                        "Pause" if is_active else "Resume",
                        id={"type": "al-toggle", "index": int(row["id"])},
                        className="al-toggle-btn",
                        n_clicks=0,
                    ),
                    className="al-td",
                ),
                html.Td(className="al-td al-td-actions", children=[
                    html.Button(
                        "Edit",
                        id={"type": "al-edit",   "index": int(row["id"])},
                        className="al-edit-btn",
                        n_clicks=0,
                    ),
                    html.Button(
                        "Delete",
                        id={"type": "al-delete", "index": int(row["id"])},
                        className="al-delete-btn",
                        n_clicks=0,
                    ),
                ]),
            ]))
        alert_section = html.Table(className="al-table", children=[
            html.Thead(html.Tr([
                html.Th("Status",    className="al-th"),
                html.Th("Ticker",    className="al-th"),
                html.Th("Type",      className="al-th"),
                html.Th("Condition", className="al-th"),
                html.Th("Created",   className="al-th"),
                html.Th("",          className="al-th"),
                html.Th("",          className="al-th"),
            ])),
            html.Tbody(rows),
        ])

    # History table
    if history_df.empty:
        history_section = _empty(
            "No alerts triggered yet",
            "History appears here when a condition is first met.",
        )
    else:
        hist_rows = []
        for _, row in history_df.iterrows():
            atype = str(row.get("alert_type", ""))
            icon  = _TYPE_ICONS.get(atype, "")
            hist_rows.append(html.Tr(className="al-tr", children=[
                html.Td(html.Strong(str(row["ticker"])), className="al-td"),
                html.Td(f"{icon}  {atype.capitalize()}", className="al-td"),
                html.Td(str(row.get("message", "—")), className="al-td al-history-msg"),
                html.Td(_fmt_dt(row.get("triggered_at")), className="al-td al-td-muted"),
            ]))
        history_section = html.Table(className="al-table", children=[
            html.Thead(html.Tr([
                html.Th("Ticker",       className="al-th"),
                html.Th("Type",         className="al-th"),
                html.Th("Message",      className="al-th"),
                html.Th("Triggered At", className="al-th"),
            ])),
            html.Tbody(hist_rows),
        ])

    return [
        cards,
        html.Div("Alert Rules", className="al-section-title"),
        alert_section,
        html.Div("Recent History", className="al-section-title"),
        history_section,
    ]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _card(label: str, value: str) -> html.Div:
    return html.Div(className="al-card", children=[
        html.Div(value, className="al-card-value"),
        html.Div(label, className="al-card-label"),
    ])


def _empty(title: str, sub: str) -> html.Div:
    return html.Div(className="al-empty", children=[
        html.Div(title, className="al-empty-title"),
        html.Div(sub,   className="al-empty-sub"),
    ])


def _fmt_threshold(alert_type: str, val) -> str:
    try:
        v = float(val)
        return f"${v:.2f}" if alert_type == "price" else f"{v:.3f}"
    except Exception:
        return str(val)


def _fmt_dt(val) -> str:
    try:
        import pandas as pd
        return pd.Timestamp(val).strftime("%b %d, %Y %H:%M")
    except Exception:
        return "—"
