"""The conformance suite: what "correct" means, independently of language.

These checks are what makes a rebuild verifiable rather than trusted, and what
a second vendor will later run against itself. They reach the unit only
through its control plane, never through in-process object graphs, so nothing
here assumes the implementation is written in Python.
"""
