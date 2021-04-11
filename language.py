import os
import socket
import time
import queue
import subprocess
from threading import Thread
from collections import namedtuple, defaultdict

import email.parser
import email.message

from .wcmatch.glob import globmatch, GLOBSTAR, BRACE

from cudatext import *
import cudax_lib as appx
#from cudax_lib import get_translation

from .util import (
        get_first,
        ed_uri,
        get_visible_eds,
        uri_to_path,
        path_to_uri,
        langid2name,
        collapse_path,
    )
from .dlg import Hint

from .sansio_lsp_client import client as lsp
from .sansio_lsp_client import events
from .sansio_lsp_client.structs import (
        TextDocumentSyncKind,
        TextDocumentContentChangeEvent,
        Registration,
        DiagnosticSeverity,
        Location,
        LocationLink,
        DocumentSymbol,
    )

#_   = get_translation(__file__)  # I18N

import traceback
import datetime

print_server_errors = False
LOG = False
LOG_NAME = 'LSP'

SNIP_ID = 'cuda_lsp__snip'

TCP_CONNECT_TIMEOUT = 5 # sec

GOTO_EVENT_TYPES = {
    events.Definition: '',
    events.References: '',
    events.Implementation: '',
    events.TypeDefinition: '',
    events.Declaration: '',
}

RequestPos = namedtuple('RequestPos', 'h_ed carets mouse_caret')


class Language:
    def __init__(self, cfg):
        self._cfg = cfg

        self.langids = cfg['langids']
        self.lang_str = ', '.join([langid2name(lid) for lid in self.langids])
        self.name = cfg['name'] # "name" from config or config filename (internal)

        self._server_cmd = cfg.get('cmd')
        self._tcp_port = cfg.get('tcp_port') # None => use Popen
        self._work_dir = cfg.get('work_dir')


        # expand user in server start cmd
        if isinstance(self._server_cmd, list):
            self._server_cmd = [os.path.expanduser(c) for c in self._server_cmd]


        self._client = None

        self.request_positions = {} # RequestPos
        self.diagnostics_man = DiagnosticsMan()

        self._closed = False
        self.sock = None
        self.process = None

        self._read_q = queue.Queue()
        self._send_q = queue.Queue()

        self._dbg_msgs = []
        self._dbg_bmsgs = []

    def __str__(self):
        return f'Lang:{self.lang_str}'

    @property
    def client(self):
        if self._client is None:
            root_uri = path_to_uri(self._work_dir) if self._work_dir else None
            self._client = lsp.Client(root_uri=root_uri, process_id=os.getpid())
            self._start_server()
        return self._client

    def is_client_exited(self):
        return self._client.state == lsp.ClientState.EXITED

    def is_ed_matches(self, ed_self, langid):
        if self.client.is_initialized:
            opts = self.scfg.method_opts(METHOD_DID_OPEN, ed_self=ed_self, langid=langid)
            if opts:
                return True
        return False


    def _start_server(self):
        # if config has tcp port - connetct to it
        if self._tcp_port and type(self._tcp_port) == int:
            print(f'{LOG_NAME}: {self.lang_str} - connecting via TCP, port: {self._tcp_port}')

            self.sock = _connect_tcp(port=self._tcp_port)
            if self.sock is None:
                print(f'NOTE:{LOG_NAME}: {self.lang_str} - Failed to connect on port {self.tcp_port}')
                return

            self._reader = self.sock.makefile('rwb')  # type: ignore
            self._writer = self._reader
        # not port - create stdio-process
        else:
            try:
                self.process = subprocess.Popen(
                    args=self._server_cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=self._work_dir,
                    #env=,
                )
            except Exception as ex:
                print(f'NOTE:{LOG_NAME}: {self.lang_str} - Failed to create process, command:'
                        +f' {self._server_cmd}; Error: {ex}')
                return

            self._reader = self.process.stdout
            self._writer = self.process.stdin
            self._err = self.process.stderr

        self.reader_thread = Thread(target=self._read_loop, name=self.name+'-reader', daemon=True)
        self.writer_thread = Thread(target=self._send_loop, name=self.name+'-writer', daemon=True)

        self.reader_thread.start()
        self.writer_thread.start()

        if print_server_errors:
            self.err_thread = Thread(target=self._err_read_loop, name=self.name+'-err', daemon=True)
            self.err_thread.start()

        timer_proc(TIMER_START, self.process_queues, 100, tag='')

        print(f'{LOG_NAME}: Started server: {self.lang_str}')

    def _err_read_loop(self):
        try:
            while self._err:
                line = self._err.readline()
                if line == b'':
                    break
                print(f'ServerError: {LOG_NAME}: {self.lang_str} - {line}') # bytes
        except Exception as ex:
            print(f'ErrReadException: {LOG_NAME}: {self.lang_str} - {ex}')
        pass;       LOG and print(f'NOTE: err reader exited')


    def _read_loop(self):
        try:
            while self._reader:
                try:
                    headers, header_bytes = parse_headers(self._reader)  # type: ignore
                except Exception as ex:
                    print(f'{LOG_NAME}: {self.lang_str} - header parse error: {ex}')
                    pass;       LOG and traceback.print_exc()
                    continue

                if header_bytes == b'':
                    pass;       LOG and print('NOTE: reader stopping')
                    res = os.waitpid(-1, os.WNOHANG)
                    pass;       LOG and print(f'+ wait result: {res}')
                    break

                try:
                    body = self._reader.read(int(headers.get("Content-Length")))
                    self._read_q.put(header_bytes + body)
                except Exception as ex:
                    print(f'BodyReadError: {LOG_NAME}: {self.lang_str} - decode error {ex}')
                    pass;       LOG and traceback.print_exc()
                finally:
                    del body
                    del headers
                    del header_bytes
        #except (AttributeError, BrokenPipeError, TypeError) as ex:
            #print("ExpectedException: ? " + str(ex))
        except Exception as ex:
            print(f'ReadLoopError: {LOG_NAME}: {self.lang_str} - {ex}')
        self._send_q.put_nowait(None) # stop send_loop()

    def _send_loop(self):
        exception = None  # type: Optional[Exception]
        try:
            while self._writer:
                buf = self._send_q.get()
                if buf is None:
                    break
                self._writer.write(buf)
                self._writer.flush()
        #except (BrokenPipeError, AttributeError):
            #pass
        except Exception as ex:
            exception = ex
        pass;       LOG and print('send loop stop exc?:' + str(exception))


    def _on_lsp_msg(self, msg):
        self._dbg_msgs = (self._dbg_msgs + [msg])[-512:]

        msgtype = type(msg)

        if msgtype == events.Initialized:
            self.scfg = ServerConfig(msg, self.langids, self.lang_str)
            app_proc(PROC_EXEC_PLUGIN, 'cuda_lsp,on_lang_inited,'+self.name)

        elif msgtype == events.RegisterCapabilityRequest:
            self.scfg.on_register(msg)
            msg.reply() # send confirmation reply to server
            self.process_queues()
            app_proc(PROC_EXEC_PLUGIN, 'cuda_lsp,on_lang_inited,'+self.name)

        elif msgtype == events.Completion:
            items = msg.completion_list.items
            pass;       LOG and print(f'got completion({len(items)}): {time.time():.3f} {msg.message_id} in {list(self.request_positions)}')
            reqpos = self.request_positions.pop(msg.message_id, None)
            if items  and  reqpos:
                compl = CompletionMan(carets=reqpos.carets, h_ed=reqpos.h_ed)
                compl.show_complete(msg.message_id, items)
                self._last_complete = (compl, msg.message_id, items)

        elif msgtype == events.Hover:
            if msg.message_id in self.request_positions:
                _reqpos = self.request_positions.pop(msg.message_id)
                if ed.get_prop(PROP_HANDLE_SELF) == _reqpos.h_ed:
                    Hint.show(msg.m_str(), caret=_reqpos.mouse_caret) # msg has .range, .contents

        elif msgtype == events.SignatureHelp:
            if msg.message_id in self.request_positions:
                _reqpos = self.request_positions.pop(msg.message_id)
                if ed.get_prop(PROP_HANDLE_SELF) == _reqpos.h_ed:
                    hint = msg.get_hint_str()
                    if hint:
                        msg_status_alt(hint, 8) # 8 - default duration

        #GOTOs
        elif msgtype in GOTO_EVENT_TYPES:
            skip_dlg = msgtype == events.Definition
            self.do_goto(items=msg.result, dlg_caption=f'Go to {msgtype.__name__}', skip_dlg=skip_dlg)

        elif msgtype == events.MDocumentSymbols:
            self.show_symbols(msg.result)

        elif msgtype == events.PublishDiagnostics:
            self.diagnostics_man.set_diagnostics(uri=msg.uri, diag_list=msg.diagnostics)

        elif msgtype == events.LogMessage:
            if msg.message == getattr(self, '_last_lsp_log', None): #WTF every log duplicated
                return
            self._last_lsp_log = msg.message
            lines = msg.message.split('\n')
            app_log(LOG_ADD, 'LSP_MSG:{}: {}'.format(msg.type.name, lines[0]), panel=LOG_PANEL_OUTPUT)
            for line in lines[1:]:
                app_log(LOG_ADD, line, panel=LOG_PANEL_OUTPUT)

        elif msgtype == events.Shutdown:
            print(f'{LOG_NAME}: {self.lang_str} - got shutdown response, exiting')
            self.client.exit()
            self.process_queues()
            self.exit()

        else:
            print(f'{LOG_NAME}: {self.lang_str} - unknown Message type: {msgtype}')


    #NOTE call immediately after adding send events, to send faster
    def process_queues(self, tag='', info=''):
        try:
            while not self._read_q.empty():
                data = self._read_q.get()
                self._dbg_bmsgs = (self._dbg_bmsgs + [data])[-128:] # dbg

                events = self.client.recv(data)
                for msg in events:
                    self._on_lsp_msg(msg)

            send_buf = self.client.send()
            if send_buf:
                self._send_q.put(send_buf)
        except Exception as ex:
            print(f'QueuesProcessingError: {LOG_NAME}: {self.lang_str} - {ex}')
            pass;       LOG and traceback.print_exc()

    def send_changes(self, eddoc):
        if not self.client.is_initialized:
            pass;       LOG and print('send_changes return: not initted client')
            return

        opts = self.scfg.method_opts(METHOD_DID_CHANGE, eddoc)
        if opts is None:
            pass;       LOG and print('NOTE: send_changes return: no opts')
            return

        docsynckind = TextDocumentSyncKind( opts.get('syncKind', TextDocumentSyncKind.NONE) )
        if docsynckind == TextDocumentSyncKind.NONE:
            pass;       LOG and print('send_changes return: NONE sync')
            return

        if docsynckind == TextDocumentSyncKind.INCREMENTAL:
            _changes = eddoc.get_changes()
            if not _changes:
                pass;       LOG and print('send_changes return: no changes')
                return
        else: # TextDocumentSyncKind.FULL:
            _changes = [TextDocumentContentChangeEvent(text=eddoc.get_text_all())]

        _verdoc = eddoc.get_verdoc()
        self.client.did_change(text_document=_verdoc, content_changes=_changes)

    def on_ed_shown(self, eddoc):
        self.diagnostics_man.on_doc_shown(eddoc)

    def on_open(self, eddoc):
        if self.client.is_initialized:
            opts = self.scfg.method_opts(METHOD_DID_OPEN, eddoc)
            if opts is not None  and  eddoc.lang is None:
                pass;       LOG and print('  ----- starting [didOpen] '+eddoc.uri)
                eddoc.on_open(lang=self)
                doc = eddoc.get_textdoc()
                self.client.did_open(doc)
                return True

    def on_close(self, eddoc):
        if self.client.is_initialized:
            opts = self.scfg.method_opts(METHOD_DID_CLOSE, eddoc)
            if opts is not None  and  eddoc.lang is not None: # lang check -- is opened
                pass;       LOG and print(' --- closing '+eddoc.uri)

                self.send_changes(eddoc) # for later: server can give edits on save

                docid = eddoc.get_docid()
                self.client.did_close(docid)

                eddoc.on_close()

    def on_save(self, eddoc):
        if self.client.is_initialized:
            # server asked for save notifications
            opts = self.scfg.method_opts(METHOD_DID_SAVE, eddoc)
            if opts is not None:
                self.send_changes(eddoc)

                include_text = opts.get('includeText', False)

                docid = eddoc.get_docid()
                text = eddoc.ed.get_text_all() if  include_text  else None
                self.client.did_save(text_document=docid, text=text)


    def _action_by_name(self, method_name, eddoc, x=None, y=None):
        if self.client.is_initialized:
            opts = self.scfg.method_opts(method_name, eddoc)
            if opts is None:
                msg_status(f'Method is not supported by server: {method_name}')
                return None,None

            docpos = eddoc.get_docpos(x, y)
            if docpos is None: # invalid caret position
                return None,None

            self.send_changes(eddoc)

            methodAttrName = method_name.split('/')[1]
            clientMethod = getattr(self.client, methodAttrName)
            id = clientMethod(docpos)
            self.process_queues()
            pass;       LOG and print(f' >> GUI:sent {method_name} request: {id}, time:{time.time():.3f}')
            return id, (docpos.position.character, docpos.position.line)
        return None,None #TODO fix ugly


    def on_complete(self, eddoc):
        id, pos = self._action_by_name(METHOD_COMPLETION, eddoc)
        if id is not None:
            self._save_req_pos(id=id)
            return True

    def on_snippet(self, ed_self, snippet_id, snippet_text): # completion callback
        if snippet_id == SNIP_ID:
            compl, message_id, items = self._last_complete
            compl.do_complete(message_id, snippet_text, items)


    def on_hover(self, eddoc, x=None, y=None):
        """ just sends request to server, dsiplaying stuff in 'dlg.py/Hint'
        """
        id, pos = self._action_by_name(METHOD_HOVER, eddoc, x=x, y=y)
        if id is not None:
            self._save_req_pos(id=id, mouse_caret=pos)

    def do_goto(self, items, dlg_caption, skip_dlg=False):
        """ items: Location or t.List[t.Union[Location, LocationLink]], None
        """
        def link_to_target(link): #SKIP
            """ returns: (uri, goto-range)
            """
            if isinstance(link, Location):
                return (link.uri, link.range)
            elif isinstance(link, LocationLink):
                return (link.targeturi, targetSelectionRange)
            else:
                raise Exception('Invalid goto-link type: '+str(type(link)))

        if not items:
            msg_status(f'{LOG_NAME}: {self.lang_str} - no results for "{dlg_caption}"')
            return

        if isinstance(items, list):
            targets = (link_to_target(item) for item in items)
            targets = ((uri_to_path(uri),range_) for uri,range_ in targets) # uri to path

            if skip_dlg:
                uri,targetrange = next(targets) # first
            else:
                targets = list(targets)
                names = [f'{os.path.basename(path)}, line {range_.start.line+1}\t{collapse_path(path)}'
                            for path,range_ in targets]
                ind = dlg_menu(DMENU_LIST_ALT, names, caption=dlg_caption)
                if ind is None:
                    return
                uri,targetrange = targets[ind]

        else: # items is single item
            uri,targetrange = link_to_target(items)

        targetpath = uri_to_path(uri)
        file_open(targetpath)
        app_idle(True) # fixes editor not scrolled to caret
        ed.set_caret(targetrange.start.character, targetrange.start.line) # goto specified position start
        ed.set_prop(PROP_LINE_TOP, max(0, targetrange.start.line-3))


    def show_symbols(self, items):
        # DocumentSymbol - hierarchy
        def flatten(l, parent, items): #SKIP
            if items is None  or  len(items) == 0:      return

            l.extend(( (item.name, parent or '', item.kind, item.selectionRange)  for item in items ))
            # recurse children
            map(flatten, ((l, item.name, item.children) for item in items  if item.children))

        if items is None  or  len(items) == 0:
            pass;       LOG and print(f'no symbols')
            return

        if type(items[0]) == DocumentSymbol: # hierarchy - flatten
            targets = []
            flatten(targets, '', items)
        else: # SymbolInformation -> (name,parent,kind,loc)
            targets = [(item.name, item.containerName or '', item.kind, item.location)  for item in items]

        targets.sort(key=lambda t: (t[0].lower(), t[1].lower(), t[2])) # loc -- Location or Range

        dlg_items = [f'{name}\t{kind.name.title()} {" in "+parent if parent else ""}'
                        for name,parent,kind,loc in targets]
        dlg_menu(DMENU_LIST_ALT, dlg_items, caption='Go to symbol')



    # GOTOs
    def request_sighelp(self, eddoc):
        id, pos = self._action_by_name(METHOD_SIG_HELP, eddoc)
        if id is not None:
            self._save_req_pos(id=id, mouse_caret=pos)

    def request_definition_loc(self, eddoc):
        id, pos = self._action_by_name(METHOD_DEFINITION, eddoc)
        if id is not None:
            self._save_req_pos(id=id, mouse_caret=pos)

    def request_references_loc(self, eddoc):
        id, pos = self._action_by_name(METHOD_REFERENCES, eddoc)
        if id is not None:
            self._save_req_pos(id=id, mouse_caret=pos)

    def request_implementation_loc(self, eddoc):
        id, pos = self._action_by_name(METHOD_IMPLEMENTATION, eddoc)
        if id is not None:
            self._save_req_pos(id=id, mouse_caret=pos)

    def request_declaration_loc(self, eddoc):
        id, pos = self._action_by_name(METHOD_DECLARATION, eddoc)
        if id is not None:
            self._save_req_pos(id=id, mouse_caret=pos)

    def request_typedef_loc(self, eddoc):
        id, pos = self._action_by_name(METHOD_TYPEDEF, eddoc)
        if id is not None:
            self._save_req_pos(id=id, mouse_caret=pos)


    def doc_symbol(self, eddoc):
        if self.client.is_initialized:
            opts = self.scfg.method_opts(METHOD_DOC_SYMBOLS, eddoc)
            if opts is not None  and  eddoc.lang is not None: # lang check -- is opened
                self.send_changes(eddoc) # for later: server can give edits on save

                docid = eddoc.get_docid()
                self.client.doc_symbol(docid)


    def call_hierarchy_in(self, eddoc):
        self.send_changes(eddoc)

        docpos = eddoc.get_docpos()
        id = self.client.call_hierarchy_in(docpos)


    def workspace_symbol(self, eddoc):
        self.client.workspace_symbol(query='')


    def shutdown(self, *args, **vargs):
        pass;       LOG and print('-- lang - shutting down')
        self.client.shutdown()

    def exit(self):
        if not self._closed:
            self._send_q.put_nowait(None) # stop send_loop()
            self.process_queues()

            if self.sock:
                self.sock.close()

            """flog('closing proc 0')
            if self.process:
                #self.process.kill()
                self.process.terminate()
                self.process.wait()
            flog('closing proc 34')"""

            self._closed = True
            timer_proc(TIMER_STOP, self.process_queues, 0)


    def _save_req_pos(self, id, mouse_caret=None):
        h_ed = ed.get_prop(PROP_HANDLE_SELF)
        carets = ed.get_carets()
        self.request_positions[id] = RequestPos(h_ed=h_ed, carets=carets, mouse_caret=mouse_caret)

    def _dbg_print_registrations(self):
        import pprint
        print('*** registrations: ', pprint.pformat(self.scfg.capabs))


def _connect_tcp(port):
    start_time = time.time()
    while time.time() - start_time < TCP_CONNECT_TIMEOUT:
        try:
            return socket.create_connection(('localhost', port))
        except ConnectionRefusedError:
            pass
    return None


DIAG_BM_TAG = app_proc(PROC_GET_UNIQUE_TAG, '') # jic
_icons_dir = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'icons')
DIAG_BM_IC_PATHS = {
    DiagnosticSeverity.ERROR       : os.path.join(_icons_dir, 'error.png'),
    DiagnosticSeverity.WARNING     : os.path.join(_icons_dir, 'warning.png'),
    DiagnosticSeverity.INFORMATION : os.path.join(_icons_dir, 'information.png'),
    DiagnosticSeverity.HINT        : os.path.join(_icons_dir, 'hint.png'),
}
DIAG_BM_KINDS = {
    DiagnosticSeverity.ERROR       : 50,
    DiagnosticSeverity.WARNING     : 51,
    DiagnosticSeverity.INFORMATION : 52,
    DiagnosticSeverity.HINT        : 53,
}
DIAG_DEFAULT_SEVERITY = DiagnosticSeverity.INFORMATION # *shrug*

class DiagnosticsMan:
    """ * Command.on_tab_change() ->
            <lang>.on_ed_shown(<new visible editor = eddoc>) ->
                this.on_doc_shown(<eddoc>) -- clear,reaply diags if visible
        * this.set_diagnostics() ->
            - set dirt if not visible
            - clear,reaply diags if visible
    """

    def __init__(self):
        self.uri_diags = {} # uri -> diag?
        self.dirtys = set() # uri

        # load icons, disable line highlight
        for severity,kind in DIAG_BM_KINDS.items():
            _icon_path = DIAG_BM_IC_PATHS[severity]
            ed.bookmark(BOOKMARK_SETUP, 0, nkind=kind, ncolor=COLOR_NONE, text=_icon_path)

    def on_doc_shown(self, eddoc):
        # if dirty - update
        if eddoc.uri in self.dirtys:
            self.dirtys.remove(eddoc.uri)

            self._apply_diagnostics(eddoc.ed, self.uri_diags[eddoc.uri])

    def set_diagnostics(self, uri, diag_list):
        if len(diag_list) > 0:
            self.uri_diags[uri] = diag_list
            for ed in get_visible_eds():
                if uri == ed_uri(ed):
                    self._apply_diagnostics(ed, diag_list)
            else: # not visible, update when visible
                self.dirtys.add(uri)

    def _apply_diagnostics(self, ed, diag_list):
        # clear old
        ed.bookmark(BOOKMARK_DELETE_BY_TAG, 0, tag=DIAG_BM_TAG)

        # set new
        line_diags = defaultdict(list) # line -> list of diagnostics
        for d in diag_list:
            line_diags[d.range.start.line].append(d)

        for nline,diags in line_diags.items():
            diags.sort(key=lambda d: d.severity or 9) # important first, None - last

            msg_lines = []
            for d in diags:
                kind = DIAG_BM_KINDS.get(d.severity, DIAG_DEFAULT_SEVERITY)
                #TODO fix ugly... (.severity and .code -- can be None)
                pre,post = ('[',']: ') if (d.severity is not None  or  d.code) else  ('','')
                mid = ':' if (d.severity is not None  and  d.code) else ''

                severity_short = d.severity.short_name() if d.severity else ''
                # "[severity:code] message"
                code = str(d.code)  if d.code is not None else  ''
                text = ''.join([pre, severity_short, mid, code, post, d.message])
                msg_lines.append(text)

            ed.bookmark(BOOKMARK_SET, nline=nline, nkind=kind, text='\n'.join(msg_lines), tag=DIAG_BM_TAG)


METHOD_DID_OPEN         = 'textDocument/didOpen'
METHOD_DID_CLOSE        = 'textDocument/didClose'
METHOD_DID_SAVE         = 'textDocument/didSave'
METHOD_DID_CHANGE       = 'textDocument/didChange'

METHOD_COMPLETION       = 'textDocument/completion'
METHOD_HOVER            = 'textDocument/hover'
METHOD_SIG_HELP         = 'textDocument/signatureHelp'
METHOD_DEFINITION       = 'textDocument/definition'
METHOD_REFERENCES       = 'textDocument/references'
METHOD_IMPLEMENTATION   = 'textDocument/implementation'
METHOD_DECLARATION      = 'textDocument/declaration'
METHOD_TYPEDEF          = 'textDocument/typeDefinition'
METHOD_DOC_SYMBOLS      = 'textDocument/documentSymbol'

CAPABILITY_DID_OPEN         = 'textDocument.didOpen'
CAPABILITY_DID_CLOSE        = 'textDocument.didClose'
CAPABILITY_DID_SAVE         = 'textDocument.didSave' # options: (supported, includeText)
CAPABILITY_DID_CHANGE       = 'textDocument.didChange' # option: TextDocumentSyncKind
CAPABILITY_COMPLETION       = 'textDocument.completion'
CAPABILITY_HOVER            = 'textDocument.hover'
CAPABILITY_SIG_HELP         = 'textDocument.signatureHelp'
CAPABILITY_DEFINITION       = 'textDocument.definition'
CAPABILITY_REFERENCES       = 'textDocument.references'
CAPABILITY_IMPLEMENTATION   = 'textDocument.implementation'
CAPABILITY_DECLARATION      = 'textDocument.declaration'
CAPABILITY_TYPEDEF          = 'textDocument.typeDefinition'
CAPABILITY_DOC_SYMBOLS      = 'textDocument.documentSymbol'

METHOD_PROVIDERS = {
    METHOD_COMPLETION       : 'completionProvider',
    METHOD_HOVER            : 'hoverProvider',
    METHOD_SIG_HELP         : 'signatureHelpProvider',
    METHOD_DEFINITION       : 'definitionProvider',
    METHOD_REFERENCES       : 'referencesProvider',
    METHOD_IMPLEMENTATION   : 'implementationProvider',
    METHOD_DECLARATION      : 'declarationProvider',
    METHOD_TYPEDEF          : 'typeDefinitionProvider',
    METHOD_DOC_SYMBOLS      : 'documentSymbolProvider',

    #METHOD_WS_SYMBOLS       : '',
}


class ServerConfig:
    def __init__(self, initialized, langids, lang_str):
        capabilities = initialized.capabilities
        self.capabs = [] # struct.Registration
        self.lang_str = lang_str

        _default_selector = [{'language': langid}  for langid in langids]
        _default_opts = {'documentSelector': _default_selector}

        docsync = capabilities.get('textDocumentSync', {})

        ### ~pseudo-registrations
        #  OPEN, CLOSE
        if docsync.get('openClose', False) is not False:
            open = Registration(id='0', method=METHOD_DID_OPEN, registerOptions=_default_opts)
            close = Registration(id='0', method=METHOD_DID_CLOSE, registerOptions=_default_opts)
            self.capabs += [open, close]

        _save = docsync.get('save', False) # save?: boolean | SaveOptions;

        # SAVE
        if _save is not False:
            _opts = {**_default_opts}
            if isinstance(_save, dict):
                _opts.update(_save)
            self.capabs.append(Registration(id='0', method=METHOD_DID_SAVE, registerOptions=_opts))

        # CHANGE
        if 'change' in docsync:
            _default_sync = int(TextDocumentSyncKind.NONE)
            _docsynckind = TextDocumentSyncKind(docsync.get('change', _default_sync))
            _opts = {**_default_opts, 'syncKind': _docsynckind}
            self.capabs.append(Registration(id='0', method=METHOD_DID_CHANGE, registerOptions=_opts))

        # ~other static capabilites
        for meth,prov in METHOD_PROVIDERS.items():
            capval = capabilities.get(prov, False)
            if capval is False:
                continue

            _opts = {**_default_opts}
            if isinstance(capval, dict):
                _opts.update(capval)
            self.capabs.append(Registration(id='0', method=meth, registerOptions=_opts))

    def on_register(self, dynreg):
        """ process dynamic registration request: RegisterMethodRequest
        """
        self.capabs.extend(dynreg.registrations)


    def method_opts(self, method_name, doc=None, ed_self=None, langid=None):
        """ returns: options dict or None
        """
        #if method_name is None:
            #method_name = capab_name.replace('.', '/')
        if ed_self is None:
            ed_self = doc.ed
        if langid is None:
            langid = doc.langid

        for registration in self.capabs:
            if registration.method == method_name:
                if ServerConfig.match_capability(registration, ed_self, langid):
                    return registration.registerOptions

        if method_name != METHOD_DID_OPEN:
            print(f'NOTE: {LOG_NAME}: {self.lang_str} - unsupported method: {method_name}')


    # "selector is one ore more filters"
    def match_capability(registration, ed_self, langid):

        filters = registration.registerOptions.get('documentSelector', [])
        # allowing empty selector on workspace methods  (ok?)
        #   example: Registration(id='...', method='workspace/symbol', registerOptions={}),
        if not filters:
            return (registration.method or '').startswith('workspace/')

        return any(ServerConfig.filter_doc_matcher(f, ed_self, langid)  for f in filters)

    def filter_doc_matcher(f, ed_self, langid):
        language = f.get('language')
        if language is not None  and  language != langid:
            return False

        # ignoring 'scheme':  C# has {'scheme': 'csharp'} wtf?

        pattern = f.get('pattern')
        if pattern is not None:
            if not globmatch(ed_self.get_filename() or "", pattern, flags=GLOBSTAR | BRACE):
                return False

        # checking because C# gives empty selector: just by scheme -- scheme is ignored
        # 'True' if have valid condition
        return bool(language) or bool(pattern)


class CompletionMan:
    def __init__(self, carets=None, h_ed=None):
        assert len(carets) == 1, 'no autocomplete for multi-carets'
        assert carets[0][3] == -1, 'no autocomplete for selection'

        self.carets = carets
        self.h_ed = h_ed or ed.get_prop(PROP_HANDLE_SELF)

    def show_complete(self, message_id, items):

        carets = ed.get_carets()

        if self.carets != carets:       return # caret moved
        if self.h_ed != ed.get_prop(PROP_HANDLE_SELF):       return # wrong editor

        lex = ed.get_prop(PROP_LEXER_FILE, '')    #NOTE probably no need to check for lexer

        if lex is None: return
        #if not is_lexer_allowed(lex): return

        words = ['{}\t{}\t{}|{}'.format(item.label, item.kind.name.lower() or '', message_id, i)
                    for i,item in enumerate(items)]

        # results are already seem to be sorted by .sortText

        sel = get_first(i for i,item in enumerate(items)  if item.preselect is True)
        sel = sel or 0

        ed.complete_alt('\n'.join(words), SNIP_ID, len_chars=0, selected=sel)

    #TODO add () and move caret if function?
    def do_complete(self, message_id, snippet_text, items):
        items_msg_id, item_ind = snippet_text.split('|')
        item_ind = int(item_ind)

        if int(items_msg_id) != message_id:
            return

        item = items[item_ind]

        # find position of main edit and new 'text'
        if item.textEdit:
            x1,y1,x2,y2 = CompletionMan.range2carets(item.textEdit.range)
            text = item.textEdit.newText
        else: # no textEdit, just using .label
            _carets = ed.get_carets()
            x0,y0, _x1,_y1 = _carets[0]

            lex = ed.get_prop(PROP_LEXER_FILE, '')
            self._nonwords = appx.get_opt(
                'nonword_chars',
                '''-+*=/\()[]{}<>"'.,:;~?!@#$%^&|`…''',
                appx.CONFIG_LEV_ALL,
                ed,
                lex)

            word = self._get_word(x0, y0)

            if not word: return # defective caret pos
            word1, word2 = word

            x1 = x0-len(word1)
            x2 = x0+len(word2)
            y1 = y2 = y0

            if (item.insertText  and
                    (item.insertTextFormat is None or item.insertTextFormat != InsertTextFormat.SNIPPET)):
                text = item.insertText
            else:
                text = item.label

        # main edit
        new_caret = ed.replace(x1,y1,x2,y2, text)
        # move caret at end of inserted text
        if new_caret:
            ed.set_caret(*new_caret)

        # additinal edits
        if item.additionalTextEdits:
            for edit in item.additionalTextEdits:
                x1,y1,x2,y2 = CompletionMan.range2carets(item.textEdit.range)
                ed.replace(x1,y1,x2,y2, item.textEdit.newText)


    def _get_word(self, x, y):
        if not 0<=y<ed.get_line_count():
            return
        s = ed.get_text_line(y)
        if not 0<x<=len(s):
            return

        x0 = x
        while (x0>0) and self._isword(s[x0-1]):
            x0-=1
        text1 = s[x0:x]

        x0 = x
        while (x0<len(s)) and self._isword(s[x0]):
            x0+=1
        text2 = s[x:x0]

        return (text1, text2)

    def _isword(self, s):
        return s not in ' \t'+self._nonwords

    def range2carets(range):
        #x1,y1,x2,y2
        return (range.start.character, range.start.line,  range.end.character, range.end.line,)


### http.client.parse_headers, from  https://github.com/python/cpython/blob/3.9/Lib/http/client.py
# (missing from CudaText)

_MAXLINE = 65536
_MAXHEADERS = 100

class HTTPMessage(email.message.Message):
    # XXX The only usage of this method is in
    # http.server.CGIHTTPRequestHandler.  Maybe move the code there so
    # that it doesn't need to be part of the public API.  The API has
    # never been defined so this could cause backwards compatibility
    # issues.

    def getallmatchingheaders(self, name):
        """Find all header lines matching a given header name.
        Look through the list of headers and find all lines matching a given
        header name (and their continuation lines).  A list of the lines is
        returned, without interpretation.  If the header does not occur, an
        empty list is returned.  If the header occurs multiple times, all
        occurrences are returned.  Case is not important in the header name.
        """
        name = name.lower() + ':'
        n = len(name)
        lst = []
        hit = 0
        for line in self.keys():
            if line[:n].lower() == name:
                hit = 1
            elif not line[:1].isspace():
                hit = 0
            if hit:
                lst.append(line)
        return lst

def parse_headers(fp, _class=HTTPMessage):
    """Parses only RFC2822 headers from a file pointer.
    email Parser wants to see strings rather than bytes.
    But a TextIOWrapper around self.rfile would buffer too many bytes
    from the stream, bytes which we later need to read as bytes.
    So we read the correct bytes here, as bytes, for email Parser
    to parse.
    """
    headers = []

    while True:
        line = fp.readline(_MAXLINE + 1)

        if len(line) > _MAXLINE:
            #raise LineTooLong("header line")
            raise Exception("LineTooLong: header line")
        headers.append(line)
        if len(headers) > _MAXHEADERS:
            #raise HTTPException("got more than %d headers" % _MAXHEADERS)
            raise Exception("HTTPException: got more than %d headers" % _MAXHEADERS)
        if line in (b'\r\n', b'\n', b''):
            break
    header_bytes = b''.join(headers)
    hstring = header_bytes.decode('iso-8859-1')
    return email.parser.Parser(_class=_class).parsestr(hstring), header_bytes
