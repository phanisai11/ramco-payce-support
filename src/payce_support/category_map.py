"""Canonical ticket-category map (P1 — queue bouncing).

Raw channel/category labels arrive in dozens of spellings
("PF / EPF Queries", "payroll- run", "Payslip"). This module collapses
them to a small canonical set so reporting, routing hints and analytics
never fragment on label variants.
"""

from __future__ import annotations

import re

# Nine canonical categories — mirrors the Ops-curated taxonomy in the
# GCP architecture (category_map table).
CANONICAL_CATEGORIES: tuple[str, ...] = (
    "payroll_run",
    "payslip_income",
    "statutory_deductions",
    "wps_payments",
    "access_login",
    "billing_refunds",
    "how_to",
    "integrations",
    "status_inquiry",
    "other",
)

# Raw label → canonical. Matching is on a normalised key (lowercase,
# non-alnum → single space, collapsed).
_ALIASES: dict[str, str] = {
    # payroll run
    "payroll": "payroll_run",
    "payroll run": "payroll_run",
    "payroll processing": "payroll_run",
    "salary run": "payroll_run",
    "pay run": "payroll_run",
    "monthly payroll": "payroll_run",
    "payroll cycle": "payroll_run",
    "run status": "payroll_run",
    "payroll failed": "payroll_run",
    # payslip / income
    "payslip": "payslip_income",
    "payslip issue": "payslip_income",
    "salary slip": "payslip_income",
    "income tax certificate": "payslip_income",
    "form 16": "payslip_income",
    "arrears": "payslip_income",
    # statutory
    "pf": "statutory_deductions",
    "epf": "statutory_deductions",
    "pf eps": "statutory_deductions",
    "esi": "statutory_deductions",
    "pt": "statutory_deductions",
    "professional tax": "statutory_deductions",
    "tds": "statutory_deductions",
    "tax deduction": "statutory_deductions",
    "gratuity": "statutory_deductions",
    "statutory": "statutory_deductions",
    "deductions": "statutory_deductions",
    "leave encashment": "statutory_deductions",
    # wps / payments
    "wps": "wps_payments",
    "sif": "wps_payments",
    "wps file": "wps_payments",
    "bank file": "wps_payments",
    "salary transfer": "wps_payments",
    "payment file": "wps_payments",
    # access
    "login": "access_login",
    "password": "access_login",
    "access": "access_login",
    "sso": "access_login",
    "account locked": "access_login",
    "user access": "access_login",
    "permission": "access_login",
    # billing / refunds
    "billing": "billing_refunds",
    "invoice": "billing_refunds",
    "refund": "billing_refunds",
    "credit note": "billing_refunds",
    "payment issue": "billing_refunds",
    "subscription": "billing_refunds",
    # how-to
    "how to": "how_to",
    "how-to": "how_to",
    "guide": "how_to",
    "setup": "how_to",
    "configuration": "how_to",
    "faq": "how_to",
    # status inquiry — bot-answerable "where is it" questions
    "status": "status_inquiry",
    "status check": "status_inquiry",
    "status inquiry": "status_inquiry",
    "track status": "status_inquiry",
    "ticket status": "status_inquiry",
    "run status check": "status_inquiry",
    "where is my": "status_inquiry",
    # integrations
    "integration": "integrations",
    "api": "integrations",
    "webhook": "integrations",
    "sync issue": "integrations",
    "erp": "integrations",
    "tally": "integrations",
    "zoho": "integrations",
}

_NON_ALNUM = re.compile(r"[^a-z0-9]+")

# Fallback keyword inference when no alias matches — ordered by
# specificity so statutory/payroll terms win over generic ones.
_KEYWORD_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("status_inquiry", ("status of", "status check", "track my", "where is my", "any update on")),
    ("statutory_deductions", ("pf", "epf", "esi", "tds", "gratuity", "professional tax", "deduction")),
    ("wps_payments", ("wps", "sif", "bank file", "salary transfer")),
    ("payslip_income", ("payslip", "salary slip", "form 16", "income")),
    ("payroll_run", ("payroll", "pay run", "salary run", "pay cycle")),
    ("access_login", ("login", "password", "sso", "locked", "permission")),
    ("billing_refunds", ("invoice", "refund", "billing", "credit note", "subscription")),
    ("how_to", ("how do", "how to", "steps to", "guide")),
    ("integrations", ("api", "integration", "webhook", "tally", "zoho", "sync")),
)


def _norm(raw: str) -> str:
    return _NON_ALNUM.sub(" ", raw.lower()).strip()


def normalize_category(raw: str | None, *, text: str = "") -> str:
    """Map a raw category label (or ticket text) to a canonical category.

    Explicit labels win via the alias table; otherwise keyword inference
    over the text; ``other`` is the honest fallback — never guessed beyond
    the keyword evidence.
    """
    if raw:
        key = _norm(raw)
        if key in _ALIASES:
            return _ALIASES[key]
        # Partial alias match for compound labels like "PF / EPF Queries".
        for alias, canon in _ALIASES.items():
            if alias in key:
                return canon
    haystack = _norm(text)
    for canon, keywords in _KEYWORD_RULES:
        if any(k in haystack for k in keywords):
            return canon
    return "other"
