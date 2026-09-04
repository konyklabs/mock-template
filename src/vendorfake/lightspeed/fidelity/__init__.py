"""Lightspeed's fidelity declaration and the documents cut from it -- data only.

``declaration.json`` names the one document this vendor is derived from and
records ``vendored: true``: the specification is published under Apache 2.0
(``info.license`` reads ``{"name": "Apache 2.0", "url":
"http://www.apache.org/licenses/LICENSE-2.0.html"}``), so a structural extract
of it may live in this repository and there is no fetch-never-commit tax the
way there is for Toast (konyklabs/roadmap#56). That is why ``extract.json`` is
committed here and only ``pin.json`` is committed there.

``extract.json`` and ``pin.json`` are cut from the upstream document by
``vendorfake-fidelity pin`` and are never edited by hand -- ``pin --check
--offline`` fails if they are. ``corpus/`` holds thirteen documented-behaviour
cases, each naming the page it was read from. Nothing in this package is code;
see ``vendorfake.fidelity`` and D-006.

TWO THINGS WORTH KNOWING about this vendor's declaration in particular.

``error_schema`` is ``PaymentErrorResponse``, the ONLY error schema in the
specification's 373 components -- scoped to payments, with ``error`` as an
object of ``code`` and ``message``. Lightspeed publishes no error envelope at
all and its documentation site has no error-codes page, so this unit
generalises the one error body the vendor does print verbatim (the
rate-limiting page's 429) and a ``deviation`` records the difference at
``/error``. ``model/error.py`` has the long version.

``annotations`` is what keeps the scope table honest. Lightspeed states the
scope an operation requires as a line of its *description* rather than as an
OAuth2 security scheme, and prose is what the cutter strips; the annotation row
lifts those lines out before the strip and records them per route in the
extract, where ``tests/unit/lightspeed/test_fidelity_scopes.py`` compares them
against the ``scopes=(...)`` on every route the unit serves.

FORMAT NOTE: the document is served as single-line JSON despite its ``.yaml``
filename. It parses with ``json.load`` and with any real YAML parser (JSON is a
YAML subset), so the extractor needs no YAML-only special case.
"""
