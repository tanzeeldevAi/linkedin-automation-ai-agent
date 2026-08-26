"""linkedin-agent -- runs your LinkedIn presence at human pace.

Pipeline:  learn -> discover -> score -> write -> act -> track

Everything the agent is allowed to do is capped in limits.py, and every single
action it takes is written to the actions table so the dashboard can show the
truth rather than a summary.
"""

__version__ = "1.0.0"
