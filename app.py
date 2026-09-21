"""ReconLoop web demo. Entry point for a Hugging Face Space, and runs locally too.

    pip install -e ".[app]"
    python app.py            ->  http://127.0.0.1:7860
"""

from __future__ import annotations

import html
import os
import random

import gradio as gr

from reconloop import io, service

CLAUDE = service.claude_available()
REPO_URL = os.environ.get("RECONLOOP_REPO_URL", "").strip()

SYNTHETIC_CHOICES = ["Rules only", "Greedy baseline", "Oracle ceiling"] + (["Claude"] if CLAUDE else [])
UPLOAD_CHOICES = ["Rules only", "Greedy baseline"] + (["Claude"] if CLAUDE else [])
BENCH_CHOICES = ["Rules only", "Greedy baseline", "Oracle ceiling"]

INTRO = f"""# ReconLoop

Three-way settlement reconciliation for a Razorpay merchant: settlements against the bank statement,
payments against invoices, refunds against credit notes, plus a fee and GST audit. Rules do almost all
of the work. A model is only asked to break ties, and every answer it gives is re-checked before
anything is linked.

Each synthetic batch ships with an answer key, so every run below is scored: how much it linked, how
much it got wrong, and what it handed back to a person.{f" [Source code]({REPO_URL})" if REPO_URL else ""}
"""

TIE_NOTES = """**Rules only** sends anything unclear to a person.
**Greedy baseline** always takes the top candidate and never abstains, so watch its wrong links climb.
**Oracle ceiling** reads the answer key: not a result, just the ceiling a perfect tie-breaker could reach.
""" + (
    "**Claude** makes live calls, capped for this demo, and every pick is verified by the rules."
    if CLAUDE else
    "**Claude** is switched off here because this deployment has no API key, so nothing on this page calls a model."
)

UPLOAD_HELP = """Drop in your own three CSVs. They need the same columns as the sample batch, which you can
download below: Razorpay's settlement recon report (amounts in paise), a bank statement (rupees,
`DD-MM-YYYY`) and your books (invoices and credit notes).

Files stay on this server for about an hour and are then deleted. Use test or anonymised data: this is a
public demo, not a place for real customer records. There is no answer key for your files, so accuracy is
not scored, but you still get the report, the exceptions, the review queue and the proposed actions.
"""

HOW = service.HOW_MD


def _iframe(report_html: str) -> str:
    return (
        f'<iframe title="Reconciliation report" sandbox srcdoc="{html.escape(report_html, quote=True)}" '
        'style="width:100%;height:1000px;border:1px solid #BCD0E6;border-radius:6px;background:#F2F5EE"></iframe>'
    )


def _run(fn, *args):
    try:
        run = fn(*args)
    except service.DemoError as exc:
        raise gr.Error(str(exc)) from None
    except Exception as exc:  # noqa: BLE001 - a demo should not show a stack trace
        raise gr.Error(f"That run failed: {type(exc).__name__}: {exc}") from None
    return service.summary_markdown(run), _iframe(run.report_html), str(run.bundle)


def on_synthetic(difficulty, seed, tie_label):
    return _run(service.run_synthetic, difficulty, seed, service.TIE_BREAKERS[tie_label])


def on_upload(rzp, bank, books, tie_label):
    files = {io.RZP_FILE: rzp, io.BANK_FILE: bank, io.BOOKS_FILE: books}
    return _run(service.run_uploaded, files, service.TIE_BREAKERS[tie_label])


def on_bench(seed_spec, difficulties, tie_labels):
    try:
        md, bundle = service.quick_bench(seed_spec, difficulties, [service.TIE_BREAKERS[t] for t in tie_labels])
    except service.DemoError as exc:
        raise gr.Error(str(exc)) from None
    return md, str(bundle)


with gr.Blocks(title="ReconLoop", analytics_enabled=False) as demo:
    gr.Markdown(INTRO)

    with gr.Tab("Try a synthetic batch"):
        with gr.Row():
            difficulty = gr.Radio(["standard", "hard"], value="hard", label="Batch",
                                  info="Hard has more traps and messier bank narrations.")
            seed = gr.Number(value=301, precision=0, minimum=0, maximum=service.MAX_SEED, label="Seed",
                             info="Same seed, same batch. 301-310 are the benchmark batches.")
            tie = gr.Radio(SYNTHETIC_CHOICES, value="Rules only", label="Tie-breaker")
        with gr.Row():
            go = gr.Button("Reconcile this batch", variant="primary")
            dice = gr.Button("Random seed")
        gr.Markdown(TIE_NOTES)
        summary = gr.Markdown()
        bundle = gr.File(label="Inputs, outputs and answer key (zip)")
        report = gr.HTML(padding=False)

    with gr.Tab("Bring your own CSVs"):
        gr.Markdown(UPLOAD_HELP)
        gr.DownloadButton("Download the sample batch", value=service.sample_bundle())
        with gr.Row():
            up_rzp = gr.File(label="razorpay_recon.csv", file_types=[".csv"], type="filepath")
            up_bank = gr.File(label="bank_statement.csv", file_types=[".csv"], type="filepath")
            up_books = gr.File(label="books.csv", file_types=[".csv"], type="filepath")
        up_tie = gr.Radio(UPLOAD_CHOICES, value="Rules only", label="Tie-breaker")
        go_up = gr.Button("Reconcile my files", variant="primary")
        summary_up = gr.Markdown()
        bundle_up = gr.File(label="Outputs (zip)")
        report_up = gr.HTML(padding=False)

    with gr.Tab("Benchmark"):
        gr.Markdown("## Published results\n\n" + service.published_results())
        with gr.Accordion("Run it yourself on seeds you choose", open=False):
            with gr.Row():
                seed_spec = gr.Textbox(value="401-405", label="Seeds",
                                       info=f"A range like 401-410 or a list like 5,9,12. Up to {service.MAX_BENCH_SEEDS}.")
                diffs = gr.CheckboxGroup(["standard", "hard"], value=["standard", "hard"], label="Batches")
                ties = gr.CheckboxGroup(BENCH_CHOICES, value=BENCH_CHOICES, label="Tie-breakers")
            go_bench = gr.Button("Run benchmark", variant="primary")
            bench_md = gr.Markdown()
            bench_file = gr.File(label="results.md and results.json (zip)")

    with gr.Tab("How it works"):
        gr.Markdown(HOW)

    go.click(on_synthetic, [difficulty, seed, tie], [summary, report, bundle])
    dice.click(lambda: random.randint(1000, 999999), None, seed)
    go_up.click(on_upload, [up_rzp, up_bank, up_books, up_tie], [summary_up, report_up, bundle_up])
    go_bench.click(on_bench, [seed_spec, diffs, ties], [bench_md, bench_file])

if __name__ == "__main__":
    demo.launch(
        server_name=os.environ.get("GRADIO_SERVER_NAME", "0.0.0.0"),
        server_port=int(os.environ.get("GRADIO_SERVER_PORT", "7860")),
        max_file_size="6mb",
        theme=gr.themes.Base(primary_hue="indigo", neutral_hue="slate"),
    )
