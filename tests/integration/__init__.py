"""Out-of-process tests: a real uvicorn server, driven over a real socket.

Nothing in this package imports ``vendorfake``. The point of an integration
test here is that the only thing shared with the code under test is HTTP: a
helper bug in the parent process cannot make the server look correct, which is
the independent-verification the reference got free from having a TypeScript
implementation and a Python consumer suite.
"""
