"""Generate a synthetic English PDF used as a development fixture."""
import sys

import pymupdf

HTML = """
<h1>Quarterly Product Report</h1>
<p>This document summarizes the <b>main results</b> of the third quarter.
Our team shipped <b>four new features</b> and fixed more than <i>two hundred</i> bugs.
For details, visit <a href="https://example.com/report">the full report</a>.</p>
<h2>Highlights</h2>
<ul>
<li>Revenue grew by <b>12%</b> compared to the previous quarter.</li>
<li>Customer satisfaction reached an all-time high.</li>
<li>The new onboarding flow reduced churn significantly.</li>
</ul>
<h2>Next Steps</h2>
<p style="font-family: serif">In the next quarter we will focus on <b>performance</b>,
<b>reliability</b> and a better experience for new users. Please send feedback to
<a href="mailto:team@example.com">team@example.com</a>.</p>
"""

out = sys.argv[1] if len(sys.argv) > 1 else "samples/sample.pdf"
story = pymupdf.Story(html=HTML)
page_rect = pymupdf.paper_rect("a4")
content_rect = page_rect + (50, 50, -50, -50)


def rectfn(rect_num, filled):
    return page_rect, content_rect, None


# write_with_links keeps <a href> as real PDF link annotations.
doc = story.write_with_links(rectfn)
doc.save(out)
print(f"wrote {out}")
