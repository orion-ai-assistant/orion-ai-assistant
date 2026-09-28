"""Browser-backed fixtures distilled from the user's September 2026 AI Mode DOM."""

import os
import tempfile
import unittest

from orion.worker.tools.google_ai.tools import ANSWER_SELECTOR, SNAPSHOT, _sources


from raw_browser_fixture import RawBrowserFixture
import json


class GoogleDOMTests(RawBrowserFixture):
    def snapshot(self, html):
        async def read():
            async with self.manager.tab('google-fixture', background=True) as page:
                frame = (await page.send('Page.getFrameTree'))['frameTree']['frame']['id']
                await page.send('Page.setDocumentContent', {'frameId': frame, 'html': html})
                return await page.evaluate('(' + SNAPSHOT + ')(' + json.dumps(ANSWER_SELECTOR) + ')')
        return self.run_async(read())

    def test_current_main_col_excludes_hidden_ui_and_reads_citation_record(self):
        data = self.snapshot('''<nav>Google navigation</nav>
          <div data-container-id="main-col">
            <div data-complete="true"><mark><strong><span data-subtree="aimfl">Ve aleyküm selâm!</span></strong> Size de huzur dilerim.</mark></div>
            <div style="display:contents"><ul><li data-complete="true">Genel bilgi ve araştırmalar</li></ul></div>
            <table><tr><td><span role="button">Posidonia australis</span></td><td>200 km²</td></tr></table>
            <div style="display:none">HIDDEN PRIVATE METADATA</div>
            <div class="invisible">CSS HIDDEN</div><style>.invisible {display:none}</style>
            <span><a href="https://www.google.com/goto?url=opaque">Diyanet</a>
              <button data-icl-uuid="citation-1">İlgili sonuçlar</button>
              <!--TgQPHd|||[[&quot;citation-1&quot;,null,null,0,&quot;favicon&quot;,&quot;Diyanet&quot;,1,&quot;https://example.org/source.pdf&quot;]]-->
              <!--TgQPHd|||[["unrelated",null,null,0,"favicon","Ignore",1,"https://example.org/unrelated"]]-->
            </span>
            <div jsaction="aimRenderComplete:finish" data-complete="true">
              <div aria-busy="true">Paylaşım işlemi</div><button>Metni kopyala</button><span>Bu yanıt hakkında</span>
            </div>
          </div>''')
        self.assertTrue(data['root_found'])
        self.assertTrue(data['completed'])
        self.assertFalse(data['busy'])
        self.assertIn('**Ve aleyküm selâm\\!** Size de huzur dilerim.', data['answer'])
        self.assertIn('Genel bilgi ve araştırmalar', data['answer'])
        self.assertIn('| Posidonia australis | 200 km² |', data['answer'])
        for hidden in ('HIDDEN', 'PRIVATE', 'METADATA', 'Paylaşım', 'Metni kopyala', 'Bu yanıt hakkında', 'İlgili sonuçlar', 'navigation'):
            self.assertNotIn(hidden, data['answer'])
        self.assertEqual(_sources(data['links']), [{'url': 'https://example.org/source.pdf', 'title': 'Diyanet'}])

    def test_latest_response_short_answer_and_visible_streaming(self):
        data = self.snapshot('''<div data-container-id="main-col">Old long answer from a previous question</div>
          <div data-container-id="main-col"><div data-complete="false">Ankara.</div>
          <div hidden aria-busy="true">hidden spinner</div></div>''')
        self.assertEqual(data['answer'], 'Ankara.')
        self.assertTrue(data['busy'])
        data = self.snapshot('<div data-subtree="aimc">Ankara.<div hidden aria-busy="true">spinner</div></div>')
        self.assertEqual(data['answer'], 'Ankara.')
        self.assertFalse(data['busy'])

    def test_generic_search_results_are_never_an_ai_answer(self):
        data = self.snapshot('<main><h1>Search results</h1><p>A very plausible answer in a normal search snippet.</p></main>')
        self.assertFalse(data['root_found'])
        self.assertEqual(data['answer'], '')

    def test_inline_links_and_citations_keep_their_position(self):
        data = self.snapshot('''<div data-container-id="main-col">
          <p>First claim.<button data-icl-uuid="first">Source chip</button></p>
          <p><a href="https://example.org/biography"><strong>Biography</strong></a>: second claim.</p>
          <p><a href="javascript:alert(1)">Unsafe label</a></p>
          <div hidden><!--TgQPHd|||[["first",null,null,0,"favicon","Archive",1,"https://example.org/archive"]]--></div>
          <button>Share</button></div>''')
        answer = data['answer']
        self.assertIn('First claim. [Archive](<https://example.org/archive>)', answer)
        self.assertIn('[**Biography**](<https://example.org/biography>): second claim.', answer)
        self.assertLess(answer.index('[Archive]'), answer.index('[**Biography**]'))
        self.assertNotIn('javascript:', answer)
        self.assertNotIn('Share', answer)
        self.assertEqual(len(_sources(data['links'])), 2)

    def test_nested_formatting_is_applied_once_without_rewriting_words(self):
        data = self.snapshot('''<div data-container-id="main-col"><p>
          <strong><b><em><i>Armillaria ostoyae</i></em></b></strong>
          and <span style="font-weight:700"><strong>Pando</strong></span>.
          Literal ***** stays literal.
        </p></div>''')
        self.assertIn('***Armillaria ostoyae***', data['answer'])
        self.assertIn('**Pando**', data['answer'])
        self.assertNotIn('*****Armillaria', data['answer'])
        self.assertIn(r'Literal \*\*\*\*\* stays literal.', data['answer'])

    def test_markdown_headings_emphasis_nested_lists_and_tables(self):
        data = self.snapshot('''<div data-container-id="main-col">
          <h2>1. En Büyük Hayvan</h2>
          <p><strong>Mavi balina</strong> ve <em>Balaenoptera musculus</em>.</p>
          <ul><li><div></div></li><li><b>Boyut:</b> 33 metre<ul><li>Denizde yaşar</li></ul></li><li>Ağırlık: 180 ton</li></ul>
          <ol start="3"><li>Üçüncü</li><li>Dördüncü</li></ol>
          <div role="heading" aria-level="3">Karşılaştırma</div>
          <table><tr><th>Canlı</th><th>Alan</th></tr><tr><td><strong>Deniz çayırı</strong></td><td>200 km²</td></tr></table>
          <p><span style="font-weight:700">Önemli</span>: &lt;script&gt; ve [sahte](javascript:test).</p>
          <div hidden><h1>Gizli başlık</h1></div></div>''')
        answer = data['answer']
        for expected in ('## 1. En Büyük Hayvan', '**Mavi balina**', '*Balaenoptera musculus*',
                         '- **Boyut:** 33 metre', '\n  - Denizde yaşar', '3. Üçüncü', '4. Dördüncü',
                         '### Karşılaştırma', '| Canlı | Alan |\n| --- | --- |',
                         '| **Deniz çayırı** | 200 km² |', '**Önemli**', '&lt;script&gt;', '\\[sahte\\]'):
            self.assertIn(expected, answer)
        self.assertNotIn('Gizli başlık', answer)
        self.assertNotIn('<script>', answer)
        self.assertNotIn('\n-\n', answer)
