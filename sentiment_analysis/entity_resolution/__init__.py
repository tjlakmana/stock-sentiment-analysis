"""
Entity Resolution Engine — multi-stage pipeline for identifying publicly
traded companies in financial news articles.

Public API:
    from sentiment_analysis.entity_resolution.pipeline import EntityResolutionPipeline
    pipeline = EntityResolutionPipeline()
    resolved, tickers, unresolved = pipeline.extract(title, summary, article_id)
"""
