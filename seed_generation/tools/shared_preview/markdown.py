import re


def escape(text):
    if text is None:
        return ""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def render_emphasis_markdown(text):
    """Mirror buildEmphasisMarkdownText (markdown_emphasis_text.dart):
    **bold** and *italic*, bold matched first so **x** isn't split into two
    italic markers."""
    escaped = escape(text)
    replaced = re.sub(r"\*\*(.+?)\*\*|\*(.+?)\*", _emphasis_sub, escaped)
    return replaced.replace("\n", "<br>")


def _emphasis_sub(match):
    bold, italic = match.group(1), match.group(2)
    if bold is not None:
        return f"<b>{bold}</b>"
    return f"<i>{italic}</i>"
