import os
import time

import sys
_plugin_dir = os.path.dirname(os.path.realpath(__file__))
sys.path.append(os.path.join(_plugin_dir, 'lsp_modules'))

from cudatext import *
from cudax_lib import _json_loads

from cudax_lib import get_translation
_ = get_translation(__file__)  # I18N

from .dlg import Hint, SignaturesDialog
from .util import (
        lex2langid,
        update_lexmap,
        langid2lex,
        ed_uri,
        is_ed_visible,
        command, # hides hint
        get_visible_eds,
        collapse_path,

        ValidationError,
    )

# imported on access
#from .language import Language
#import cuda_project_man
#from .book import DocBook

LOG = False
LOG_NAME = 'LSP'
IS_WIN = os.name=='nt'
SERVER_RESPONSE_DIALOG_LEN = 80

# considering all 'lsp_*.json' - server configs
dir_settings    = app_path(APP_DIR_SETTINGS)
fn_config       = os.path.join(dir_settings, 'cuda_lsp.json')
fn_opt_descr    = os.path.join(_plugin_dir,  'readme', 'options_description.md')
fn_state        = os.path.join(dir_settings, 'cuda_lsp_state.json')

SEVERS_SHUTDOWN_MAX_TIME = 2 # seconds
SEVERS_SHUTDOWN_CHECK_PERIOD = 0.1 # seconds
LINT_STYLE_MAP = {0:1, 1:4, 2: 2, 3:6}

STATE = {} # like log-panel's filter state

opt_enable_mouse_hover = True
opt_root_dir_source = [0]
opt_send_change_on_request = False
opt_hover_max_lines = 10
opt_hover_additional_commands = [
    "Definition",
    "References",
    "Implementation",
    "Declaration",
    "Type definition",
]
opt_cudatext_in_py_env = False
opt_lint_type = 'b'
opt_lint_underline_style = 2  # solid 0, dotted 1, dashes 2, wave 3
opt_enable_code_tree = False
opt_tree_types_show = ''

# to close - change lexer (then back)
opt_manual_didopen = None # debug help "manual_didopen"

"""
file:///install.inf
file:///book.py
file:///language.py
file:///util.py

https://microsoft.github.io/language-server-protocol/specifications/specification-current/#exit

#TODO
* print server stderr to Output panel
* test hover with OS scaled
* handle lite lexers
* test older python

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
* separate log panel for server's `LogMessage`
* unregistering capabilities


#TODO features
+ Document:
    codeAction
    codeLens
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

def get_project_dir():
    """ choose root directory for server: .opt_root_dir_source
    """
    import cuda_project_man

    path = None
    for optval in opt_root_dir_source:
        if optval == 0: # project file dir
            path = cuda_project_man.project_variables()["ProjDir"]
        elif optval == 1: # first node
            _nodes = cuda_project_man.global_project_info.get('nodes')
            path = _nodes[0] if _nodes else None
        elif optval == 2: # project's main-file dir
            path = cuda_project_man.global_project_info.get('mainfile')
            if path:
                path = os.path.dirname(path)

        if path:
            return path

def get_project_lsp_cfg_path():
    import cuda_project_man

    fn_proj = cuda_project_man.global_project_info.get('filename')
    if fn_proj:
        return os.path.splitext(fn_proj)[0] + '.cuda-proj-lsp'

def get_project_lsp_cfg():
    fn_cfg = get_project_lsp_cfg_path()
    if fn_cfg  and  os.path.exists(fn_cfg):
        with open(fn_cfg, 'r', encoding='utf-8') as f:
            return _json_loads(f.read())


def get_server_cfg_fns():
    if os.path.exists(dir_settings):
        _fns = os.listdir(dir_settings)
        _lsp_fns = [name for name in _fns  if name.startswith('lsp_')
                                                and name.lower().endswith('.json')]
        return _lsp_fns

class Command:

    def __init__(self):
        self.is_loading_sesh = False
        # editors not on_open'ed durisg sesh-load;  on_open visibles when sesh loaded
        self._sesh_eds = []
        self._langs = {} # langid -> Language
        self._book = None
        self._project_dir = None

        self._load_config()

        # hover dlg calls these with "caret" named param, caret is tuple(caretx, carety)
        self._hint_cmds = {
            _('Definition'):        self.call_definition,
            _('References'):        self.call_references,
            _('Implementation'):    self.call_implementation,
            _('Declaration'):       self.call_declaration,
            _('Type definition'):   self.call_typedef,
        }
        ### filter commands by option: opt_hover_additional_commands
        # to lower case for case-insensitive comparison
        _user_cmds = {name.lower() for name in opt_hover_additional_commands}
        for name in self._hint_cmds:
            if name.lower() not in _user_cmds: # if removed by user
                self._hint_cmds[name] = None # None values are dimmed in hover

        ### dbg
        if opt_manual_didopen:
            # first call only starts server, subsequent - send didOpen
            app_proc(PROC_SET_SUBCOMMANDS, 'cuda_lsp;force_didopen;LSP-Open-Doc\t')

    @property
    def book(self):
        if self._book is None:
            from .book import DocBook

            self._book = DocBook()
        return self._book

    def config(self):
        self._save_config()
        file_open((fn_config, fn_opt_descr))
        se = Editor(ed.get_prop(PROP_HANDLE_SECONDARY))
        se.set_prop(PROP_WRAP, True)

        # underline demo
        underline = {'"solid"':  1, '"dotted"': 4, '"dashes"': 2, '"wave"':   6, }
        _colors = app_proc(PROC_THEME_UI_DICT_GET, '')
        err_col = _colors['EdMicromapSpell']['color']
        for text,style in underline.items():
            res = se.action(EDACTION_FIND_ONE, text, "c")
            if res:
                se.attr(MARKERS_ADD, x=res[0], y=res[1], len=res[2]-res[0],
                            color_border=err_col, border_down=style)

    def config_server(self):
        fn_cfg = get_project_lsp_cfg_path()
        if fn_cfg:
            if not os.path.exists(fn_cfg):
                with open(fn_cfg, 'w', encoding='utf-8') as f:
                    f.write('{\n    \n}\n')
            file_open(fn_cfg)
            ed.set_prop(PROP_LEXER_FILE, 'JSON')
        else:
            msg_status(_('No project opened'))

    def edit_server_cfg(self):
        _lsp_fns = get_server_cfg_fns() # in `dir_settings`
        if _lsp_fns:
            _lsp_fns.sort()
            ind = dlg_menu(DMENU_LIST, _lsp_fns, caption=_('Choose config to edit'))
            if ind is not None:
                file_open(os.path.join(dir_settings, _lsp_fns[ind]))
        else:
            msg_status(_('No server configs exist'))

    #NOTE also gets called for unsaved from session
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

        doc = self.book.get_doc(ed_self) # get existing

        lex = ed_self.get_prop(PROP_LEXER_FILE)
        langid = lex2langid(lex)

        if lex is None  or  langid is None:
            if doc:
                doc.update()
            return


        try:
            lang = self._get_lang(ed_self, langid) # uses dynamic server registrationed
        except ValidationError as ex:
            print(ex)
            return

        pass;       LOG and print(f'on_open: lex, langid, filename:{lex, langid, ed_self.get_filename()} =>> lang:{lang.name if lang else "none"}')

        # if have server for this lexer
        if lang:
            if not doc:
                self.book.new_doc(ed_self)
                doc = self.book.get_doc(ed_self)

            if lang.on_open(doc): # doc's .lang is set only when was actually didOpen-ed
                doc.update(lang=lang)

                if lang.tree_enabled:
                    lang.update_tree(doc)

    def on_focus(self, ed_self):
        doc = self.book.get_doc(ed_self)
        if doc and doc.lang and doc.lang.tree_enabled:
            doc.lang.update_tree(doc)

    def on_save(self, ed_self):
        """ handles changed uri for LSP doc
            * if was unsaved with LSP lexer: will have a LSP 'doc' -- ok
            * lexer change via save is handles in .on_lexer()
        """
        doc = self.book.get_doc(ed_self)
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

    def on_save_pre(self, ed_self):
        doc = self.book.get_doc(ed_self)
        if doc and doc.lang:
            doc.lang.on_save_pre(doc)

    def on_close(self, ed_self):
        doc = self.book.get_doc(ed_self)
        if doc:
            pass;       LOG and print('on_close: '+doc.uri)
            if doc.lang:
                doc.lang.on_close(doc)
            self.book.on_close(ed_self) # deletes doc

    #NOTE: also gets called when document first activated
    def on_lexer(self, ed_self):
        doc = self.book.get_doc(ed_self)
        if doc:
            newlex = ed_self.get_prop(PROP_LEXER_FILE)
            oldlex = doc.lex
            pass;       LOG and print(f'lex changed: {ed_uri(ed_self)}:: {oldlex} => {newlex}')
            if oldlex != newlex:
                if doc.lang:
                    doc.lang.on_close(doc) # sends on_close to server
                doc.update()
                self.on_open(ed_self) # changes/removes lang server of doc
        else:    # create doc if new lexer is supported by lsp server
            self.on_open(ed_self)

    def on_change_slow(self, ed_self):
        doc = self.book.get_doc(ed_self)
        if doc and doc.lang:
            # tree update sends changes by itself
            if doc.lang.tree_enabled and doc.lang.update_tree(doc):
                return
            if not opt_send_change_on_request:
                doc.lang.send_changes(doc)


    @command
    def on_complete(self, ed_self):
        doc = self.book.get_doc(ed_self)
        if doc and doc.lang:
            return doc.lang.on_complete(doc)

    def on_snippet(self, ed_self, snippet_id, snippet_text):
        """ for Editor.complete_alt()
        """
        for lang in self._langs.values():
            if lang.on_snippet(ed_self, snippet_id, snippet_text):
                break

    def on_mouse_stop(self, ed_self, x, y):
        if not opt_enable_mouse_hover:      return
        # require Control pressed
        if app_proc(PROC_GET_KEYSTATE, '') != 'c':
            return
        if Hint.is_under_cursor()  or  Hint.is_visible():      return

        doc = self.book.get_doc(ed_self)
        if ed_self.get_prop(PROP_FOCUSED):
            caret = ed_self.convert(CONVERT_PIXELS_TO_CARET, x, y, "")
            self.call_hover(ed_self, caret)

    #@command
    def on_func_hint(self, ed_self):
        doc = self.book.get_doc(ed_self)
        if doc  and  doc.lang  and  ed_self.get_prop(PROP_FOCUSED):
            doc.lang.request_sighelp(doc)
            return True
            
    def on_caret_slow(self, ed_self):
        if SignaturesDialog.is_visible():
            self.on_func_hint(ed_self)
            
    def on_scroll(self, ed_self):
        print('event: on scroll')
        if SignaturesDialog.is_visible():
            SignaturesDialog.hide()

    def on_goto_def(self, ed_self):
        self.call_definition(ed_self)

    def on_app_deactivate(self, ed_self):
        SignaturesDialog.hide()
        
    def on_tab_change(self, ed_self):
        if SignaturesDialog.is_visible():
            SignaturesDialog.hide()
        
        doc = self.book.get_doc(ed_self)
        if doc  and  doc.lang:
            doc.lang.on_ed_shown(doc)
        else: # look for matching server if not already opened
            self.on_open(ed_self)


    def on_lang_inited(self, name):
        """ when server initialized properly (handshake done) - send 'didOpen'
                for documents of this server
                (for visible editors, others - from on_tab_change())
            * if couldn't find - got shutdown before initted
        """
        for lang in self._langs.values():
            if lang.name == name:
                for doc in self.book.get_docs():
                    if doc.lang is None  and  is_ed_visible(doc.ed):
                        self.on_open(doc.ed)
                break # found initted lang

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

        elif state == APPSTATE_PROJECT:
            new_project_dir = get_project_dir()
            if self._project_dir != new_project_dir  and  self._langs:
                _collapsed_path = collapse_path(new_project_dir)
                print(f'{LOG_NAME}: project root folder changed: {_collapsed_path}; notifying servers...')

                for name,lang in list(self._langs.items()):
                    handled = lang.on_rootdir_change(new_project_dir)
                    if not handled:
                        self.shutdown_server(name=name)

                # on_open visible eds/docs without lang
                for edt in get_visible_eds():
                    doc = self.book.get_doc(edt)
                    if not doc  or  not doc.lang:
                        self.on_open(edt)

        elif state == APPSTATE_THEME_UI:
            from .dlg import PanelLog

            PanelLog.on_theme_change()
            SignaturesDialog.on_theme_change()


    def on_key(self, ed_self, key, state):
        # VK_ESCAPE==27
        if key==27 and 'c' not in state and 's' not in state and 'a' not in state:
            if Hint.is_visible():
                Hint.hide()
                return False
            if SignaturesDialog.is_visible():
                SignaturesDialog.hide()
                return False

    def on_exit(self, *args, **vargs):
        #### save state before exiting
        state_changed = False
        for langid,lang in self._langs.items():
            key,state = lang.get_state_pair()
            if state:
                STATE[key] = state
            elif STATE.pop(key, None):
                state_changed = True
        ## if something was removed or state not empty - save
        if state_changed or STATE:
            # if started lang server - json will be imported;  otherwise no need to save server state
            try:
                js = json.dumps(STATE, indent=2)
                with open(fn_state, 'w', encoding='utf-8') as f:
                    f.write(js)
            except NameError: # for missing json
                pass


        # start servers shutdown
        for langid,lang in self._langs.items():
            lang.shutdown()

        _start = time.time()
        while self._langs:
            time.sleep(SEVERS_SHUTDOWN_CHECK_PERIOD)
            for key,lang in list(self._langs.items()):
                lang.process_queues()
                if lang.is_client_exited():
                    del self._langs[key]

            if time.time() - _start > SEVERS_SHUTDOWN_MAX_TIME:
                break

        if not IS_WIN:
            try:
                os.waitpid(-1, os.WNOHANG) # -1 -- any child
            except ChildProcessError:
                pass


    def _get_lang(self, ed_self, langid):
        # to not import json from on_exit
        global json
        import json

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
                    from copy import deepcopy
                    from .language import Language

                    cfg = deepcopy(cfg)

                    # update server 'settings' with project's lsp server settings
                    proj_lsp_cfg = get_project_lsp_cfg()
                    cfg.setdefault('settings', {}).update(proj_lsp_cfg or {})

                    self._project_dir = get_project_dir()
                    cfg['work_dir'] = self._project_dir

                    try:
                        lang = Language(cfg,
                                cmds=self._hint_cmds,
                                lintstr=opt_lint_type,
                                underline_style=LINT_STYLE_MAP[opt_lint_underline_style],
                                state=STATE.get(cfg.get('name'))
                        )
                    except ValidationError:
                        if cfg in servers_cfgs:
                            servers_cfgs.remove(cfg) # dont nag on every on_open
                        raise
                    pass;       LOG and print(f'*** Created lang({lang.name}) for {ed_self, langid}')
                    # register server to all its supported langids
                    for server_langid in lang.langids:
                        self._langs[server_langid] = lang
                    break

        return self._langs.get(langid)

    @command
    def call_hover(self, ed_self=None, caret=None):
        """ caret - if present - used instead of actual caret (for hovered stuff)
        """
        ed_self = ed_self or ed
        doc = self.book.get_doc(ed_self)
        if doc and doc.lang:
            doc.lang.on_hover(doc, caret)

    @command
    def call_definition(self, ed_self=None, caret=None):
        ed_self = ed_self or ed
        doc = self.book.get_doc(ed_self)
        if doc and doc.lang:
            doc.lang.request_definition_loc(doc, caret)

    @command
    def call_references(self, caret=None):
        doc = self.book.get_doc(ed)
        if doc and doc.lang:
            doc.lang.request_references_loc(doc, caret)

    @command
    def call_implementation(self, caret=None):
        doc = self.book.get_doc(ed)
        if doc and doc.lang:
            doc.lang.request_implementation_loc(doc, caret)

    @command
    def call_declaration(self, caret=None):
        doc = self.book.get_doc(ed)
        if doc and doc.lang:
            doc.lang.request_declaration_loc(doc, caret)

    @command
    def call_typedef(self, caret=None):
        doc = self.book.get_doc(ed)
        if doc and doc.lang:
            doc.lang.request_typedef_loc(doc, caret)


    @command
    def call_format_doc(self):
        doc = self.book.get_doc(ed)
        if doc and doc.lang:
            doc.lang.request_format_doc(doc)

    @command
    def call_format_sel(self):
        doc = self.book.get_doc(ed)
        if doc and doc.lang:
            doc.lang.request_format_sel(doc)


    def dbg_call_hierarchy_in(self):
        doc = self.book.get_doc(ed)
        if doc and doc.lang:
            doc.lang.call_hierarchy_in(doc)


    def dbg_workspace_symbols(self):
        doc = self.book.get_doc(ed)
        if doc and doc.lang:
            doc.lang.workspace_symbol(doc)


    def dbg_show_msg(self, show_bytes=False):
        lex = ed.get_prop(PROP_LEXER_FILE)
        langid = lex2langid(lex)
        lang = self._langs.get(langid)
        if lang is None:
            msg_status(_('No messages for server of current document'))
            return

        names = ['msg|' + str(msg)[:SERVER_RESPONSE_DIALOG_LEN]+'...\t'+type(msg).__name__
                        for msg in lang._dbg_msgs]
        ind = dlg_menu(DMENU_LIST, names)

        if ind is not None:
            max_output_width = max(80, ed.get_prop(PROP_SCROLL_HORZ_INFO)['page'])
            if ed.get_filename():
                file_open('')
            import pprint
            try:
                ed.set_text_all(pprint.pformat(lang._dbg_msgs[ind].dict(), width=max_output_width))
            except:
                j = {k:str(v) for k,v in lang._dbg_msgs[ind].dict().items()}
                ed.set_text_all(pprint.pformat(j, width=max_output_width))
            ed.set_prop(PROP_LEXER_FILE, 'Python')

    def dbg_show_docs(self):
        items = [f'{doc.lang}: {doc}' for doc in self.book.get_docs()]
        dlg_menu(DMENU_LIST, items, caption=_('LSP Docs'))

    @command
    # for project folder change
    def shutdown_server(self, name=None):
        # choice dlg
        if name == None:
            names = list(self._langs)
            names.sort()
            ind = dlg_menu(DMENU_LIST, names, caption=_('Shutdown server'))
            if ind is not None:
                name = names[ind]

        # shutting down and clearing remains
        if name is not None  and  name in self._langs:
            lang = self._langs.pop(name)
            lang.shutdown()

            # remove referencees to Language object
            for doc in self.book.get_docs():
                if doc.lang == lang:
                    doc.update(lang=None)

            # server can have multiple langids - remove all
            lang_ids = [langid for langid,lang_ in self._langs.items()  if lang_ == lang]
            for langid in lang_ids:
                del self._langs[langid]

    def shutdown_all_servers(self):
        for name in list(self._langs):
            self.shutdown_server(name=name)

    def force_didopen(self, *args, **vargs):
        ed_self = Editor(ed.get_prop(PROP_HANDLE_SELF))

        _langid = lex2langid(ed_self.get_prop(PROP_LEXER_FILE))
        lang = self._get_lang(ed_self, _langid) # uses dynamic server registrationed
        pass;       LOG and print(f'manual didopen: lex, langid, filename:{_langid, ed_self.get_filename()} =>> lang:{lang.name if lang else "none"}')

        self.book.new_doc(ed_self)
        doc = self.book.get_doc(ed_self)
        lang.on_open(doc)
        doc.update(lang=lang)


    def _load_config(self):
        global opt_enable_mouse_hover
        global opt_root_dir_source
        global opt_manual_didopen
        global opt_send_change_on_request
        global opt_hover_max_lines
        global opt_hover_additional_commands
        global opt_cudatext_in_py_env
        global opt_lint_type
        global opt_lint_underline_style
        global opt_enable_code_tree
        global opt_tree_types_show

        # general cfg
        if os.path.exists(fn_config):
            with open(fn_config, 'r', encoding='utf-8') as f:
                js = f.read()
            j = _json_loads(js)
            opt_enable_mouse_hover = j.get('enable_mouse_hover', opt_enable_mouse_hover)

            _opt_root_dir_source = j.get('root_dir_source', opt_root_dir_source)
            if not isinstance(_opt_root_dir_source, list):  # convert item to list
                _opt_root_dir_source = [_opt_root_dir_source]
            if all(val in {0, 1, 2} for val in _opt_root_dir_source):
                opt_root_dir_source = _opt_root_dir_source
            else:
                print(f'NOTE: {LOG_NAME}: invalid value of option "root_dir_source"'
                        +f' in {fn_config}, accepted values: 0, 1, 2')

            opt_send_change_on_request = j.get('send_change_on_request', opt_send_change_on_request)
            opt_hover_max_lines = j.get('hover_dlg_max_lines', opt_hover_max_lines)
            opt_hover_additional_commands = j.get('hover_additional_commands', opt_hover_additional_commands)
            opt_cudatext_in_py_env = j.get('cudatext_in_py_env', opt_cudatext_in_py_env)
            opt_lint_type = j.get('lint_type', opt_lint_type)

            opt_enable_code_tree = j.get('enable_code_tree', opt_enable_code_tree)
            opt_tree_types_show = j.get('tree_types_show', opt_tree_types_show)

            _opt_lint_underline_style = j.get('lint_underline_style', opt_lint_underline_style)
            if _opt_lint_underline_style in LINT_STYLE_MAP:
                opt_lint_underline_style = _opt_lint_underline_style

            # hidden,dbg
            opt_manual_didopen = j.get('manual_didopen', None)

            # apply options
            Hint.set_max_lines(opt_hover_max_lines)

        # plugin state
        if os.path.exists(fn_state):
            with open(fn_state, 'r', encoding='utf-8') as f:
                js = f.read()
            j = _json_loads(js)
            STATE.update(j)

        # servers
        _lsp_fns = get_server_cfg_fns()
        if _lsp_fns:
            user_lexids = {}

            for name in _lsp_fns:
                path = os.path.join(dir_settings, name)
                with open(path, 'r', encoding='utf-8') as f:
                    try:
                        j = _json_loads(f.read())
                    except:
                        _path = collapse_path(path)
                        print(f'NOTE: {LOG_NAME}: failed to load server config: {_path}')
                        continue

                # load lex map from config
                langids = j.setdefault('langids', [])
                lexmap = j.get('lexers', {})
                for lex,langid in lexmap.items():
                    langids.append(langid)
                    user_lexids[lex] = langid

                # add cuda/py to python server's PYTHONPATH env
                if opt_cudatext_in_py_env  and  'python' in langids:
                    env_paths = j.setdefault('env_paths', {})
                    py_env_paths = env_paths.setdefault('PYTHONPATH', [])
                    py_env_paths.append(app_path(APP_DIR_PY))

                if not langids:
                    _path = collapse_path(path)
                    print(f'NOTE: {LOG_NAME}: server config error - no associated lexers: {_path}')
                    continue
                if 'name' not in j:
                    j['name'] = os.path.splitext(name)[0]
                # tree options
                j.setdefault('enable_code_tree', opt_enable_code_tree)
                j.setdefault('tree_types_show', opt_tree_types_show)

                servers_cfgs.append(j)

            update_lexmap(user_lexids) # add user mapping to global map

        if not servers_cfgs:
            _dir_settings = collapse_path(dir_settings)
            print(f'NOTE: {LOG_NAME}: no server configs loaded from "{_dir_settings}"')


    def _save_config(self):
        import json

        j = {
            'root_dir_source':           opt_root_dir_source,
            'send_change_on_request':    opt_send_change_on_request,
            'enable_mouse_hover':        opt_enable_mouse_hover,
            'hover_dlg_max_lines':       opt_hover_max_lines,
            'hover_additional_commands': opt_hover_additional_commands,
            'cudatext_in_py_env':        opt_cudatext_in_py_env,
            'lint_type':                 opt_lint_type,
            'lint_underline_style':      opt_lint_underline_style,
            'enable_code_tree':          opt_enable_code_tree,
            'tree_types_show':           opt_tree_types_show,
        }
        if opt_manual_didopen is not None:
            j['manual_didopen'] = opt_manual_didopen

        with open(fn_config, 'w', encoding='utf-8') as f:
            json.dump(j, f, indent=2)


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
    @property
    def lcpp(self):
        return self._langs.get('cpp')
    def _killcpp(self):
        lcpp = self._langs.pop('cpp')
        lcpp.shutdown()

    def _kill_servers(self):
        """ only use before exiting
        """
        for lang in self._langs.values():
            try:
                lang.process.kill()
            except:
                pass


# if python too old - give msgbox and disable plugin
ver = sys.version_info
if (ver.major, ver.minor) < (3, 6):
    msg = _('{}: current Python version is not supported. '
            'Please upgrade to Python 3.6 or newer.').format(LOG_NAME)
    callback = lambda *args, msg=msg, **vargs: msg_box(msg, MB_OK or MB_ICONWARNING)
    timer_proc(TIMER_START_ONE, callback, 1000)

    class Command:
        def __getattribute__(self, name):
            return lambda *args, **vargs: None



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


