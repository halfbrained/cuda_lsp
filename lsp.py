import os
import time
import json

import sys
_plugin_dir = os.path.dirname(os.path.realpath(__file__))
sys.path.append(os.path.join(_plugin_dir, 'lsp_modules'))

from cudatext import *
from cudax_lib import _json_loads #, get_translation
import cuda_project_man

from .book import DocBook
from .dlg import Hint
from .util import (
        lex2langid,
        update_lexmap,
        langid2lex,
        ed_uri,
        is_ed_visible,
        command, # hides hint
    )

# imported on access
#from .language import Language

#_   = get_translation(__file__)  # I18N

LOG = False
LOG_NAME = 'LSP'

fn_config = os.path.join(app_path(APP_DIR_SETTINGS), 'cuda_lsp.json')
#fn_config_servers = os.path.join(app_path(APP_DIR_SETTINGS), 'cuda_lsp_servers.json')
dir_server_configs = os.path.join(app_path(APP_DIR_DATA), 'lspconfig')

opt_enable_mouse_hover = True
opt_root_dir_source = 0 # 0 - from project parent dir,  1 - first project dir/node
opt_send_change_on_request = False
opt_hover_max_lines = 10

# to close - change lexer (then back)
opt_manual_didopen = False # debug help "manual_didopen"

"""
file:///install.inf
file:///book.py
file:///language.py
file:///util.py

https://microsoft.github.io/language-server-protocol/specifications/specification-current/#exit

#TODO
* python server scans unncesasry files
* print server stderr to Output panel
* test hover with OS scaled
* handle lite lexers

* ! kill css server properly

#TODOC
* server root uri
* config tcp port
* commands + options; +hover-Control

#TODO later (random order)
* add status messages
* hover dialog - add 'hide' to dialog just in case
* remember active_ed,caret - dont show dialog-respose if changed (not only for completion)
* check if need snippet support in completion
* find server supporting 'prepareCallHierarchy' stuff
* 'PartialResultParams'
* 'WorkDoneProgressParams'
* urls in hover dialog
* generic completion result cache?
* sidebar panel - tree (server/doc), config, shutdown server, manual file opening
* markdown Editor display
* () on functions completion?
* 'hover' dialog -- add  context menu - apply any lexer


#TODO features
+ Document:
    codeAction
    codeLens
    format
    rename
    onTypeFormatting
    selectionRange
    callHierarch
    moniker
    --documentHighlight
    --documentLink
    --colorProvider
    --foldingRange
    --linkedEditingRange
    --semanticTokens

+ Workspace:
    applyEdit
    symbol
    configuration
    [file operations]

+ Window:
    workDoneProgress
    showMessage
    showDocument

+ General:
    regularExpressions
    markdown

"""

class Command:

    def __init__(self):
        self.is_loading_sesh = False
        # editors not on_open'ed durisg sesh-load;  on_open visibles when sesh loaded
        self._sesh_eds = []
        self._langs = {} # langid -> Language
        self._book = DocBook()

        self._load_config()

        # dbg
        if opt_manual_didopen:
            # first call only starts server, subsequent - send didOpen
            app_proc(PROC_SET_SUBCOMMANDS, 'cuda_lsp;force_didopen;LSP-Open-Doc\t')


    def config(self):
        if not os.path.exists(fn_config):
            self._save_config()
        file_open(fn_config)

    #NOTE alse gets called for unsaved from session
    def on_open(self, ed_self):
        if not self.is_loading_sesh:
            self._do_on_open(ed_self)
        else: # sesh is loading - delay
            self._sesh_eds.append(ed_self)

    def _do_on_open(self, ed_self):
        """ Creates 'doc' only if 'ed' is visible
                (to not didOpen all opened files at start)
            * on new capability - only visible 'ed's are 'didOpen'-ed
            * ? tab change
            (updates existing doc when needed)
        """
        if opt_manual_didopen  or  not is_ed_visible(ed_self):
            return

        doc = self._book.get_doc(ed_self) # get existing

        lex = ed_self.get_prop(PROP_LEXER_FILE)
        langid = lex2langid(lex)

        if lex is None  or  langid is None:
            if doc:
                doc.update()
            return


        lang = self._get_lang(ed_self, langid) # uses dynamic server registrationed
        pass;       LOG and print(f'on_open: lex, langid, filename:{lex, langid, ed_self.get_filename()} =>> lang:{lang.name if lang else "none"}')

        # if have server for this lexer
        if lang:
            if not doc:
                self._book.new_doc(ed_self)
                doc = self._book.get_doc(ed_self)

            if lang.on_open(doc): # doc's .lang is set only when was actually didOpen-ed
                doc.update(lang=lang)

    def on_save(self, ed_self):
        """ handles changed uri for LSP doc
            * if was unsaved with LSP lexer: will have a LSP 'doc' -- ok
            * lexer change via save is handles in .on_lexer()
        """
        doc = self._book.get_doc(ed_self)
        if doc:     # if owning LSP doc
            newuri = ed_uri(ed_self)
            if doc.uri != newuri:   # if uri changed (new file) - close old, open new
                pass;       LOG and print(f'* uri change: {doc.uri} => {newuri}')
                if doc.lang:
                    doc.lang.on_close(doc)
                self.on_open(ed_self)
            elif doc.lang: # just saved to same file
                doc.lang.on_save(doc)
        # handle lexer|ext change by saving (can't know if something changed)
        else:
            self.on_open(ed_self)

    def on_close(self, ed_self):
        doc = self._book.get_doc(ed_self)
        if doc:
            pass;       LOG and print('on_close: '+doc.uri)
            if doc.lang:
                doc.lang.on_close(doc)
            self._book.on_close(ed_self) # deletes doc

    #NOTE: also gets called when document first activated
    def on_lexer(self, ed_self):
        doc = self._book.get_doc(ed_self)
        if doc:
            newlex = ed_self.get_prop(PROP_LEXER_FILE)
            oldlex = doc.lex
            pass;       LOG and print(f'lex changed: {ed_uri(ed_self)}:: {oldlex} => {newlex}')
            if oldlex != newlex:
                if doc.lang:
                    doc.lang.on_close(doc) # sends on_close to server
                self.on_open(ed_self) # changes/removes lang server of doc
        else:    # create doc if new lexer is supported by lsp server
            self.on_open(ed_self)

    def on_change_slow(self, ed_self):
        if opt_send_change_on_request:
            return

        doc = self._book.get_doc(ed_self)
        if doc and doc.lang:
            doc.lang.send_changes(doc)

    @command
    def on_complete(self, ed_self):
        doc = self._book.get_doc(ed_self)
        if doc and doc.lang:
            return doc.lang.on_complete(doc)

    def on_snippet(self, ed_self, snippet_id, snippet_text):
        """ for Editor.complete_alt()
        """
        for lang in self._langs.values():
            lang.on_snippet(ed_self, snippet_id, snippet_text)

    def on_mouse_stop(self, ed_self, x, y):
        if not opt_enable_mouse_hover:      return
        if Hint.is_under_cursor():      return
        # require Control pressed
        if app_proc(PROC_GET_KEYSTATE, '') != 'c':
            return

        doc = self._book.get_doc(ed_self)
        if doc  and  doc.lang  and  ed_self.get_prop(PROP_FOCUSED):
            doc.lang.on_hover(doc, x=x, y=y)

    @command
    def on_func_hint(self, ed_self):
        doc = self._book.get_doc(ed_self)
        if doc  and  doc.lang  and  ed_self.get_prop(PROP_FOCUSED):
            doc.lang.request_sighelp(doc)
            return ''

    def on_goto_def(self, ed_self):
        self.call_definition(ed_self)

    def on_tab_change(self, ed_self):
        doc = self._book.get_doc(ed_self)
        if doc  and  doc.lang:
            doc.lang.on_ed_shown(doc)
        else: # look for matching server if not already opened
            self.on_open(ed_self)


    def on_lang_inited(self, name):
        """ when server initialized properly (handshake done) - send 'didOpen'
                for documents of this server
                (for visible editors, others - from on_tab_change())
        """
        for lang in self._langs.values():
            if lang.name == name:
                for doc in self._book.get_docs():
                    if doc.lang is None  and  is_ed_visible(doc.ed):
                        self.on_open(doc.ed)
                break # found initted lang
        else:
            raise Exception('Coulnt find initted lang: '+str(name))

    def on_state(self, ed_self, state):
        if state == APPSTATE_SESSION_LOAD_BEGIN: # started
            self.is_loading_sesh = True

        elif state in [APPSTATE_SESSION_LOAD_FAIL, APPSTATE_SESSION_LOAD]: # ended
            self.is_loading_sesh = False
            # on_open for delayed
            eds = self._sesh_eds[:]
            self._sesh_eds.clear()
            for editor in eds:
                self._do_on_open(editor)


    def on_exit(self, *args, **vargs):
        # start servers shutdown
        for langid,lang in self._langs.items():
            lang.shutdown()

        _start = time.time()
        while self._langs:
            time.sleep(0.1)
            for key,lang in list(self._langs.items()):
                lang.process_queues()
                if lang.is_client_exited():
                    del self._langs[key]

            if time.time() - _start > 2:
                break

        os.waitpid(-1, os.WNOHANG) # -1 -- any child


    def _get_lang(self, ed_self, langid):
        # look in existing langs with proper registration match
        for lang in self._langs.values():
            if lang.is_ed_matches(ed_self, langid):
                pass;       LOG and print(f'* matched existing lang: {ed_self, langid} -- {lang.name}')
                return lang

        # create new lang
        if langid not in self._langs:
            # no server exists for this langid, try to create
            for cfg in servers_cfgs:
                if langid in cfg.get('langids', []):
                    from .language import Language

                    # choose root directory for server: .opt_root_dir_source
                    work_dir = None
                    if opt_root_dir_source == 0: # project file dir
                        work_dir = cuda_project_man.project_variables()["ProjDir"] or None
                    elif opt_root_dir_source == 1: # first node
                        _nodes = cuda_project_man.global_project_info.get('nodes')
                        work_dir = _nodes[0] if _nodes else None
                    cfg['work_dir'] = work_dir

                    lang = Language(cfg)
                    pass;       LOG and print(f'*** Created lang({lang.name}) for {ed_self, langid}')
                    # register server to all its supported langids
                    for server_langid in lang.langids:
                        self._langs[server_langid] = lang
                    break

        return self._langs.get(langid)

    @command
    def call_hover(self):
        doc = self._book.get_doc(ed)
        if doc and doc.lang:
            doc.lang.on_hover(doc)

    @command
    def call_definition(self, ed_self=None):
        ed_self = ed_self or ed
        doc = self._book.get_doc(ed_self)
        if doc and doc.lang:
            doc.lang.request_definition_loc(doc)

    @command
    def call_references(self):
        doc = self._book.get_doc(ed)
        if doc and doc.lang:
            doc.lang.request_references_loc(doc)

    @command
    def call_implementation(self):
        doc = self._book.get_doc(ed)
        if doc and doc.lang:
            doc.lang.request_implementation_loc(doc)

    @command
    def call_declaration(self):
        doc = self._book.get_doc(ed)
        if doc and doc.lang:
            doc.lang.request_declaration_loc(doc)

    @command
    def call_typedef(self):
        doc = self._book.get_doc(ed)
        if doc and doc.lang:
            doc.lang.request_typedef_loc(doc)


    def dbg_call_hierarchy_in(self):
        doc = self._book.get_doc(ed)
        if doc and doc.lang:
            doc.lang.call_hierarchy_in(doc)


    def dbg_doc_symbols(self):
        doc = self._book.get_doc(ed)
        if doc and doc.lang:
            doc.lang.doc_symbol(doc)

    def dbg_workspace_symbols(self):
        doc = self._book.get_doc(ed)
        if doc and doc.lang:
            doc.lang.workspace_symbol(doc)


    def dbg_signature(self):
        doc = self._book.get_doc(ed)
        if doc and doc.lang:
            doc.lang.request_sighelp(doc)


    def dbg_show_msg(self, show_bytes=False):
        lex = ed.get_prop(PROP_LEXER_FILE)
        langid = lex2langid(lex)
        lang = self._langs.get(langid)
        if lang is None:
            msg_status(f'No messages for server of current document')
            return

        names = ['msg|' + str(msg)[:80]+'...\t'+type(msg).__name__ for msg in lang._dbg_msgs]
        ind = dlg_menu(DMENU_LIST, names)

        if ind is not None:
            if ed.get_filename():
                file_open('')
            import pprint
            try:
                ed.set_text_all(pprint.pformat(lang._dbg_msgs[ind].dict(), width=256))
            except:
                j = {k:str(v) for k,v in lang._dbg_msgs[ind].dict().items()}
                ed.set_text_all(pprint.pformat(j, width=256))

    def dbg_show_docs(self):
        items = [f'{doc.lang}: {doc}' for doc in self._book.get_docs()]
        dlg_menu(DMENU_LIST, items, caption='LSP Docs')

    @command
    # for project folder change
    def shutdown_server(self):
        names = list(self._langs)
        names.sort()
        ind = dlg_menu(DMENU_LIST, names, caption='Shutdown server')
        if ind is not None:
            lang = self._langs.pop(names[ind])
            lang.shutdown()

            # remove referencees to Language object
            for doc in self._book.get_docs():
                if doc.lang == lang:
                    doc.update(lang=None)

            # server can have multiple langids - remove all
            lang_ids = [langid for langid,lang_ in self._langs.items()  if lang_ == lang]
            for langid in lang_ids:
                del self._langs[langid]


    def force_didopen(self, *args, **vargs):
        ed_self = Editor(ed.get_prop(PROP_HANDLE_SELF))

        _langid = lex2langid(ed_self.get_prop(PROP_LEXER_FILE))
        lang = self._get_lang(ed_self, _langid) # uses dynamic server registrationed
        pass;       LOG and print(f'manual didopen: lex, langid, filename:{_langid, ed_self.get_filename()} =>> lang:{lang.name if lang else "none"}')

        self._book.new_doc(ed_self)
        doc = self._book.get_doc(ed_self)
        lang.on_open(doc)
        doc.update(lang=lang)

    @command
    def caret_definition(self, caret_str):
        """ caret_str: string - '3|39' - caretx|carety
        """
        ed_self = Editor(ed.get_prop(PROP_HANDLE_SELF))

        caret = list(map(int, caret_str.split('|')))
        caret_px = ed_self.convert(CONVERT_CARET_TO_PIXELS, *caret)
        if caret_px:
            x,y = caret_px
            doc = self._book.get_doc(ed_self)
            if doc and doc.lang:
                doc.lang.request_definition_loc(doc, x=x, y=y)


    def _load_config(self):
        global opt_enable_mouse_hover
        global opt_root_dir_source
        global opt_manual_didopen
        global opt_send_change_on_request
        global opt_hover_max_lines

        # general cfg
        if os.path.exists(fn_config):
            with open(fn_config, 'r', encoding='utf-8') as f:
                js = f.read()
            j = _json_loads(js)
            opt_enable_mouse_hover = j.get('enable_mouse_hover', opt_enable_mouse_hover)

            _opt_root_dir_source = j.get('root_dir_source', opt_root_dir_source)
            if _opt_root_dir_source in [0, 1]:
                opt_root_dir_source = _opt_root_dir_source
            else:
                print(f'NOTE:{LOG_NAME}: invalid value of option "root_dir_source"'
                        +f' in {fn_config}, should be "0" or "1"')

            opt_send_change_on_request = j.get('send_change_on_request', opt_send_change_on_request)
            opt_hover_max_lines = j.get('hover_dlg_max_lines', opt_hover_max_lines)

            # hidden,dbg
            opt_manual_didopen = j.get('manual_didopen', False)

            # apply options
            Hint.set_max_lines(opt_hover_max_lines)

        # servers
        if os.path.exists(dir_server_configs):
            user_lexids = {}
            _fns = os.listdir(dir_server_configs)
            _json_fns = (name for name in _fns  if name.lower().endswith('.json'))
            for name in _json_fns:
                path = os.path.join(dir_server_configs, name)
                with open(path, 'r', encoding='utf-8') as f:
                    try:
                        j = _json_loads(f.read())
                    except:
                        print(f'NOTE:{LOG_NAME}: failed to load server config: {path}')
                        continue

                # load lex map from config
                langids = j.setdefault('langids', [])
                lexmap = j.get('lexers', {})
                for lex,langid in lexmap.items():
                    langids.append(langid)
                    user_lexids[lex] = langid

                if 'name' not in j:
                    j['name'] = os.path.splitext(name)[0]
                servers_cfgs.append(j)

            update_lexmap(user_lexids) # add user mapping to global map

        if not servers_cfgs:
            print(f'NOTE:{LOG_NAME}: no server configs was found in "{dir_server_configs}"')


    def _save_config(self):
        if not os.path.exists(fn_config):
            with open(fn_config, 'w', encoding='utf-8') as f:
                f.write(options_json)

    # DBG
    @property
    def lcs(self):
        return self._langs.get('csharp')
    def _killcs(self):
        lcs = self._langs.pop('csharp')
        lcs.shutdown()
    @property
    def lpy(self):
        return self._langs.get('python')
    def _killpy(self):
        lpy = self._langs.pop('python')
        lpy.shutdown()


# if python too old - give msgbox and disable plugin
import sys
ver = sys.version_info
if (ver.major, ver.minor) < (3, 6):
    msg = f'{LOG_NAME}: current Python version is not supported.' \
        +' Please upgrade to Python 3.6 or newer.'
    callback = lambda *args, msg=msg, **vargs: msg_box(msg, MB_OK or MB_ICONWARNING)
    timer_proc(TIMER_START_ONE, callback, 1000)

    class Command:
        def __getattribute__(self, name):
            return lambda *args, **vargs: None


options_json = """{
    // when 'false' - 'hover' only acessible via a command
    "enable_mouse_hover": true,

    // LSP server root directory source:
    // 0: parent directory of '.cuda-proj'
    // 1: first directory in project
    "root_dir_source": 0,

    // false - changes to the documents are sent to server after edit and a short delay (default)
    // true - sent only before requests (will delay server's analysis)
    "send_change_on_request": false,

    // hover dialog max lines number
    "hover_dlg_max_lines": 10,
}
"""

servers_cfgs = []
"""
{
    'name': 'pyls',
    'langids': ['python'],
    'cmd': ['pyls'],
    'work_dir': '/mnt/H/cuda/__FM/py/cuda_runner',
    #'tcp_port': 2087,
},
#{
    #'name': 'omnisharp',
    #'langids': ['csharp'],
    #'work_dir': '/media/q/REST_API',
    #'cmd': [
        #'/home/eon/Downloads/omnisharp-linux-x64/run',
        #'-s', '/media/q/REST_API',
        #'-lsp',
        #'omnisharp.useGlobalMono:always',
    #],
#},
#{
    #'name': 'omnisharp',
    #'langids': ['csharp'],
    #'work_dir': '/media/q/websocket-sharp',
    #'cmd': [
        #'/home/eon/Downloads/omnisharp-linux-x64/run',
        #'-s', '/media/q/websocket-sharp',
        #'-lsp',
        #'omnisharp.useGlobalMono:always',
    #],
#},
#{
    #'name': 'ccls',
    #'langids': ['c', 'cpp'],
    #'cmd': ['ccls'],
    #'work_dir': '/mnt/G/del/C__menu_run/',
#},
]
"""


