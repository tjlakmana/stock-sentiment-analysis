"""
Confidence scoring for Entity Resolution Engine results.

Two orthogonal scores:
  - classification_confidence: how sure we are of the entity *type*
  - resolution_confidence:     how sure we are of the ticker *mapping*

The pipeline stores resolution_confidence as the final entity confidence.
"""
from __future__ import annotations


def classification_confidence(entity_type: str, spacy_label: str) -> float:
    """
    Confidence that the entity has been correctly classified.

    spaCy's PERSON and GPE labels are very reliable (≥ 0.97).
    ORG is reliable but includes both companies and non-company orgs (0.80).
    Lexically-derived types (Disease, Drug, …) sit in between.
    """
    if entity_type == "Person"           and spacy_label == "PERSON": return 1.00
    if entity_type == "Country"          and spacy_label == "GPE":    return 0.98
    if entity_type == "GovernmentAgency":                              return 0.97
    if entity_type == "Exchange":                                      return 0.97
    if entity_type == "Currency":                                      return 0.95
    if entity_type == "EconomicIndicator":                             return 0.92
    if entity_type == "Disease":                                       return 0.90
    if entity_type == "Drug":                                          return 0.88
    if entity_type == "Commodity":                                     return 0.88
    if entity_type == "Animal":                                        return 0.85
    if entity_type == "ETF":                                           return 0.90
    if entity_type == "Technology":                                    return 0.80
    if entity_type == "Organization":                                  return 0.80
    if entity_type == "Company"  and spacy_label == "ORG":            return 0.80
    if entity_type == "Company":                                       return 0.60
    return 0.60


def resolution_confidence(matched_by: str, mapper_score: float) -> float:
    """
    Confidence for a Company → ticker mapping, given how the match was made.

    matched_by values (from CompanyResolver):
        "explicit_ticker"   — $AAPL, NASDAQ:MSFT, etc.  → 1.00
        "company_dictionary"— TickerMapper exact/fuzzy   → 0.95 × mapper_score
        "none"              — no match found             → 0.00
    """
    if matched_by == "explicit_ticker":
        return 1.0
    if matched_by == "company_dictionary":
        return round(0.95 * mapper_score, 3)
    return 0.0
