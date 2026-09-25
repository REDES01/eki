# SPDX-License-Identifier: Apache-2.0
"""Never imported: the command line is the package eki/cli/, which Python
finds before this file. Don't add code here — a command is a file of its own
in eki/cli/ (see eki/cli/__init__.py).

The file stays because older copies of eki know a checkout by it: the
candidate check of a build still running older code (eki/candidate.py) and
the Mac app looking for your checkout (mac/Model.swift) both look for
eki/cli.py. Without it, the change that split the command line couldn't go
live, and an app built before it couldn't find the engine.
"""
