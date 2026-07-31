# Displacement Desk — scheduled ICT scanner

Runs as a GitHub Action every ~15 minutes: scans for 15m displacement + FVG
setups (confirmed by H4 context), tracks open paper trades, and rewrites a
static dashboard at `docs/index.html`. No server to keep alive — GitHub runs
it for you.

## Setup (one time)

1. **Create a new GitHub repo** — must be **public** for unlimited free
   Actions minutes. Upload all the files in this folder, keeping the folder
   structure (`.github/workflows/scan.yml` has to stay at that exact path).

2. **Enable GitHub Pages**
   - Repo → Settings → Pages
   - Source: "Deploy from a branch"
   - Branch: `main`, folder: `/docs`
   - Save

3. **Trigger the first run**
   - Repo → Actions tab → "ICT Scan" workflow → "Run workflow" (this uses
     the `workflow_dispatch` trigger, no need to wait for the schedule)
   - After it finishes, your dashboard is live at:
     `https://<your-username>.github.io/<your-repo-name>/`

That's it — after the first run, the scheduled trigger takes over and it
keeps updating every 15 minutes on its own, with your phone or any device
fully offline.

## Notes

- `state.json` holds open positions between runs — don't edit it by hand.
- `trading_journal.csv` is the full trade history; it's what the dashboard
  reads for win-rate stats.
- Because the repo is public (required for free minutes), the journal and
  dashboard are publicly viewable. Fine for paper trading — just don't add
  real API keys or credentials to this repo.
- GitHub's schedule isn't second-precise; runs can be delayed a few minutes
  during platform load, same as the ~15 min gap you already had.
