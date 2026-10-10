"""The test library: ready-made packs of tests that ship with Quartermaster and are copied into a tests folder.

A pack is a folder here with a `pack.yaml` (its name, what it is for, what the test user needs, and when it was last
checked on a pod) and a `tests` folder of ordinary test files. Installing copies the tests into
`<tests folder>/library/<pack>/`, where they are tests like any other: they can be run, scheduled, edited and put in
suites. A suite for the pack (`library-<pack>`) is made with them.

Installing again is an update that never loses work: a file you have changed is kept as it is, and said so.
"""

from quartermaster.packs import catalog
from quartermaster.packs.catalog import INSTALL_DIR, Pack, install, list_packs, pack_status

__all__ = ["INSTALL_DIR", "Pack", "catalog", "install", "list_packs", "pack_status"]
