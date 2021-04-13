import time
from difflib import SequenceMatcher

from cudatext import *
#from cudax_lib import get_translation

from .util import get_first, lex2langid, ed_uri

#from .sansio_lsp_client import structs

#_   = get_translation(__file__)  # I18N

import traceback
import datetime


class DocBook:
    def __init__(self):
        self.docs = {} # uri => Document

    def new_doc(self, ed):
        global structs
        from .sansio_lsp_client import structs

        doc = EditorDoc(ed)
        self.docs[doc.uri] = doc

    def get_doc(self, ed=None, uri=None):
        return self._get_doc(ed=ed, uri=uri)

    def get_docs(self):
        return list(self.docs.values())

    #def on_save(self, ed): # handled in Command.on_save()

    def on_close(self, ed):
        eddoc = self._get_doc(ed=ed)
        del self.docs[eddoc.uri]

    def _get_doc(self, *args, ed=None, uri=None):
        assert not args, 'only call _get_doc() w/ named arguments'

        if ed is not None:
            return get_first(doc for doc in self.docs.values()  if doc.ed == ed)
        elif uri is not None:
            return self.docs[uri]

class EditorDoc:
    def __init__(self, ed):
        self._ed = ed
        self._txt = ed.get_text_all()
        self._ver = 1
        self._uri = ed_uri(ed)
        self._lex = ed.get_prop(PROP_LEXER_FILE)
        self._langid = lex2langid(self._lex)
        self._lang = None

    def __str__(self):
        return f'Doc:{self.ed} [lang:{self.lang}, langid:{self.langid}]'

    @property
    def uri(self): return self._uri
    @property
    def ver(self): return self._ver
    @property
    def txt(self): return self._txt
    @property
    def ed(self): return self._ed
    @property
    def langid(self): return self._langid
    @property
    def lang(self): return self._lang
    @property
    def lex(self): return self._lex

    def on_open(self, lang):
        if self._lang is not None:
            raise Exception('Opening opened doc: {self.uri}')
        self._lang = lang
    def on_close(self):
        if self._lang is None:
            raise Exception('Closeing unopened doc: {self.uri}')
        self._lang = None

    def update(self, lang=None):
        self._lex = self._ed.get_prop(PROP_LEXER_FILE)
        self._langid = lex2langid(self._lex)
        self._lang = lang

    def get_changes(self):
        oldtxt,  self._txt  =  self._txt,  self.get_text_all()
        if oldtxt == self._txt:     return []

        oldspl = oldtxt.splitlines(keepends=True)
        newspl = self._txt.splitlines(keepends=True)
        # matches example: [Match(a=0, b=0, size=4), Match(a=4, b=5, size=0)]
        matches = SequenceMatcher(a=oldspl, b=newspl).get_matching_blocks()

        #NOTE: tracking changes by full lines  (optimize if needed later)
        ### changes: TextDocumentContentChangeEvent(txt, range)
        # range - Range(start, end)
        # start,end - Position(line_ind, char_ind)
        changes = []
        ia, ib = 0, 0
        for match in matches:
            if match.a == ia:
                if match.b == ib: # same pos -- nothing skipped
                    #ia += match.size
                    #ib += match.size
                    pass
                elif match.b > ib: # inserted line after .ib
                    start = structs.Position(line=ib, character=0)
                    end = start # ...
                    _range = structs.Range(start=start, end=end)
                    _change_str = ''.join(newspl[ib:match.b])
                    change_ev = structs.TextDocumentContentChangeEvent(text=_change_str, range=_range)
                    changes.append(change_ev)
                else:
                    raise Exception('Match block b - before (should_never_happen_tm):' + str((ia, ib, match)))
            elif match.a > ia: # change before this match
                if match.b == ib: # deleted line(s)
                    start = structs.Position(line=ia, character=0)
                    end = structs.Position(line=match.a-1, character=len(oldspl[match.a-1]))
                    _range = structs.Range(start=start, end=end)
                    _change_str = ''
                    change_ev = structs.TextDocumentContentChangeEvent(text=_change_str, range=_range)
                    changes.append(change_ev)
                elif match.b > ib: # changed line(s)
                    start = structs.Position(line=ia, character=0)
                    end = structs.Position(line=match.a-1, character=len(oldspl[match.a-1]))
                    _range = structs.Range(start=start, end=end)
                    _change_str = ''.join(newspl[ib:match.b])
                    change_ev = structs.TextDocumentContentChangeEvent(text=_change_str, range=_range)
                    changes.append(change_ev)
                else:
                    raise Exception('Match block c? - before (should_never_happen_tm):' + str((ia, ib, match)))
            else:
                raise Exception('Match block a - before (should_never_happen_tm):' + str((ia, ib, match)))

            ia = match.a + match.size
            ib = match.b + match.size

        if changes:
            self._ver += 1
        return changes

    def get_text_all(self):
        return self._ed.get_text_all()

    def on_save(self):
        pass

    def get_verdoc(self):
        return structs.VersionedTextDocumentIdentifier(uri=self.uri, version=self.ver)

    def get_docpos(self, x=None, y=None):
        if x is None or y is None: # caret pos
            x1, y1, _x2, _y2 = self.ed.get_carets()[0]

        else:  # mouse pos
            res = self.ed.convert(CONVERT_PIXELS_TO_CARET, x, y, "")
            if res is None:     return
            x1, y1 = res
            # is after text
            tl = self.ed.get_text_line(y1)
            if x1 >= len(tl):       return
            # is in comment or string
            if self.ed.get_token(TOKEN_GET_KIND, x1, y1) in ('s','c'):      return


        _docid = self.get_docid()
        _pos = structs.Position(line=y1, character=x1)
        docpos = structs.TextDocumentPosition(textDocument=_docid, position=_pos)
        return docpos

    def get_textdoc(self):
        doc = structs.TextDocumentItem(
            uri         = self.uri,
            languageId  = self.langid,
            version     = self.ver,
            text        = self.txt,
        )
        return doc

    def get_docid(self):
        return structs.TextDocumentIdentifier(uri=self.uri)



