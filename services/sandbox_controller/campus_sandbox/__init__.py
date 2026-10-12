"""Sandbox controller (S4, architecture chapter 13): the only place that starts untrusted code, in short-lived, hardened containers.

The API and the workers never import candidate code. A missing or unaccepted sandbox path disables the capability; it is never
replaced by in-process execution or by an instruction to be careful.
"""
