import pytest

pytest.importorskip("gradio")


def test_app_builds_and_handlers_return_three_outputs():
    import gradio as gr

    import app

    assert isinstance(app.demo, gr.Blocks)
    summary, report, bundle = app.on_synthetic("standard", 7, "Rules only")
    assert "Wrong links" in summary
    assert report.startswith("<iframe") and "srcdoc=" in report and "<script" not in report
    assert bundle.endswith(".zip")


def test_handler_errors_become_gradio_errors():
    import gradio as gr

    import app

    with pytest.raises(gr.Error):
        app.on_synthetic("hard", "not-a-seed", "Rules only")
