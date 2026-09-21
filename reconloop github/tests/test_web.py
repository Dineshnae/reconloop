import json

from reconloop import service, web


def test_markdown_subset():
    html = web.md_to_html("### Head **b**\n\n| | |\n|---|---|\n| A | 1 |\n\n| x | y |\n|---|---|\n| 1 | 2 |\n\n"
                          "one\ntwo `c`\n\n- **a**: b\n- c\n\n1. first\n2. second")
    assert "<h4>Head <strong>b</strong></h4>" in html
    assert "<tbody><tr><td>A</td><td>1</td></tr></tbody>" in html          # empty header row dropped
    assert "<thead><tr><th>x</th><th>y</th></tr></thead>" in html
    assert "<p>one two <code>c</code></p>" in html
    assert "<ul><li><strong>a</strong>: b</li><li>c</li></ul>" in html
    assert "<ol><li>first</li><li>second</li></ol>" in html


def test_markdown_escapes_text():
    assert "&lt;script&gt;" in web.md_to_html("| <script> | x |\n|---|---|\n| a | b |")


def test_browser_entry_points_return_json():
    ok = json.loads(web.synthetic("hard", 305, "Rules only"))
    greedy = json.loads(web.synthetic("hard", 305, "Greedy baseline"))
    assert ok["ok"] and ok["wrong_links"] == 0 and ok["zip_b64"] and "<table>" in ok["summary_html"]
    assert greedy["wrong_links"] > 0
    assert not json.loads(web.synthetic("hard", 5, "Claude"))["ok"]              # never offered in the browser
    assert "whole number" in json.loads(web.synthetic("hard", "x", "Rules only"))["error"]


def test_browser_upload_benchmark_and_sample():
    paths = {n: str(service.SAMPLE_DIR / n) for n in ("razorpay_recon.csv", "bank_statement.csv", "books.csv")}
    up = json.loads(web.uploaded(json.dumps(paths), "Rules only"))
    assert up["ok"] and up["wrong_links"] is None and "not scored" in up["summary_html"]
    assert not json.loads(web.uploaded(json.dumps(paths), "Oracle ceiling"))["ok"]
    bench = json.loads(web.benchmark("401", json.dumps(["standard"]), json.dumps(["Rules only"])))
    assert bench["ok"] and "<table>" in bench["table_html"]
    assert "in the browser" in json.loads(web.benchmark("1-30", json.dumps(["hard"]), json.dumps(["Rules only"])))["error"]
    assert json.loads(web.sample_zip())["zip_name"].endswith(".zip")


def test_static_build_embeds_everything(tmp_path):
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("build_static", Path(__file__).parents[1] / "scripts" / "build_static.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    page = mod.build(tmp_path, author="Test Author", repo_url="https://example.com/repo")
    text = page.read_text()
    assert "{{" not in text and "Test Author" in text and "pyodide@" in text
    assert (tmp_path / "README.md").read_text().startswith("---\ntitle: ReconLoop") and "sdk: static" in (tmp_path / "README.md").read_text()
