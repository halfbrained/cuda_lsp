import time
from difflib import SequenceMatcher

from cudatext import *
#from cudax_lib import get_translation

from .util import get_first, lex2langid, ed_uri, get_word

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

    def get_changes(self, whole_doc):
        """ whole_doc - bool
        """
        oldtxt,  self._txt  =  self._txt,  self.get_text_all()
        if oldtxt == self._txt:
            return []
        if whole_doc:
            _ch = structs.TextDocumentContentChangeEvent.whole_document_change(change_text=self._txt)
            return [_ch]

        oldspl = oldtxt.splitlines(keepends=True)
        newspl = self._txt.splitlines(keepends=True)

        #NOTE: tracking changes by full lines  (optimize if needed later)
        ### changes: TextDocumentContentChangeEvent(txt, range)
        # range - Range(start, end)
        # start,end - Position(line_ind, char_ind)
        changes = []
        _start_time = time.time()
        while True:
            if time.time() - _start_time > 0.1:
                # taken too long, send whole doc
                changes.clear()
                _ch = structs.TextDocumentContentChangeEvent.whole_document_change(change_text=self._txt)
                changes.append(_ch)
                break

            # matches example: [Match(a=0, b=0, size=4), Match(a=4, b=5, size=0)]
            opc = SequenceMatcher(a=oldspl, b=newspl).get_opcodes()

            opclen = len(opc)
            if opclen == 0  or  (opclen == 1 and opc[0][0] == 'equal'):
                break

            tag, i1,i2, j1,j2 = opc[0]  if opc[0][0] != 'equal' else  opc[1]
            # replace: a[i1:i2] should be replaced by b[j1:j2].
            # delete:  a[i1:i2] should be deleted
            if tag == 'replace'  or  tag == 'delete':
                _start = structs.Position(line=i1, character=0)
                # end: at start of next line (to include '\n'), unless is last line and has no newline
                if i2 == len(oldspl)  and  oldspl[i2-1][-1] != '\n'  and  oldspl[i2-1][-1] != '\r':
                    _end = structs.Position(line=i2-1, character=len(oldspl[i2-1]))
                else:
                    _end = structs.Position(line=i2, character=0)
                range_ = structs.Range(start=_start, end=_end)

                if tag == 'replace':
                    change_str = ''.join(newspl[j1:j2])
                    # apply change to splits
                    oldspl[i1:i2] = newspl[j1:j2]
                else:   # delete
                    change_str = ''
                    # apply change to splits
                    del oldspl[i1:i2]

            elif tag == 'insert':   # b[j1:j2] should be inserted at a[i1:i1]
                _start = structs.Position(line=i1, character=0)
                _end = _start
                range_ = structs.Range(start=_start, end=_end)
                change_str = ''.join(newspl[j1:j2])

                # apply change to splits
                oldspl[i1:i1] = newspl[j1:j2]

            else:
                raise Exception('INVALID opcodes: {opc}')

            change_ev = structs.TextDocumentContentChangeEvent(text=change_str, range=range_)
            changes.append(change_ev)
        #end while

        if changes:
            self._ver += 1

        return changes

    def get_text_all(self):
        return self._ed.get_text_all()

    def on_save(self):
        pass

    def get_verdoc(self):
        return structs.VersionedTextDocumentIdentifier(uri=self.uri, version=self.ver)

    def get_docpos(self, caret=None):
        if caret is None: # caret pos
            x1, y1, _x2, _y2 = self.ed.get_carets()[0]
            
            ## change x to the beginning of the word
            #word = get_word(x1, y1)
            #if word and len(word[0]) != 0:
                #x1 = x1 - len(word[0])

        else:  # mouse pos
            x1, y1 = caret
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

    def get_ed_format_opts(self):
        return structs.FormattingOptions(
            tabSize                 = self.ed.get_prop(PROP_TAB_SIZE),
            insertSpaces            = self.ed.get_prop(PROP_TAB_SPACES),
            trimTrailingWhitespace  = self.ed.get_prop(PROP_SAVING_TRIM_SPACES),
            insertFinalNewline      = self.ed.get_prop(PROP_SAVING_FORCE_FINAL_EOL),
            trimFinalNewlines       = self.ed.get_prop(PROP_SAVING_TRIM_FINAL_EMPTY_LINES),
        )

    def get_selection_range(self):
        """ returns: single range, None on error
        """
        carets = self.ed.get_carets()
        if len(carets) == 1:
            x0,y0, x1,y1 = carets[0]
            _start = structs.Position(line=y0, character=x0)
            _end = structs.Position(line=y1, character=x1)
            return structs.Range(start=_start, end=_end)

    #def apply_edit(ed: Editor, edit: TextEdit):
    def apply_edit(ed, edit):
        x1,y1,x2,y2 = EditorDoc.range2carets(edit.range)
        
        while ed.get_line_count() <= y2:
            ed.set_text_line(-2, '')
        
        if x1==x2 and y1==y2:
            #NOTE: need 'insert' because cant `replace()'` beyond text end
            ed.insert(x1,y1, edit.newText)
        else:
            ed.replace(x1,y1,x2,y2, edit.newText)

    def range2carets(range):
        #x1,y1,x2,y2
        return (range.start.character, range.start.line,  range.end.character, range.end.line,)

