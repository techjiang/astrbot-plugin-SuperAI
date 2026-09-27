"""联网搜索工具的纯函数测试（不发真实请求）。"""

from superai.tools.web_tools import _extract_main_text, strip_html, unwrap_ddg_url


def test_strip_html():
    assert strip_html("<b>你好</b>&nbsp;世界") == "你好\xa0世界"


def test_unwrap_ddg_redirect():
    url = "https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa&rut=1"
    assert unwrap_ddg_url(url) == "https://example.com/a"


def test_unwrap_ddg_protocol_relative():
    assert unwrap_ddg_url("//example.com/x") == "https://example.com/x"


def test_unwrap_ddg_plain_url_unchanged():
    assert unwrap_ddg_url("https://example.com/x") == "https://example.com/x"


def test_extract_main_text_strips_scripts_and_tags():
    html = """
    <html><head><style>body{color:red}</style>
    <script>var a = 1;</script></head>
    <body><h1>标题</h1><p>这是正文内容。</p></body></html>
    """
    text = _extract_main_text(html)
    assert "标题" in text
    assert "这是正文内容。" in text
    assert "var a" not in text
    assert "color:red" not in text
    assert "<" not in text
