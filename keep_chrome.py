#!/usr/bin/env python
"""Hold the one Chrome open. Run by supervisor in the container.

Everything else attaches to this over CDP -- see the note in la/browser.attach
about why there is exactly one browser process.
"""
from la import browser, db

if __name__ == "__main__":
    db.init()
    browser.serve_forever()
