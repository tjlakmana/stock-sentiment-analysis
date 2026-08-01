"""
Module: __init__.py
Purpose: Package entry-point exposing the EntityResolutionPipeline for external use
Part of: Stock Sentiment Analysis Dashboard
Author: Tjoet Aliya Lakmana

Entity Resolution Engine — multi-stage pipeline for identifying publicly
traded companies in financial news articles.

Public API:
    from sentiment_analysis.entity_resolution.pipeline import EntityResolutionPipeline
    pipeline = EntityResolutionPipeline()
    resolved, tickers, unresolved = pipeline.extract(title, summary, article_id)
"""
