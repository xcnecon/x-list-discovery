# Methodology and data contracts

## Three distinct populations

- **Members**: every account in the original list, copied into input/members.csv.
- **Seeds**: voting sources. Default: all members. `--seeds` selects an explicit subset; `--personal-seeds` selects profiles clearly identifying people. These options affect both rankings in the same way.
- **Exclusions**: optional already-followed accounts and owner. These only remove targets. They never replace the list's voting population.

Both rankings exclude every original member and all explicit target exclusions. Membership is matched by stable account ID or case-insensitive username. Each source contributes at most one vote per target, even when duplicate API pages or aliases occur.

## Rankings

Co-follow ranks by distinct source count descending, then lowercase username ascending. It never filters or breaks ties with total followers, including missing or suspect follower values. The default minimum is two distinct sources; change it per run.

Gems applies the same overlap minimum and exclusions, then requires a known total follower count at least as large as the overlap count and strictly below the chosen ceiling (default 50,000). The heuristic score is:

```text
gem_index = round(100 * common_follows / sqrt(total_followers + smoothing), 2)
```

The smoothing default is 1,000. Sort by score descending, then lowercase username. The follower ceiling and smoothing are recorded with each report. These are configurable heuristics; followers are snapshots, not guaranteed live values. Missing or impossible counts remain eligible for co-follow but are omitted from gems.

Without profile filters, organizations and uncertain accounts may appear. `--people-only` keeps identified individual, non-automated profiles; `--exclude-bd` excludes role signals. The full candidate table records non-selected profiles and reasons. These filters do not change how votes are counted. `--all-targets` bypasses optional target profile filters but cannot include original members or already-followed exclusions.

## Source snapshots

The API adapter uses the provider's documented [List members](https://docs.twitterapi.io/api-reference/endpoint/get_list_members) and [following profiles](https://docs.twitterapi.io/api-reference/endpoint/get_user_followings) responses. It checks business status, pagination shape, advancing cursors, and profile identity. A duplicate-only page does not stop pagination when its cursor advances. Exhaustion requires has_next_page=false; page limits and malformed responses are failures.

Successful source caches are resumable per account. Failed/incomplete requests are not empty successes. Private/unavailable empty sources remain coverage gaps; zero is verified only when profile metadata supports it. The source coverage table distinguishes these states. A cached fetch need not represent today's live network.

Offline graph import requires JSON mapping every voting-source username to a profile list, with no extra sources. Imported capture time is the source file's modification time, explicitly labeled. It is not asserted to be the original acquisition time.

## Workspace

```text
runs/<name>/
  config.json
  input/       original snapshot, members, seeds, seed review, exclusions, profile reviews
  state/       per-source caches, fetch status, provenance, archived refreshed data
  data/        complete aggregate and coverage metadata
  rankings/    cofollow/{all,selected}.csv and gems/{all,selected}.csv with fingerprints
  output/      both workbooks, full selected CSVs, validation records, discovery.json
```

Changing inputs, exclusions, source caches, configuration, or profile reviews invalidates the relevant downstream data. Atomic writes and a per-workspace process lock protect checkpoints. Separate workspace names prevent accidental cross-list reuse. Original input files are never modified.

Config supports mode (`both`, `cofollow`, `gems`), min_common, gem_max_followers, gem_smoothing, individuals_only, exclude_bd, and owner. Original member exclusion is mandatory. CLI ranking options affect that run and are persisted in rank metadata, not silently written back to config.

## Profile-review schema

Keys are usernames, case-insensitive when imported. A later --reviews file overrides an earlier review of the same account.

```json
{
  "individuals": {"person": "Manually confirmed individual"},
  "organizations": {"brand": "Official brand account"},
  "bd_accounts": {"salesperson": "Business-development role"},
  "uncertain": {"unknown": "Identity not established"}
}
```

Heuristics are conservative and incomplete. Personal affiliation with a project alone does not prove an organizational account. This product does not inspect content or certify research skill.

## Output safety

Exported strings are text, never formulas. CSV protects formula-like prefixes; Excel XML control characters are removed. Ordinary X hyperlinks are supported. No cross-workbook references are created. Each workbook is reopened and checked for row counts, formulas, and external workbook links before replacing its destination.
