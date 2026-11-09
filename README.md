# X List Discovery

Find the people your list pays attention to — before you add them.

[中文说明](README.zh-CN.md) · [Methodology](docs/methodology.md) · [Release guide](docs/releasing.md)

Given an X/Twitter List or a CSV of accounts, this command-line tool produces two independent rankings:

| Output | What it finds | Ranking |
| --- | --- | --- |
| **Co-follow** | Accounts followed by many list members, outside the original list | Number of distinct list sources; username breaks ties |
| **Gems** | Accounts with strong list overlap and relatively few total followers | Configurable follower ceiling and a smoothed overlap score |

Both outputs exclude **every original list member**, including members omitted from a custom voting subset. Co-follow ranking never uses follower counts. Each list member contributes at most one vote to a target.

## Install

Python 3.9+ on macOS, Linux, or Windows:

```bash
python -m pip install git+https://github.com/xcnecon/x-list-discovery.git
```

For development, clone the repository and run `python -m pip install -e '.[dev]'`.

## Try it offline

From a repository checkout, with no API key:

```bash
x-list-discovery init demo --members examples/members.csv
x-list-discovery import-graph demo examples/followings.json
x-list-discovery run demo
```

The example uses fictional accounts and writes both rankings under `runs/demo/output/`:

```text
cofollow.xlsx   cofollow.csv
gems.xlsx       gems.csv
discovery.json
```

Each workbook includes a top preview, all candidates with selection reasons, shared-follow evidence, source coverage, and the exact methodology. CSV contains **all selected accounts**, independent of the preview size.

## Use your list

Fetching uses [twitterapi.io](https://twitterapi.io), a paid third-party data provider. Supply your own key. Copy `.env.example` to `.env` and set `TWITTERAPI_IO_KEY`, or export that variable in your shell.

```bash
# Fetch list membership from a URL or numeric List ID
x-list-discovery init research --list https://x.com/i/lists/123456789

# Or start from a CSV/JSON snapshot (no API call during init)
x-list-discovery init research --members /path/to/members.csv

# Fetch each voting source's following list, then produce both reports
x-list-discovery fetch research
x-list-discovery run research --min-common 5 --max-followers 50000
```

Choose one of the two `init` commands. A workspace name must be unique. `--max-followers` applies only to gems. `run` is offline unless you explicitly add `--fetch`.

Minimal CSV:

```csv
username
alice
bob
```

Supported username columns: `userName`, `username`, `screen_name`. Optional fields include `id`/`user_id`, `name`, `description`, `followers_count`, and `following_count`. IDs are numeric strings, so large X IDs keep their precision. All supplied members vote by default; your own live following list is never silently used as the source.

## Control the results

```bash
# Use a reviewed subset for voting; all original members are still excluded
x-list-discovery init curated --members members.csv --seeds personal-seeds.csv

# Optional profile filters; do not affect the definition of shared-follow counts
x-list-discovery init people --members members.csv --people-only --exclude-bd

# Optionally restrict voters to profiles clearly identifying people
x-list-discovery init personal --members members.csv --personal-seeds --people-only

# Already-followed accounts are target exclusions, never voting sources
x-list-discovery exclude-followings research --account your_handle
x-list-discovery run research

# Reuse the same cached graph with either ranking
x-list-discovery run research --mode cofollow --min-common 5
x-list-discovery run research --mode gems --min-common 5 --max-followers 10000
x-list-discovery status research
```

Profile filters are conservative heuristics. Uncertain identities remain in the audit table. Review decisions can be supplied with `init --reviews examples/profile-reviews.json`, and later edited in the workspace's `input/reviews.json`. See [methodology](docs/methodology.md) for schema and selection details.

## Caching and provenance

Each workspace lives in `runs/<name>/`, with copied inputs, source identities, per-account checkpoints, fetch timestamps, ranking parameters, and report hashes. `fetch` resumes without re-fetching successful sources. `fetch --refresh` archives previous caches; interrupted refreshes resume. Failed or incomplete source requests block aggregation instead of becoming empty successes.

A successfully requested private/unavailable source is recorded as unavailable. Its contribution is unknown, not verified zero. Reports expose those gaps. Input and cache changes invalidate downstream results; `run` reuses a valid aggregate and rebuilds stale ones.

```bash
# Import an existing JSON mapping seed username -> followed profile list
x-list-discovery import-graph research /path/to/followings.json

# All commands support an alternate workspace parent (before the subcommand)
x-list-discovery --root /path/to/workspaces status research
```

Imported graphs must exactly match the workspace's voting sources. No real user data or API keys ship with this repository or its distributions.

## Development

```bash
python -m pip install -e '.[dev]'
python -m unittest discover -s tests -v
python -m build
python -m twine check dist/*
```

CI tests Python 3.9–3.14, plus Windows and macOS, using fake API responses. A tag builds and attaches distributions to a **draft** GitHub release; publishing to PyPI is a separate manual workflow with a configured trusted publisher. See [releasing](docs/releasing.md).

## License

MIT. This project is independent of X and twitterapi.io. Network overlap and low popularity are discovery signals, not proof of content quality.
