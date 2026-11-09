# GetHired

GetHired is a job hunting pipeline. It pulls fresh roles from LinkedIn, reads each one against your CV, throws away the ones that don't fit, and tells you why the rest do. Each match also comes with a short gap analysis: what you're missing, what you could pick up quickly, and what you already have that the posting doesn't spell out.

For a while this ran as a hosted product called **NullToOffer** on DigitalOcean, with a web dashboard and daily emails. That deployment is gone. What's left in this repo is the engine underneath it, and it's built to be cloned, changed and run on your own machine.

---

## Why not just connect Apify to Claude?

It's a fair question. You can hook Apify up to Claude, ask it to scrape LinkedIn and tell it to find jobs that fit your CV. I tried that first, and it's the reason this repo exists. It works for ten jobs. It falls apart at a few hundred, and a proper search returns a few hundred every day.

**A chat reads everything at once. This reads one job at a time.** Twenty search terms at 30 listings each, plus posts, can mean 600+ postings with full descriptions. Put all of that in one conversation and the model has to hold your CV and hundreds of job descriptions in context together. It starts skimming, judges the 300th job less carefully than the 3rd, and quietly skips some. Here, every job gets its own call with only your CV and that one posting, so job #400 gets the same attention as job #1.

**The rules stay the same every time.** The location, visa and experience rules are built into the prompt in code. A role in the wrong city always scores 3 or below. An overqualified match always loses 2 points. The model replies in a fixed format, and the score is parsed as a number and checked against the threshold in Python. In a chat you'd retype those rules every session, the model would apply them a bit differently each time, and "only show me 7+" depends on it remembering to.

**Most of the filtering doesn't need a model at all.** Stale postings, jobs you've already seen, titles that don't match your roles and posts from journalists are all removed by plain code before Claude sees anything. You only pay for the jobs that are actually in the running. In a chat, every one of those passes through an expensive model.

**It remembers what it already showed you.** Every scored job is stored per user in MongoDB and skipped on the next run. A chat starts from nothing each time, so tomorrow you'd get yesterday's jobs again, and pay to have them scored again.

**It uses a cheap model for a repetitive job.** Scoring one posting against one CV doesn't need the biggest model. Haiku does it well for a fraction of the cost. A chat session usually means a top-tier model churning through a huge context, which costs far more per job.

**It runs while you sleep.** A cron job or GitHub Action starts it every morning and the results are waiting for you. With a chat, nothing happens until you open it and ask.

**The output is data, not a paragraph.** Each match is saved with a score, a reason, a sponsor likelihood and lists of gaps and strengths. You can sort it, filter it, build a dashboard on it or track applications. A chat gives you prose you then have to copy somewhere.

|  | Apify + Claude chat | GetHired |
|---|---|---|
| 500 jobs | Crammed into one context, quality drops | 500 separate, focused calls |
| Scoring rules | Retyped each time, applied loosely | Written in code, applied every time |
| Threshold | The model's call | `score >= 7` in Python |
| Stale, duplicate and irrelevant jobs | Sent through the model | Filtered out first in code |
| Yesterday's jobs | Shown and paid for again | Skipped |
| Model | Usually a large one | Haiku, one job per call |
| Running it | You open a chat and ask | Runs on a schedule |
| Output | Text | Structured records |
| Several people | One chat each | One run, separate profiles |

Chatting with Claude is still the better tool for one thing: talking through a specific role once you've found it. GetHired does the finding. Use the chat for what comes after.

---

## What it actually does

Most job boards match on keywords. That's how you end up with "Senior Staff ML Engineer, 12+ years" in a graduate's inbox, or a perfect role in a city you can't work in. GetHired treats every posting the way a recruiter reading your CV would:

1. **Scrapes two sources.** Standard LinkedIn job listings, plus LinkedIn *posts*: the "we're hiring, DM me" updates recruiters and founders write. Those roles often never reach a job board.
2. **Cleans up the posts.** Claude reads each post and decides whether it's a real hiring call or just someone talking about hiring. Journalists, newsletters and reshares get dropped. If the model call fails, a regex fallback pulls out the title, company, location and salary.
3. **Drops stale roles.** Anything posted more than 30 days ago is removed.
4. **Scores each job from 1 to 10** against your CV, following three sets of rules:
   - **Location:** a job outside your preferred locations (and not remote) scores 3 or lower, however strong the technical fit.
   - **Visa / sponsorship:** depending on your status, it flags whether the employer is likely to sponsor, and can take points off when that looks unlikely.
   - **Experience:** a role aimed at people much more or much less experienced than you loses 2 points, and the reason says so.
5. **Keeps scores of 7 and above.** Everything else is thrown away.
6. **Runs a gap analysis** on each survivor, with up to 3 items per section:
   - 🔴 **Hard gaps:** things you don't have that the role needs
   - 🟡 **Soft gaps:** things you could close or argue around
   - 🟢 **Hidden strengths:** things on your CV that suit this role but that you might not think to lead with
7. **Saves and notifies.** Results are written to MongoDB, and an email digest goes out.

---

## How the pipeline flows

```
  user profile (CV, roles, locations, visa status)
                 │
                 ▼
   build search terms  ("ML Engineer London", "AI Engineer Remote", ...)
                 │
        ┌────────┴─────────┐
        ▼                  ▼
  LinkedIn Jobs       LinkedIn Posts
  (Apify actor)       (Apify actor)
        │                  │
        │           Claude: is this a real job post?
        │                  │
        └────────┬─────────┘
                 ▼
        30 day age filter
                 ▼
        role keyword filter
                 ▼
     skip jobs already seen by this user
                 ▼
     Claude: score 1 to 10 (location, visa, experience)
                 ▼
            keep score >= 7
                 ▼
     Claude: hard gaps / soft gaps / hidden strengths
                 ▼
        MongoDB  +  email digest
```

In multi-user mode, up to 3 users are processed at once (an `asyncio.Semaphore`). Each user gets their own scrape, so one person's preferences never leak into another's results.

---

## Project layout

```
GetHired/
├── run_local.py                   # Single-user run, writes digest.html
├── main.py                        # Multi-user pipeline (the old hosted version)
├── database.py                    # MongoDB helpers: users, jobs, user_jobs
├── clear_mongo.py                 # Wipes user_jobs so jobs get rescored on the next run
├── core/
│   ├── scorer.py                  # Scoring prompt rules + single-user score_jobs()
│   └── gap_analyser.py            # Single-user analyse_gaps()
├── scrapers/
│   ├── jobs_scraper.py            # LinkedIn job listings via Apify
│   └── linkedin_posts_scraper.py  # LinkedIn hiring posts via Apify + Claude extraction
├── emailer/
│   └── digest.py                  # Full HTML/plain text digest with gaps per job
├── data/
│   └── my_cv.txt                  # Your CV for single-user mode (you create this, it's gitignored)
└── .github/workflows/
    └── daily_scan.yml             # Optional: run main.py every day at 08:00 UTC
```

---

## Running it locally

You need Python 3.10 or newer (CI used 3.11), plus API keys for:

| Service | What it's for | Needed? |
|---|---|---|
| [Anthropic](https://console.anthropic.com/) | Scoring, gap analysis, post extraction | Yes |
| [Apify](https://apify.com/) | Running the LinkedIn scraping actors | Yes |
| [MongoDB](https://www.mongodb.com/atlas) | Storing users and results | Multi-user mode only |
| [Resend](https://resend.com/) | Sending email | Only if you want email |
| [Clerk](https://clerk.com/) | Looking up user emails | Multi-user mode only, optional |
| [Sentry](https://sentry.io/) | Error tracking | No, it stays off without a DSN |

### 1. Install

```bash
git clone <this repo>
cd GetHired
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Create a `.env`

```bash
cp .env.example .env
```

Fill in `ANTHROPIC_API_KEY` and `APIFY_API_TOKEN` at minimum. Everything else in the file is only needed for multi-user mode or email.

From here you have two options.

### Option A: just for you (no database)

This is the simplest setup. `run_local.py` uses the single-user functions in `core/` and `emailer/`, which read your CV from `data/my_cv.txt` and never touch MongoDB.

1. Create `data/my_cv.txt` and paste your CV into it as plain text. It's gitignored, so it won't end up in a commit.
2. Open `core/scorer.py` and edit the line in `score_jobs()` that lists target roles (`"ML Engineer, AI Engineer, NLP Engineer, ..."`). It's hardcoded for the original user.
3. Edit the settings at the top of `run_local.py`:

```python
SEARCH_TERMS = ["ML Engineer London", "AI Engineer Remote"]
LOCATIONS = ["London", "Remote"]
VISA_STATUS = "UK Citizen / Indefinite Leave to Remain"
USE_POSTS = True       # also scrape LinkedIn hiring posts
SEND_EMAIL = False     # True sends the digest through Resend
OUTPUT_FILE = "digest.html"
```

4. Run `python run_local.py` and open `digest.html` in a browser.

If you set `SEND_EMAIL = True`, the digest is sent from and to `FROM_EMAIL`, so that address has to be on a domain you've verified in Resend.

This mode keeps no record of what it has already shown you, so the same job can come back the next day. If that bothers you, store seen URLs in a JSON file, or move to Option B.

### Option B: the full multi-user pipeline

This is how the hosted version worked. Each person is a document in the `users` collection of a MongoDB database called `nulltooffer`.

**Insert a user:**

```python
from dotenv import load_dotenv; load_dotenv()
from database import get_db

get_db().users.insert_one({
    "clerk_user_id": "local_me",          # any unique id works
    "cv_text": open("data/my_cv.txt", encoding="utf-8").read(),
    "target_roles": ["ML Engineer", "AI Engineer"],
    "locations": ["London", "Remote"],
    "visa_status": "Require Sponsorship from employer",
    "preferred_sources": ["linkedin_jobs", "linkedin_posts"],
    "subscription_tier": "pro",           # skips the free trial gate
})
```

**Run it:**

```bash
python main.py
```

Matches end up in two collections:

- `jobs`: one document per posting (title, company, description, recruiter, company details, salary and so on), keyed by URL
- `user_jobs`: one document per user and job, holding `score`, `reason`, `sponsor_likelihood`, `hard_gaps`, `soft_gaps`, `hidden_strengths`, plus `status` and `is_saved` fields for a front end to use

Because `user_jobs` is checked before scoring, nobody gets scored twice on the same posting. To start over, run `python clear_mongo.py`, which empties `user_jobs` and leaves everything else alone.

The notification email needs `CLERK_SECRET_KEY` to find the user's address. Without it the pipeline still runs and saves results, and just logs that it skipped the email.

**A note on local MongoDB:** `database.py` connects with `tls=True`, which suits Atlas (the free tier is plenty). For a plain `mongod` on localhost, remove that argument.

---

## Making it yours

All the behaviour lives in a handful of constants and prompt builders. These are the ones worth knowing about.

### Thresholds and models

| What | Where | Default |
|---|---|---|
| Minimum score to keep a job | `_SCORE_THRESHOLD` in `main.py`, `SCORE_THRESHOLD` in `core/scorer.py` | `7` |
| Claude model | `_MODEL` / `MODEL` in `main.py`, `core/scorer.py`, `core/gap_analyser.py`, `scrapers/linkedin_posts_scraper.py` | `claude-haiku-4-5-20251001` |
| Max job age | `max_days` in `_is_recent()` in both scrapers | `30` days |
| Listings per search URL | `COUNT_PER_URL` in `jobs_scraper.py` | `30` |
| Posts per search | `MAX_RESULTS` in `linkedin_posts_scraper.py` | `20` |
| Search terms per user | `sorted_terms[:20]` in `_build_search_terms()` | `20` |
| Users processed at once | `asyncio.Semaphore(3)` in `main.py` | `3` |

Haiku was picked because it's cheap and every job means at least one call. Switching to a bigger model will give sharper reasons and gap analysis, at a higher cost per run.

### Cities

Search terms are built as `"<role> <city>"`, then split back apart for each scraper. **A city that isn't in the maps falls back to London**, so add your own before you search for it:

- `_CITY_LOCATION_MAP` in `scrapers/jobs_scraper.py`: city to LinkedIn location string (for example `("Berlin", "Berlin, Germany")`)
- `_CITY_KEYWORD_MAP` in `scrapers/linkedin_posts_scraper.py`: city to the keyword used in post search
- `_LOCATION_CITY_MAP` in `main.py`: normalises messy user input like "London, UK" into "London"

The regex fallback for posts (`_extract_location`) also assumes a UK user. It knows London neighbourhoods and postcodes and treats a list of other countries as "not UK". Change it to suit where you live.

### Visa rules

The sponsorship logic in `_build_sponsor_rules()` (`core/scorer.py`) is written around UK immigration. It matches these exact `visa_status` strings:

| `visa_status` | Behaviour |
|---|---|
| `UK Citizen / Indefinite Leave to Remain` | Sponsorship ignored, output `N/A` |
| `EU Settled / Pre-Settled Status` | Sponsorship ignored, output `N/A` |
| `Require Sponsorship from employer` | Up to 2 points off when sponsorship is unlikely |
| `Skilled Worker Visa (already sponsored)` | Flags whether a new employer would sponsor |
| anything else | Treated as a time-limited visa (Graduate Route, Youth Mobility); likelihood flagged for later |

If you're in another country, rewrite those branches to fit your local rules (H-1B, Blue Card and so on). Each branch returns the prompt text plus the allowed answers, so it's quick to change.

### Scoring and gap prompts

The prompt is put together from three builders in `core/scorer.py`:

- `_build_location_rules(locations)`
- `_build_sponsor_rules(visa_status)`
- `_build_experience_rules()`

Each one returns plain text that gets dropped into the system prompt. To add your own rule (salary floor, company size, no agencies, must be hybrid), write another builder and append its output in `_score_job_for_user()` in `main.py` and `score_jobs()` in `core/scorer.py`. The response parser only reads `Score:`, `Reason:` and `Sponsor Likelihood:` lines, so new rules should change how the score comes out rather than add new output fields, unless you also update `_parse_response()`.

The gap analysis prompt lives in `_analyse_gap_for_job()` (multi-user) and `analyse_gaps()` (single-user). The single-user version adds an explicit experience mismatch check, which is a good template if you want the multi-user one to match.

### Role keywords for posts

When Claude extraction fails, `_infer_title()` falls back to regex, and finally to `_TITLE_KEYWORDS`, which are all ML/AI roles. The last-resort title is `"ML/AI Role"`. If you're hunting for, say, product design roles, change both.

### Sources

The scrapers are plain functions that take a list of search terms and return a list of dicts in a shared shape (`title`, `company`, `location`, `salary`, `description` or `post_text`, `url`, `source`, `type`). Adding another board (Indeed, Wellfound, a company careers page) means writing one function that returns that shape and calling it next to the other two in `process_user()`. Users choose sources through `preferred_sources`.

---

## Scheduling

Without a server, you still have two easy ways to run it every day:

- **GitHub Actions:** `.github/workflows/daily_scan.yml` runs `main.py` at 08:00 UTC and can also be started by hand. Add your keys as repository secrets. It's built for the multi-user mode, so it needs `MONGODB_URI`.
- **Your own machine:** a cron job (`0 8 * * * cd /path/to/GetHired && venv/bin/python run_local.py`) or Windows Task Scheduler pointed at the same command.

---

## Cost

Costs depend on how much you search, but you can estimate them:

- **Apify:** two actor runs per user per day (one for listings, one for posts), billed at whatever rate those actors charge.
- **Anthropic:** one call for each scraped post (extraction), one for each job that reaches scoring, and one more for each job scoring 7 or above (gap analysis).

Narrow searches and the per-user dedup keep this small. Twenty search terms on two sources can easily mean a few hundred scoring calls on the first run, then far fewer later because already-seen jobs are skipped.

---

## Known rough edges

- Scoring and gap logic exist twice: async versions in `main.py` for multi-user runs, and sync versions in `core/` for single-user runs. If you change a prompt, change both.
- The trial and waitlist logic in `process_user()` (free tier gets one run, then a waitlist email) belonged to the hosted product. Set `subscription_tier` to `"pro"` or `"lite"` to bypass it, or delete that block.
- Email templates still carry NullToOffer branding and a `nulltooffer.app` fallback URL.
- Salary from LinkedIn listings is always shown with a `£` sign, whatever the actual currency.
- Scraping LinkedIn is subject to LinkedIn's terms of service. The Apify actors do the scraping; use them responsibly and at a sensible volume.

---

## License

MIT. See [LICENSE](LICENSE). Fork it, change it, run it for yourself or your friends.
