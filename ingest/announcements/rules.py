"""Headline rules: (1) pure-admin filter, (2) coarse headline category.

Investegate does not expose the RNS headline *code*, so the category is derived from the
headline text. It is a convenience for reporting/filtering only; Phase 2's LLM `type` is the
real classification. Admin rules are deliberately narrow: a headline must be ENTIRELY an admin
phrase to be filtered ("Director Subscription and Total Voting Rights" stays in the main table).
"""
from __future__ import annotations

import re

_PREFIX = re.compile(r"^(replacement|amendment|correction|re-?issue)\s*[:\-–]\s*", re.I)
_SUFFIX = re.compile(
    r"\s*[\-–(:]?\s*(replacement|amendment|amended|correction|corrected|revised)\)?\s*$", re.I)

ADMIN_PATTERNS: list[tuple[str, str]] = [
    ("total_voting_rights", r"(total voting rights|tvr)( and capital)?( update)?"),
    ("block_listing", r"(six monthly |interim |annual )?block ?listing.*"),
    ("form_8", r"form 8(\.\d)?.*|form 38\.5.*"),
    ("holdings", r"(notification of )?(major )?holding\(?s?\)? in company|notification of major holdings?|"
                 r"(tr-?1|tr1)?:? ?(standard form for )?notification of major holdings?"),
    ("own_shares", r"(transactions? in|purchase of|repurchase of|sale of)( its)? own shares|"
                   r"(sale|transfer) of (shares )?from treasury|transaction in own shares and total voting rights"),
    ("meeting_admin", r"(notice of |result(s)? of |posting of |publication of )"
                      r"(the )?(scheme )?(annual |extraordinary |general |court |annual general |)(general )?(meeting|agm|egm|gm)s?"
                      r"( and (.* )?(meeting|agm|egm|gm)s?)?( results)?"),
    ("notice_of_results", r"notice of (final |interim |half[- ]year |full[- ]year |preliminary |q[1-4] |annual )?"
                          r"results( date)?.*|(final |interim |half[- ]year )?results date|"
                          r"notification of .*results( date)?.*"),
    ("annual_report_admin", r"(annual financial report|annual report( and accounts)?|"
                            r"(posting|publication|availability) of (the )?(annual report|circular|report and accounts|"
                            r"prospectus|supplementary prospectus|admission document).*|"
                            r"annual report.*(notice of|and) agm|document(s)? on display|doc re.*)"),
    ("adviser_change", r"(change of|appointment of( joint)?) (registered office|adviser|nominated adviser|nomad|"
                       r"broker|joint broker|corporate broker|company secretary|auditors?|address|"
                       r"financial adviser|registrar|name)s?.*"),
    ("director_declaration", r"director(s)? declaration.*|(rule|aim rule) 17.*"),
    ("rule_2_9", r"rule 2\.9( announcement)?|rule 2\.10( announcement)?|rule 2\.9 and 2\.10.*"),
    ("price_monitoring", r"(second |third )?price monitoring extension"),
    ("options_admin", r"(grant|exercise|vesting|award) of (share )?options.*|exercise of warrants|"
                      r"(employee benefit trust|ebt).*|(issue of equity (re|in respect of|pursuant to) "
                      r"(options|warrants|share options|employee share).*)"),
    ("nav", r"(net asset value|nav)( update)?|weekly nav|monthly nav"),
    ("misc_admin", r"annual information update|statement re (suspension|restoration).*|"
                   r"restoration of trading|lei number|isin.*|capital reorgani[sz]ation timetable|"
                   r"directors? (shareholding )?notification of (a )?change of address"),
]
_ADMIN = [(name, re.compile(rf"^(?:{pat})$", re.I)) for name, pat in ADMIN_PATTERNS]

CATEGORY_PATTERNS: list[tuple[str, str]] = [
    ("takeover", r"offer for|possible offer|rule 2\.[47]|recommended (cash |share )?(offer|acquisition)|"
                 r"scheme of arrangement|formal sale process|strategic review|takeover|approach|"
                 r"firm intention|statement re possible"),
    ("profit_warning", r"profit warning|below (market )?expectations|revised guidance"),
    ("results", r"results|half[- ]?year(ly)? report|interim report|annual report and accounts|prelim|"
                r"interim statement|half[- ]year statement|q[1-4] (report|update)"),
    ("trading_update", r"trading (update|statement)|pre[- ]close|year[- ]end update|business update|"
                       r"ahead of (market )?expectations|in line with (market )?expectations|"
                       r"agm (statement|update|trading)|update on trading"),
    ("fundraise", r"placing|fundrais|subscription|open offer|retail offer|equity raise|bookbuild|"
                  r"conditional raise|issue of equity|broker option|convertible|loan note|capital raise|"
                  r"rights issue|wrm|pricing of"),
    ("director_dealing", r"director.{0,15}(dealing|shareholding|purchase|buy)|pdmr"),
    ("contract_win", r"contract|award|order|framework|tender|partnership|agreement|licen[cs]e|selected|"
                     r"customer win|new customer"),
    ("operational", r"drilling|operations? update|operational update|production|spud|assay|resource|"
                    r"reserves|exploration|clinical|trial|fda|approval|ce mark|launch|patent|well "),
    ("board_change", r"board|directorate|appointment|resignation|\bceo\b|\bcfo\b|chair"),
    ("acquisition_disposal", r"acquisition|disposal|acquire|sale of|merger"),
    ("dividend", r"dividend"),
]
_CAT = [(name, re.compile(pat, re.I)) for name, pat in CATEGORY_PATTERNS]


def clean_headline(h: str) -> str:
    h = re.sub(r"\s+", " ", (h or "").replace("&", " and ")).strip()
    h = _PREFIX.sub("", h)
    h = _SUFFIX.sub("", h)
    return h.strip(" .-–:")


def is_replacement(h: str) -> bool:
    return bool(re.search(r"\b(replacement|amendment|correction)\b", h or "", re.I))


def admin_rule(headline: str) -> str | None:
    """Name of the admin rule the headline fully matches, else None."""
    h = clean_headline(headline)
    for name, rx in _ADMIN:
        if rx.match(h):
            return name
    return None


def headline_category(headline: str) -> str:
    if admin_rule(headline):
        return "admin"
    h = clean_headline(headline)
    for name, rx in _CAT:
        if rx.search(h):
            return name
    return "other"
