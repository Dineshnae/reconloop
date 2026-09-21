"""Create or update the Hugging Face Space that hosts the ReconLoop demo.

    pip install -U huggingface_hub
    hf auth login                                     # a token with write access
    python scripts/deploy_space.py --space <your-hf-username>/reconloop

Optional extras:

    --repo-url https://github.com/<you>/reconloop     # shown in the app header
    --anthropic-key-from-env                          # copies ANTHROPIC_API_KEY in as a Space secret
    --call-budget 300                                 # live model calls allowed per Space restart

The Space runs app.py with the config in README.md's front matter.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from huggingface_hub import HfApi

ROOT = Path(__file__).resolve().parents[1]
IGNORE = [
    ".git/*", ".github/*", "**/__pycache__/*", "*.pyc", ".pytest_cache/*", "*.egg-info/*",
    ".venv/*", ".env", ".cache/*", "out/*", ".gate/*", "bench/data/*", "bench-claude/data/*",
]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Deploy the ReconLoop demo to a Hugging Face Space.")
    ap.add_argument("--space", required=True, help="username/space-name")
    ap.add_argument("--repo-url", help="GitHub URL to show in the app")
    ap.add_argument("--anthropic-key-from-env", action="store_true",
                    help="copy ANTHROPIC_API_KEY from this shell into the Space as a secret")
    ap.add_argument("--call-budget", type=int, default=300, help="live model calls per Space restart")
    ap.add_argument("--calls-per-run", type=int, default=25, help="live model calls per reconciliation")
    ap.add_argument("--private", action="store_true", help="create the Space private")
    args = ap.parse_args(argv)

    key = ""
    if args.anthropic_key_from_env:                      # fail before creating anything
        key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not key:
            print("error: ANTHROPIC_API_KEY is not set in this shell", file=sys.stderr)
            return 2

    api = HfApi()
    api.create_repo(repo_id=args.space, repo_type="space", space_sdk="gradio",
                    private=args.private, exist_ok=True)
    print(f"Space ready: https://huggingface.co/spaces/{args.space}")

    if args.repo_url:
        api.add_space_variable(repo_id=args.space, key="RECONLOOP_REPO_URL", value=args.repo_url)
    if key:
        api.add_space_secret(repo_id=args.space, key="ANTHROPIC_API_KEY", value=key)
        api.add_space_variable(repo_id=args.space, key="RECONLOOP_DEMO_CALL_BUDGET", value=str(args.call_budget))
        api.add_space_variable(repo_id=args.space, key="RECONLOOP_DEMO_CALLS_PER_RUN", value=str(args.calls_per_run))
        print(f"Claude tie-breaker enabled, capped at {args.call_budget} calls per restart "
              f"and {args.calls_per_run} per run.")

    api.upload_folder(repo_id=args.space, repo_type="space", folder_path=str(ROOT),
                      ignore_patterns=IGNORE, commit_message="Deploy ReconLoop demo")
    print("Uploaded. First build takes a few minutes; watch the Logs tab.")

    if hasattr(api, "wait_for_space"):
        try:
            runtime = api.wait_for_space(repo_id=args.space, timeout=900)
            print(f"Stage: {getattr(runtime, 'stage', 'unknown')}")
        except Exception as exc:  # noqa: BLE001 - the upload already succeeded
            print(f"Could not wait for the build ({exc}). Check the Space page.", file=sys.stderr)
    print(f"App: https://huggingface.co/spaces/{args.space}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
