"""shared_preview — rendering helpers shared by discovery/discovery_scripts/
app_preview.py and encounters/encounters_scripts/app_preview.py.

Both scripts render the same JSON emphasis convention (**bold**, *italic*)
mirrored from devocional_nuevo's buildEmphasisMarkdownText
(lib/widgets/markdown_emphasis_text.dart) -- keeping that logic here means
a future change to the Dart renderer only needs to be mirrored once.
"""
