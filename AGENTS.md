# AGENTS.md

Read [CLAUDE.md](CLAUDE.md) first. It is the single source of guidance for this
repository and applies to every coding agent, not just Claude Code.

It covers, in order:

- what this project builds and where the design reasoning lives
- **current status** — nothing has run against live BigQuery yet, and what is
  left to change that
- **traps that have already cost time**, including a dependency that looks unused
  but is load-bearing, and imports that must stay lazy
- the parts of the SQL and the DuckDB usage that are easy to get wrong
- commands, including why the test count differs by dependency group
- commit and lint conventions

If you are about to remove something because it looks unused, check that traps
section before you do.
