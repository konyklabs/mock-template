"""Lightspeed's fidelity declaration -- data only, and no code.

``declaration.json`` names the one document this vendor is derived from and
records ``vendored: true``: the specification is published under Apache 2.0
(``info.license`` reads ``{"name": "Apache 2.0", "url":
"http://www.apache.org/licenses/LICENSE-2.0.html"}``), so a structural extract
of it may live in this repository and there is no fetch-never-commit tax the
way there is for Toast (konyklabs/roadmap#56).

**THIS PACKAGE IS A STUB.** The declaration below names the source and the
licence and nothing else; ``extract.json``, ``pin.json`` and ``corpus/`` are
filled by slice L3 of konyklabs/roadmap#94. Until they exist, running
``vendorfake fidelity pin --check`` or ``report`` against this target reports
the missing extract rather than a false pass, which is why the target is
declared and the files are not faked.

FORMAT NOTE for whoever fills it: the document is served as single-line JSON
despite its ``.yaml`` filename and extension. It parses with ``json.load`` and
with any real YAML parser (JSON is a YAML subset), so the extractor needs no
YAML-only special case.
"""
