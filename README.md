# Upwork MCP Server

MCP (Model Context Protocol) server for Upwork via browser automation — search jobs, read your profile / connects / proposals, and (unverified) manage proposals, messages, and contracts from an MCP client such as Claude Code.

> **Fork.** Based on [vanooo/upwork-mcp](https://github.com/vanooo/upwork-mcp) by Ivan Solskiy. This fork adds real Upwork search-filter passthrough (competition, verified-payment, budget/rate floors, sorting) and rewrites several read tools whose scrapers had drifted against Upwork's current markup (they were returning `{}` or the wrong element). See **[Tool status](#tool-status)** for exactly what is verified vs. untested — nothing here is claimed to work that hasn't been run.

## Job search filters

`upwork_search_jobs` passes through Upwork's real search-URL grammar (the previous version silently dropped budget/category and hardcoded the sort order):

| Param | Effect |
|-------|--------|
| `query` | keywords (supports `AND`/`OR`/`NOT` and `"quoted phrases"`) |
| `sort` | `recency` (newest) or `relevance` |
| `job_type` | `hourly`, `fixed`, or `both` |
| `experience_level` | `entry`, `intermediate`, `expert` |
| `budget_min` / `budget_max` | fixed-price budget range (USD) |
| `hourly_min` | minimum hourly rate (USD/hr) |
| `payment_verified` | only clients with a verified payment method |
| `low_competition` | only jobs with fewer than 10 proposals so far |
| `hired_before` | only clients who have hired at least once |
| `location` | client location, e.g. `"United States"` |
| `limit` | max results (1–50) |

Every result carries the **proposal count** (competition) when Upwork exposes it — the key signal for finding winnable jobs.

## Tool status

Honest state as of this fork (browser-scraping tools drift when Upwork changes its markup):

- **Verified working — read-only, tested live:** `upwork_check_session`, `upwork_search_jobs`, `upwork_get_job_details`, `upwork_get_connects_balance`, `upwork_get_my_profile`, `upwork_get_profile_stats`, `upwork_get_proposals`
- **Present but NOT tested — they take real, billable or outward actions, so exercise deliberately:** `upwork_submit_proposal`, `upwork_send_message`, `upwork_withdraw_proposal`
- **Not yet verified against current Upwork markup (may need the same text-parsing fix):** `upwork_get_proposal_details`, `upwork_get_messages`, `upwork_get_conversation`, `upwork_get_unread_count`, `upwork_get_contracts`, `upwork_get_contract_details`, `upwork_get_work_diary`

## How It Works

This MCP uses **Chrome DevTools Protocol (CDP)** to connect to your real Chrome browser. This approach:
- Bypasses Cloudflare's "automated test software" detection
- Uses your real browser profile with history and cookies
- Requires Chrome to be running with debug port enabled

## Installation

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) package manager
- Google Chrome browser

### Install from source

```bash
cd upwork-mcp
uv sync
```

## Authentication

The server connects to Chrome via CDP (Chrome DevTools Protocol).

### First-time setup

```bash
# Start login flow - opens Chrome with debug port
uv run upwork-mcp --login
```

This will:
1. Start Chrome with `--remote-debugging-port=9222`
2. Navigate to Upwork login page
3. Wait for you to complete login (click Cloudflare checkbox, enter credentials)
4. Save session to `~/.upwork-mcp/chrome-profile/`

### Check session status

```bash
uv run upwork-mcp --check
```

### Clear session

```bash
uv run upwork-mcp --logout
```

## Usage

### With Claude Code (local development)

Add to your MCP settings (`~/.config/claude-code/settings.json` or workspace settings):

```json
{
  "mcpServers": {
    "upwork": {
      "command": "uv",
      "args": ["--directory", "/path/to/upwork-mcp", "run", "upwork-mcp"]
    }
  }
}
```

### Available Tools

Status legend: ✅ verified live · ⚠️ present, untested (real action) · ❓ not verified against current markup.

| Tool | Description | Status |
|------|-------------|--------|
| `upwork_search_jobs` | Search for jobs (see filters above) | ✅ |
| `upwork_get_job_details` | Get detailed job information | ✅ |
| `upwork_get_my_profile` | Get your freelancer profile | ✅ |
| `upwork_get_connects_balance` | Get connects balance + recent history | ✅ |
| `upwork_get_profile_stats` | Get earnings / work-history stats | ✅ |
| `upwork_get_proposals` | Get your submitted proposals | ✅ |
| `upwork_check_session` | Check if session is valid | ✅ |
| `upwork_submit_proposal` | Submit a proposal to a job | ⚠️ |
| `upwork_send_message` | Send a message | ⚠️ |
| `upwork_withdraw_proposal` | Withdraw a submitted proposal | ⚠️ |
| `upwork_get_proposal_details` | Get details of a specific proposal | ❓ |
| `upwork_get_messages` | Get inbox conversations | ❓ |
| `upwork_get_conversation` | Get messages in a conversation | ❓ |
| `upwork_get_unread_count` | Get unread message count | ❓ |
| `upwork_get_contracts` | Get your contracts | ❓ |
| `upwork_get_contract_details` | Get contract details | ❓ |
| `upwork_get_work_diary` | Get work diary entries | ❓ |
| `upwork_close_session` | Close browser and cleanup | ❓ |

## Examples

### Search for Python developer jobs

```
Search for Python developer jobs on Upwork with budget over $1000
```

### Get job details

```
Get details for this Upwork job: https://www.upwork.com/jobs/~01234567890
```

### Check proposals

```
Show my active proposals on Upwork
```

### Read messages

```
Check my Upwork messages
```

## CLI Options

```bash
upwork-mcp [OPTIONS]

Options:
  --login        Open browser for manual login
  --check        Check if session is valid
  --logout       Clear saved session
  --no-headless  Show browser window (debugging)
  --timeout MS   Page timeout in milliseconds (default: 30000)
  --transport    MCP transport type (default: stdio)
```

## Development

### Project Structure

```
upwork-mcp/
├── pyproject.toml
├── README.md
├── src/upwork_mcp/
│   ├── __init__.py
│   ├── server.py           # MCP server entry point
│   ├── browser/
│   │   ├── client.py       # Patchright browser wrapper
│   │   └── auth.py         # Login flow
│   ├── tools/
│   │   ├── jobs.py         # Job search and details
│   │   ├── profile.py      # Profile and connects
│   │   ├── proposals.py    # Proposal management
│   │   ├── messages.py     # Messaging
│   │   └── contracts.py    # Contract management
│   └── utils/
│       ├── config.py       # Configuration
│       └── logging.py      # Logging setup
├── tests/
└── scripts/
    └── test_all.py
```

### Running tests

```bash
uv run python scripts/test_all.py
```

## Session Storage

Session data is stored in `~/.upwork-mcp/profile/`. This includes browser cookies and local storage that persist your Upwork login.

## Troubleshooting

### Session expired

```bash
# Re-authenticate
uvx upwork-mcp --login
```

### CAPTCHA or Cloudflare challenge

Run with visible browser to solve manually:

```bash
uvx upwork-mcp --no-headless
```

### Browser not found

```bash
# Install Chromium for Patchright
uvx patchright install chromium
```

## License

Apache 2.0
