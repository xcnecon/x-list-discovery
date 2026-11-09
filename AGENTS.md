# x-list-discovery

This is a standalone Python CLI with two outputs: accounts followed by many list
members but outside the original list, and a separate underfollowed ranking.

- Keep the list's members, voting seeds, and optional target exclusions separate.
- Exclude every original member by stable ID and case-insensitive handle in both modes.
- Count one vote per distinct source. Popularity never affects co-follow eligibility or ties.
- Use follower ceilings and the documented smoothing score only in gems mode.
- Default to all supplied members as seeds; personal-account filtering is explicit.
- Keep credentials and real user graphs out of source, examples, archives, and logs.
- Never run paid API requests in tests. Use temporary workspaces and fake responses.
- Preserve per-source checkpoints, source coverage, interrupted/failed fetch state,
  atomic writes, freshness checks, and workspace isolation.
- All Excel formulas are prohibited in exports. Normal web hyperlinks are allowed;
  cross-workbook links are prohibited.
- Keep documentation and CLI platform neutral; no personal file paths or research data.

Run `python -m unittest discover -s tests -v` and `python -m build` before release.
