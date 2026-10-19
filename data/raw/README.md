# Raw source files (pinned by hash)

Each import spec (`data/imports/*.toml`) pins its raw files by SHA-256; `ecobackfire datasets import` and
`ecobackfire datasets validate` refuse a file whose hash differs.

Layout: `data/raw/<dataset_id>/<release_version>/`. Files here are **verbatim** excerpts of upstream files (header +
selected lines, byte-identical), plus a stdlib script that recreates them from a pinned upstream checkout. Nothing is
parsed, rounded or converted before it is stored here. Large upstream files are not committed; their full-file hash and
upstream commit are recorded in the release metadata.
