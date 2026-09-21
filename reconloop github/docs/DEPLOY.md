# Deploying the demo

There are two versions of the demo. Both run the same engine code.

| | Browser version (`site/`) | Server version (`app.py`) |
|---|---|---|
| Where Python runs | the visitor's browser, via Pyodide (WebAssembly) | a Hugging Face Space |
| Hugging Face plan | **free** (Static Space) | Gradio Spaces now need PRO |
| Claude tie-breaker | not offered (it would put an API key in public page code) | optional, budget-capped |
| Uploaded CSVs | never leave the visitor's machine | sent to the Space |
| First load | 5 to 15 s to fetch the ~12 MB Python runtime, then instant | cold start if the Space slept |

## Browser version on a free Static Space

1. Build it (already built in `site/`; rebuild after changing the engine):

   ```bash
   python scripts/build_static.py --author "Your Name" --repo-url https://github.com/<you>/reconloop
   ```

2. On huggingface.co: **New Space**, SDK **Static**, template **Blank**, **Public**. Create it.
3. **Files → Add file → Upload files**: drag in `site/index.html` and `site/README.md`, and commit. They replace
   the template's files. The template's `style.css` can stay or go; the page does not use it.
4. Open the **App** tab. The status line turns green once the Python engine has loaded.

The same two files work on any static host: GitHub Pages, Netlify, Vercel or your own domain.

## Server version (needs Hugging Face PRO)

### The fast way

```bash
pip install -U huggingface_hub
hf auth login                                        # paste a token with write access
python scripts/deploy_space.py --space <your-hf-username>/reconloop \
    --repo-url https://github.com/<you>/reconloop
```

The script creates the Space if it does not exist, uploads the repo (skipping caches, `out/` and generated
benchmark data), and waits for the build. The first build takes a few minutes; watch the **Logs** tab.

### Without the CLI

1. On huggingface.co: **New → Space**. Name it `reconloop`, pick **Gradio**, **CPU basic (free)**, public.
2. Open the **Files** tab → **Add file → Upload files**, and drag in the whole repo: `app.py`,
   `requirements.txt`, `README.md`, and the `reconloop/`, `data/`, `bench/` and `docs/` folders.
3. The Space builds itself. Watch **Logs**.

### Turning on the Claude tie-breaker (optional)

Without a key, the demo runs rules, the greedy baseline and the oracle. All three are instant and free, and
the page says plainly that Claude is switched off. To enable it, add these under **Settings → Variables and
secrets**:

| Name | Kind | Value | What it does |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | secret | your key | turns the Claude option on |
| `RECONLOOP_DEMO_CALL_BUDGET` | variable | `300` | live calls allowed per Space restart |
| `RECONLOOP_DEMO_CALLS_PER_RUN` | variable | `25` | live calls allowed per reconciliation |
| `RECONLOOP_MODEL` | variable | `claude-sonnet-5` | model id (this is the default) |
| `RECONLOOP_REPO_URL` | variable | your GitHub URL | adds a source link to the app header |

Or pass `--anthropic-key-from-env` to the deploy script and it sets all of these for you.

Budget maths: one reconciliation asks the model about 5 to 9 payments, at roughly 650 tokens of prompt
each. A 300-call budget covers about 40 visitor runs per restart. Past the cap, cases go to the review
queue with the reason recorded, which is the same path as any model failure, so the demo degrades honestly
instead of breaking.

## Before you send the link

- **Wake it up.** A free Space sleeps after 48 hours without a visit, and a cold start makes a reviewer
  wait. Open it yourself shortly before you submit.
- **Check it in a private window,** logged out, to confirm it is public.
- **Click through once:** run a hard batch on rules only (0 wrong links), then the greedy baseline on the
  same seed (wrong links appear), then open the report.
- **Upload tab:** try the sample batch zip so you know the path works.

## If the Space will not build

| Symptom | Fix |
|---|---|
| `ModuleNotFoundError: reconloop` | the `reconloop/` folder did not upload; re-upload it next to `app.py` |
| Build fails installing Gradio | the `sdk_version` in README front matter must exist on PyPI; 6.27.0 is what this repo was tested on |
| "No application file" | `app_file: app.py` must be in the front matter, and the file at the repo root |
| Free CPU quota errors | your account may be at its Spaces limit; delete an old Space, or fall back to the two-minute screen recording the form accepts |
| Space is stuck "Building" | Settings → **Factory rebuild** |

## Running it locally

```bash
pip install -e ".[app]"
python app.py            # http://127.0.0.1:7860
```

Same code as the Space, so if it works here it works there.
