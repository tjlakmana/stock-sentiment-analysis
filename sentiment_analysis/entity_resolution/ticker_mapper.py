"""
Module: ticker_mapper.py
Purpose: Re-export TickerMapper from nlp/ to keep the entity_resolution package self-contained
Part of: Stock Sentiment Analysis Dashboard
Author: Tjoet Aliya Lakmana

Ticker mapper for the Entity Resolution Engine.

Re-exports TickerMapper from nlp/ticker_mapper.py — the full company-name
dictionary and fuzzy matching logic live there to avoid duplication.

Usage:
    from sentiment_analysis.entity_resolution.ticker_mapper import TickerMapper
    mapper = TickerMapper(fuzzy_threshold=0.82)
    ticker, confidence = mapper.get_ticker("NVIDIA Corporation")
"""
from sentiment_analysis.nlp.ticker_mapper import TickerMapper  # noqa: F401

__all__ = ["TickerMapper"]
