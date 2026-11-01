# PageIndex offline Markdown functions

Source: https://github.com/VectifyAI/PageIndex

Pinned commit: `6d23caf416858f2ca136840305d1f479a86f6ef7`. MIT license is retained in `LICENSE`.
`upstream.json` identifies the original file hash and exact exported functions.

`markdown.py` contains three original pure functions (only trailing whitespace normalized) from
`pageindex/page_index_md.py`, with only the required `re` import. Their function ASTs match the pinned upstream exactly. This is a bounded
vendored source subset, not an installed PageIndex SDK or a replacement RAG system.
It supports heading extraction, original section text and tree construction only.
The upstream package imports `utils.py`, which calls `find_dotenv/load_dotenv`
at import time. That package import would violate the project's secret-file
boundary and introduce unused PDF/provider dependencies. The pure subset avoids
those side effects without patching SDK internals or routing around the Gateway.

Updates must compare these functions to a newly reviewed upstream revision and
update the source hash, pin, license and parity evidence together. Do not add
model summaries, thinning, search agents, cloud uploads or authentication here.
The existing Evidence World remains the authority for original reads.
