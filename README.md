# DEM — Deus Ex Machina

An open-source, self-hostable pipeline that turns a [Plane](https://plane.so) epic into approved pull requests using Claude Code agents. Write an epic in Plane; a planner agent breaks it into tickets; engineer agents build each ticket in an isolated container and open PRs; reviewer and QA agents critique the work and loop feedback back to the engineer until both pass; a human approves and merges.

The human touchpoints are exactly two: **writing the epic** and **approving the PR**.

> Full specification and build plan: [`docs/PLAN.md`](docs/PLAN.md).

## Status

Under active construction, phase by phase per the plan. Not yet usable end to end.

## Quickstart (dev)

```bash
make setup      # sync deps, install pre-commit hooks
make dev        # docker compose up (conductor at :8420)
make test
make lint
```

`GET /health` returns `{"status": "ok"}`.

## Contributing

Changes follow the branch → PR workflow; `main` is protected. Squash merges only.

## License

MIT
