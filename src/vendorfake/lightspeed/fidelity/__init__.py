"""Lightspeed's fidelity declaration and the documents cut from it -- data only.

The specification is Apache 2.0 licensed, so ``extract.json`` is committed here;
it and ``pin.json`` are written by the fidelity tooling, never by hand, and
``declaration.json`` is the hand-written declaration. ``error_schema`` is
``PaymentErrorResponse``, the specification's only error schema, generalised in
``model/error.py``. ``annotations`` lifts each route's required scope out of its
description text. The document is single-line JSON despite its ``.yaml`` extension.
"""
