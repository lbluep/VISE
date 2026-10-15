"""version.py - the single source of truth for the Vise release stamp.

Bump this when cutting a release. `init/setup.sh` records it in
`.vise/version`; on the next run a mismatch means the install was upgraded
(extracted over, pulled, ...) and `core/baseline.py upgrade` runs to refresh
baselines safely. Adapter baselines and snapshots carry the stamp so
cross-version restores can be refused instead of silently rolling an adapter
back to bytes from an older Vise.
"""

VERSION = "0.1.0"
