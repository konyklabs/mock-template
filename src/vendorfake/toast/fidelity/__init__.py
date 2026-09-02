"""Toast's fidelity declaration -- data only, and no upstream bytes.

``declaration.json`` names Toast's seven published specification files and
``vendored: false``: under Toast's API terms a copy of those files, even a
structural extract, does not belong in a public repository (recorded on
konyklabs/roadmap#56). So only ``pin.json`` -- the sha256, size, version and
fetch date of each upstream file, facts rather than copies -- ships here; the
extract is cut at run time from a fresh fetch into the local cache. ``corpus/``
holds the documented-behaviour cases. Nothing in this package is code.
"""
