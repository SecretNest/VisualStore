"""Result sinks: how a compute result becomes database rows.

Compute children return plain data (design §3). The scheduler calls the sink for the job's type
on the event-loop thread with the registry connection, so all database writes stay on one thread.
Milestone 2 registers `fingerprint`; milestone 3 adds `match`, `rebuild_index`, `eval`.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

ResultSink = Callable[[sqlite3.Connection, str, dict, dict], None]

RESULT_SINKS: dict[str, ResultSink] = {}
