import sys
import os
import socket
import time
import queue
import subprocess
from threading import Thread
from collections import namedtuple, defaultdict

import email.parser
import email.message

from wcmatch.glob import globmatch, GLOBSTAR, BRACE

from cudatext import *
import cudax_lib as appx

from cudax_lib import get_translation
_ = get_translation(__file__)  # I18N

from .util import (
        get_first,
        ed_uri,
        get_visible_eds,
        get_word,
        get_nonwords_chars,
        uri_to_path,
        path_to_uri,
        langid2lex,
        collapse_path,
        normalize_drive_letter,
        replace_unbracketed,
        TimerScheduler,

        ValidationError,
    )
from .dlg import Hint, SEVERITY_MAP, SignaturesDialog
from .dlg import PanelLog, SEVERITY_ERR, SEVERITY_LOG
from .book import EditorDoc
#from .tree import TreeMan  # imported on access

ver = sys.version_info
if (ver.major, ver.minor) < (3, 7):
    modules36_dir = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'lsp_modules36')
    sys.path.append(modules36_dir)


from .sansio_lsp_client import client as lsp
from .sansio_lsp_client import events
from .sansio_lsp_client.structs import (
        TextDocumentSyncKind,
        Registration,
        DiagnosticSeverity,
        Location,
        LocationLink,
        DocumentSymbol,
        CompletionItemKind,
        MarkupKind,
        MarkedString,
        FormattingOptions,
        WorkspaceFolder,
        InsertTextFormat,
    )
    
from .snip.snippet import Snippet

import traceback
import datetime

LOG = False
DBG = LOG
LOG_NAME = 'LSP'

api_ver = app_api_version()

IS_WIN = os.name=='nt'
IS_MAC = sys.platform=='darwin'
CMD_OS_KEY = 'cmd_windows' if IS_WIN else ('cmd_macos' if IS_MAC else 'cmd_unix')

SNIP_ID = 'cuda_lsp__snip'

TCP_CONNECT_TIMEOUT = 5     # sec
MAX_FORMAT_ON_SAVE_WAIT = 1 # sec
MIN_TIMER_TIME = 10     # ms
MAX_TIMER_TIME = 10    # ms

GOTO_EVENT_TYPES = {
    events.Definition,
    events.References,
    events.Implementation,
    events.TypeDefinition,
    events.Declaration,
}
CALLABLE_COMPLETIONS = {
    CompletionItemKind.METHOD,
    CompletionItemKind.FUNCTION,
    CompletionItemKind.CONSTRUCTOR,
    CompletionItemKind.CLASS,
}

RequestPos = namedtuple('RequestPos', 'h_ed carets target_pos_caret cursor_ed')
CachedCompletion = namedtuple('CachedCompletion', 'obj message_id items filtered_items carets h_ed line_str is_incomplete')

GOTO_TITLES = {
    events.Definition:      _('Go to: definition'),
    events.References:      _('Go to: references'),
    events.Implementation:  _('Go to: implementation'),
    events.TypeDefinition:  _('Go to: type definition'),
    events.Declaration:     _('Go to: declaration'),
}


class Language:
    def __init__(self, cfg, cmds=None, lintstr='', underline_style=None, state=None):
        self._shutting_down = None  # scheduled shutdown when not yet initialized

        self._last_complete = None
        self._cfg = cfg
        self._caret_cmds = cmds # {caption -> callable}

        self.langids = cfg['langids']
        # unique sorted lexers
        self.lang_str = ', '.join(sorted({langid2lex(lid) for lid in self.langids}))
        self.name = cfg['name'] # "name" from config or config filename (internal)

        self._server_cmd = cfg.get(CMD_OS_KEY)
        self._tcp_port = cfg.get('tcp_port') # None => use Popen
        self._work_dir = cfg.get('work_dir')
        # paths to add to env  -- {var_name: list[paths]}
        self._env_paths = cfg.get('env_paths')
        self._log_stderr = bool(cfg.get('log_stderr'))
        self._format_on_save = bool(cfg.get('format_on_save'))

        self._validate_config()

        # expand user paths
        if isinstance(self._server_cmd, list):
            self._server_cmd = [os.path.expanduser(c) for c in self._server_cmd]

        if isinstance(self._env_paths, dict):
            for name,paths in self._env_paths.items():
                self._env_paths[name] = [os.path.expanduser(p) for p in paths]


        self._client = None
        self.plog = PanelLog.get_logger(self.name, state=state)
        self._treeman = None
        # weakref needs a strong reference for a method-ref to work
        self._timer_callback = self.process_queues
        self._timer = TimerScheduler(
                callback=self._timer_callback,
                mintime=MIN_TIMER_TIME,
                maxtime=MAX_TIMER_TIME,
                delta=10,
            )

        self.request_positions = {} # RequestPos
        self.diagnostics_man = DiagnosticsMan(lintstr, underline_style, self.plog)
        self.progresses = {} # token -> progress start message

        self._closed = False
        self.sock = None
        self.process = None
        
        self._reader = None
        self._writer = None
        self._err = None

        self._read_q = queue.Queue()
        self._send_q = queue.Queue()
        self._err_q = queue.Queue()

        self._dbg_msgs = []
        self._dbg_bmsgs = []

        if DBG:
            self.plog.set_lex(ed.get_prop(PROP_LEXER_FILE))


    def __str__(self):
        return f'Lang:{self.lang_str}'

    @property
    def client(self):
        if self._client is None:
            root_uri = path_to_uri(self._work_dir) if self._work_dir else None
            self._client = lsp.Client(
                root_uri=root_uri,
                workspace_folders=self.workspace_folders,
                process_id=os.getpid(),
                settings=self._cfg["settings"]
            )
            self._start_server()
        return self._client

    @property
    def client_state_str(self):
        return (self._client.state.name).title()  if self._client is not None else  'Not started'

    @property
    def workspace_folders(self):
        if self._work_dir:
            # for now just a single folder in workspace
            root_uri = path_to_uri(self._work_dir)
            return [ WorkspaceFolder(uri=root_uri, name='Root'), ]
        else:
            return None

    @property
    def tree_enabled(self):
        return self._cfg.get('enable_code_tree')

    @property
    def treeman(self):
        if not self._treeman  and  self.tree_enabled:
            from .tree import TreeMan

            self._treeman = TreeMan(self._cfg)

        return self._treeman

    def is_client_exited(self):
        return self._client.state == lsp.ClientState.EXITED

    def is_ed_matches(self, ed_self, langid):
        if self.client.is_initialized:
            opts = self.scfg.method_opts(METHOD_DID_OPEN, ed_self=ed_self, langid=langid)
            if opts:
                return True
        return False


    def _start_server(self):

        def connect_via_tcp():
            print(_('{}: {} - connecting via TCP, port: {}').format(
                  LOG_NAME, self.lang_str, self._tcp_port))

            self.sock = _connect_tcp(port=self._tcp_port)
            if self.sock is None:
                print('NOTE: ' + _('{}: {} - Failed to connect on port {}').format(
                      LOG_NAME, self.lang_str, self._tcp_port))
                return

            self._reader = self.sock.makefile('rwb')  # type: ignore
            self._writer = self._reader

        def connect_via_stdin():
            print(_('{}: starting server - {}; root: {}').format(
                  LOG_NAME, self.lang_str, self._work_dir))

            env = ServerConfig.prepare_env(self._env_paths)

            startupinfo = None
            if IS_WIN:
                # Prevent cmd.exe window from popping up
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = subprocess.SW_HIDE

            try:
                self.process = subprocess.Popen(
                    args=self._server_cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=self._work_dir,
                    env=env,
                    startupinfo=startupinfo,
                )
            except Exception as ex:
                print(f'NOTE: {LOG_NAME}: {self.lang_str} - Failed to create process, command:'
                        +f' {self._server_cmd}; Error: {ex}')
                return

            self._reader = self.process.stdout
            self._writer = self.process.stdin
            self._err = self.process.stderr

        # if config has tcp port - connect to it
        if self._tcp_port and type(self._tcp_port) == int:
            if self._server_cmd:
                connect_via_stdin()
            connect_via_tcp()
        # not port - create stdio-process
        else:
            connect_via_stdin()

        self.reader_thread = Thread(target=self._read_loop, name=self.name+'-reader', daemon=True)
        self.writer_thread = Thread(target=self._send_loop, name=self.name+'-writer', daemon=True)

        self.reader_thread.start()
        self.writer_thread.start()

        self.err_thread = Thread(target=self._err_read_loop, name=self.name+'-err', daemon=True)
        self.err_thread.start()

        #timer_proc(TIMER_START, self.process_queues, 100, tag='')
        self._timer.restart()

    def _err_read_loop(self):
        try:
            while self._err:
                line = self._err.readline()
                if line == b'':
                    break
                if self._log_stderr:
                    #print(f'ServerError: {LOG_NAME}: {self.lang_str} - {line}') # bytes
                    try:
                        s = line.decode('utf-8')
                    except:
                        s = str(line)
                    self._err_q.put(s)
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

                pass;       LOG and print(f'{LOG_NAME}: receive time: {time.time():.3f}')

                if header_bytes == b'':
                    pass;       LOG and print('NOTE: reader stopping')
                    res = os.waitpid(-1, os.WNOHANG if not IS_WIN else 0) # Alexey's fix
                    pass;       LOG and print(f'+ wait result: {res}')
                    break

                try:
                    body = self._reader.read(int(headers.get("Content-Length", 10*1000))) # Alexey's fix
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


    #NOTE call immediately after adding send events, to send faster
    def process_queues(self, tag='', info=''):
        try:
            if self._shutting_down:
                self.shutdown()
                self._shutting_down = False

            # read Queue
            errors = []
            while not self._read_q.empty():
                data = self._read_q.get()
                self._dbg_bmsgs = (self._dbg_bmsgs + [data])[-128:] # dbg

                events = self.client.recv(data, errors=errors)

                for err in errors:
                    msg_status(f'{LOG_NAME}: {self.lang_str}: unsupported msg: {str(err)[:60]}')
                    pass;       LOG and self.plog.log_str(f'{err}', type_='dbg', severity=SEVERITY_ERR)

                errors.clear()

                for msg in events:
                    self._on_lsp_msg(msg)

            # send Quue
            send_buf = self.client.send()
            if send_buf:
                self._send_q.put(send_buf)
                self._timer.restart()

            # stderr Queue
            while not self._err_q.empty():
                s = self._err_q.get()
                self.plog.log_str(s, type_='stderr')

        except Exception as ex:
            print(f'ERROR: QueuesProcessingError: {LOG_NAME}: {self.lang_str} - {ex}')
            #pass;
            #LOG and
            traceback.print_exc()


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

        elif msgtype == events.WorkspaceFolders:
            msg.reply(folders=self.workspace_folders)

        elif msgtype == events.Completion:
            if msg.completion_list:
                items = msg.completion_list.items
                pass;       LOG and print(f'got completion({len(items)}): {time.time():.3f} {msg.message_id} in {list(self.request_positions)}')
                reqpos = self.request_positions.pop(msg.message_id, None)
                #print('msg.completion_list.isIncomplete:',msg.completion_list.isIncomplete)
            else:
                items = None
            if items:
                if reqpos:
                    try:
                        compl = CompletionMan(carets=reqpos.carets, h_ed=reqpos.h_ed)
                        #print("using fresh results.","items:",len(items)," incomplete:",msg.completion_list.isIncomplete)
                        self._last_complete = compl.show_complete(msg.message_id, items, msg.completion_list.isIncomplete)
                    except AssertionError as e:
                        print("NOTE:",e)
            else:
                msg_status(f'{LOG_NAME}: {self.lang_str}: Completion - no info')

        elif msgtype == events.Hover:
            if msg.message_id in self.request_positions:
                _reqpos = self.request_positions.pop(msg.message_id)
                if ed.get_prop(PROP_HANDLE_SELF) == _reqpos.h_ed:
                    first_item = msg.contents[0] if isinstance(msg.contents, list) and len(msg.contents) > 0 else msg.contents
                    if first_item: # if received anything
                        if isinstance(first_item, (MarkedString, str)):
                            # for deprecated 'MarkedString' or 'str' default to 'markdown'
                            markupkind = MarkupKind.MARKDOWN
                        else:
                            # can be a list (supposedly)
                            markupkind = getattr(first_item, 'kind', None)

                        filtered_cmds = self.scfg.filter_commands(self._caret_cmds)
                        Hint.show(msg.m_str(),
                                caret=_reqpos.target_pos_caret,   cursor_loc_start=_reqpos.cursor_ed,
                                markupkind=markupkind,
                                language=getattr(first_item, 'language', None),
                                caret_cmds=filtered_cmds,
                        )
                    else:
                        msg_status(f'{LOG_NAME}: {self.lang_str}: Hover - no info')

        elif msgtype == events.SignatureHelp:
            if msg.message_id in self.request_positions:
                _reqpos = self.request_positions.pop(msg.message_id)
                if ed.get_prop(PROP_HANDLE_SELF) == _reqpos.h_ed:
                    hint = msg.get_hint_str()
                    if hint:
                        #hint = replace_unbracketed(hint, ',', ',\n\t', brackets={'{':'}', '[':']'})
                        #caret_x, caret_y = _reqpos.carets[0][:2]
                        # 8 - default duration
                        #msg_status_alt(hint, 8, pos=HINTPOS_TEXT_BRACKET, x=caret_x, y=caret_y)
                        
                        SignaturesDialog.set_text(msg.get_signatures())
                        SignaturesDialog.show()
                        
                    else:
                        SignaturesDialog.hide()
                        msg_status(f'{LOG_NAME}: {self.lang_str}: Signature help - no info')

        #GOTOs
        elif msgtype in GOTO_EVENT_TYPES:
            skip_dlg = msgtype == events.Definition
            dlg_caption = GOTO_TITLES.get(msgtype, f'Go to {msgtype.__name__}')
            reqpos = self.request_positions.pop(msg.message_id)
            self.do_goto(items=msg.result, dlg_caption=dlg_caption, skip_dlg=skip_dlg, reqpos=reqpos)

        elif msgtype == events.MDocumentSymbols:
            _reqpos = self.request_positions.pop(msg.message_id)
            if ed.get_prop(PROP_HANDLE_SELF) == _reqpos.h_ed  and  self.treeman:
                self.treeman.fill_tree(msg.result)

        elif msgtype == events.DocumentFormatting:
            if msg.message_id in self.request_positions:
                _reqpos = self.request_positions.pop(msg.message_id)
                if ed.get_prop(PROP_HANDLE_SELF) == _reqpos.h_ed:
                    ed.set_prop(PROP_RO, False)     # doc is set 'RO' during format-on-save

                    if msg.result:
                        # need reverse order for applying
                        # usually result are sorted by position (asc or desc) => reverse if [0] < [-1]
                        if msg.result[0].range.start.line < msg.result[-1].range.start.line:
                            msg.result.reverse()
                        # sort in descending order
                        msg.result.sort(reverse=True, key=lambda x:x.range.start)
                        for edit in msg.result:
                            EditorDoc.apply_edit(ed, edit)
                    else:
                        msg_status(f'{LOG_NAME}: {self.lang_str}: Document formatting - no info')

        elif msgtype == events.PublishDiagnostics:
            if IS_WIN:
                msg.uri = normalize_drive_letter(msg.uri)
            self.diagnostics_man.set_diagnostics(uri=msg.uri, diag_list=msg.diagnostics)

        elif msgtype == events.ConfigurationRequest:
            cfgs = [ServerConfig.get_configuration(self._cfg, cfgitem) for cfgitem in msg.items]
            msg.reply(cfgs)

        elif msgtype == events.LogMessage:
            # abandoning server - ignore logs
            if self._shutting_down is not None:
                return
            self.plog.log(msg)

        elif msgtype == events.ShowMessage:
            self.plog.log(msg)

        elif msgtype == events.ResponseError:
            _reqpos = self.request_positions.pop(msg.message_id, None)    # discard
            errstr = f'ResponseError[{msg.code}]: {msg.message}'
            self.plog.log_str(errstr, type_=_('Response Error'), severity=SEVERITY_ERR)

        elif isinstance(msg, events.WorkDoneProgressCreate)  or  issubclass(msgtype, events.Progress):
            self._on_progress(msg)

        elif msgtype == events.Shutdown:
            print(f'{LOG_NAME}: {self.lang_str}[{self.client_state_str}] - got shutdown response, exiting')
            self.client.exit()
            self.process_queues()
            self.exit()

        else:
            print(f'{LOG_NAME}: {self.lang_str} - unknown Message type: {msgtype}')


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

        _is_whole_doc = docsynckind == TextDocumentSyncKind.FULL
        _changes = eddoc.get_changes(whole_doc=_is_whole_doc)
        if not _changes:
            pass;       LOG and print('send_changes return: no changes')
            return

        _verdoc = eddoc.get_verdoc()
        self.client.did_change(text_document=_verdoc, content_changes=_changes)
        self._timer.restart()


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
        # clean up diagnostics img dictionary
        h_ed = eddoc.ed.get_prop(PROP_HANDLE_SELF)
        self.diagnostics_man._decor_serverity_ims.pop(h_ed, None)

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

    def on_save_pre(self, eddoc):
        if not self._format_on_save:
            return

        req_id = self.request_format_doc(eddoc)
        if req_id is not None:
            end_time = time.time() + MAX_FORMAT_ON_SAVE_WAIT
            eddoc.ed.set_prop(PROP_RO, True)    # prevent document editing between request and formattng
            try:
                while time.time() < end_time:
                    if req_id in self.request_positions:
                        app_idle(wait=True)
                    else:
                        break
                else:
                    msg_status(_('{}: {} - No format-on-save response came').format(
                                                                        LOG_NAME, self.lang_str))
            finally:
                # check if editor closed before resetting 'RO'
                if eddoc.ed.get_prop(PROP_TAB_TITLE) is not None:
                    eddoc.ed.set_prop(PROP_RO, False)

    def on_rootdir_change(self, newroot):
        if self._client is not None  and  self.client.is_initialized:
            opts = self.scfg.method_opts(METHOD_WS_FOLDERS)
            if opts  and  opts.get('supported')  and  opts.get('changeNotifications'):
                removed = [] # emptys if no folder
                added = []
                if self._work_dir:
                    old_root_uri = path_to_uri(self._work_dir)
                    removed.append(WorkspaceFolder(uri=old_root_uri, name='Root'))
                if newroot:
                    new_root_uri = path_to_uri(newroot) if newroot else None
                    added.append(WorkspaceFolder(uri=new_root_uri, name='Root'))
                self._work_dir = newroot

                self.client.did_change_workspace_folders(removed=removed, added=added)
                return True

    def _action_by_name(self, method_name, eddoc, caret=None):
        if self.client.is_initialized:
            opts = self.scfg.method_opts(method_name, eddoc)
            if opts is None:
                msg_status(f'{LOG_NAME}: Method is not supported by server: {method_name}')
                return None,None

            docpos = eddoc.get_docpos(caret)
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
        on_complete_kind = app_proc(PROC_GET_AUTOCOMPLETION_INVOKE, 0)
        
        # try to complete from cached data (_last_complete)
        if on_complete_kind != 'c' and self._last_complete and not self._last_complete.is_incomplete:
            _, message_id, items, filtered_items, carets, _, line_prev, is_incomplete = self._last_complete
            x1,y1, _,_ = ed.get_carets()[0]
            x2,y2, _,_ = carets[0]
            
            # check if left side of line was not changed
            line_current = ed.get_text_line(y2, max_len=1000)[:x2]
            
            #print(line_prev.strip())
            if line_prev.strip() == line_current.strip():
                word = get_word(x1, y1)
                if word:
                    word1part, word2part = word
                else:
                    word1part = word2part = ''
                word_len = len(word1part) + len(word2part)
                
                filtered_items = list(filter(lambda i: word1part in i.label, filtered_items))
                
                if filtered_items: # if cache has word then continue, else - send request
                    text_between_last_pos = ed.get_text_substr(x1,y1,x2,y2).strip()
                    if text_between_last_pos == '':
                        text_between_last_pos = ed.get_text_substr(x2,y2,x1,y1).strip()
                        
                    whitespace_walk = text_between_last_pos == ''
                    if (whitespace_walk and word_len == 0):
                        #print("using cache! (whitespace_walk)")
                        compl = CompletionMan(carets)
                        self._last_complete = compl.show_complete(message_id, items, self._last_complete.is_incomplete)
                        return True
                    
                    crossed_word_boundary = any(char in text_between_last_pos for char in get_nonwords_chars())
                    #print("x1/x2",x1,x2)
                    if not crossed_word_boundary and (y1 == y2) and (x1 >= x2) and (x1 <= x2 + word_len):
                        #print("using cache!")
                        compl = CompletionMan(carets)
                        self._last_complete = compl.show_complete(message_id, items, self._last_complete.is_incomplete)
                        return True
    
        # cache can't be used -> request data from server
        id, pos = self._action_by_name(METHOD_COMPLETION, eddoc)
        #print('pos',pos)
        if id is not None:
            self._save_req_pos(id=id)
            return True

    def on_snippet(self, ed_self, snippet_id, snippet_text): # completion callback
        if snippet_id == SNIP_ID and self._last_complete:
            compl, message_id, items, filtered_items, _, h_ed, _, is_incomplete = self._last_complete
            if h_ed == ed.get_prop(PROP_HANDLE_SELF):
                return compl.do_complete(message_id, snippet_text, filtered_items)
        return False


    def on_hover(self, eddoc, caret):
        """ just sends request to server, dsiplaying stuff in 'dlg.py/Hint'
        """
        id, pos = self._action_by_name(METHOD_HOVER, eddoc, caret)
        if id is not None:
            self._save_req_pos(id=id, target_pos_caret=pos)

    def do_goto(self, items, dlg_caption, skip_dlg=False, reqpos=None):
        """ items: Location or t.List[t.Union[Location, LocationLink]], None
        """
        def link_to_target(link): #SKIP
            """ returns: (uri, goto-range)
            """
            if isinstance(link, Location):
                return (link.uri, link.range)
            elif isinstance(link, LocationLink):
                return (link.targetUri, link.targetSelectionRange)
            else:
                raise Exception('Invalid goto-link type: '+str(type(link)))

        if not items:
            msg_status(f'{LOG_NAME}: {self.lang_str}: {dlg_caption} - no info')
            return

        if isinstance(items, list):
            targets = (link_to_target(item) for item in items)
            targets = ((uri_to_path(uri),range_) for uri,range_ in targets) # uri to path

            if skip_dlg:
                item = items[0] # first
            else:
                targets = list(targets)
                # ((dir,filename), line)
                names = ((os.path.split(path), range_.start.line+1)  for path,range_ in targets)
                names = [f'{fn}, line {nline}\t{collapse_path(folder)}' for (folder,fn),nline in names]
                ind = dlg_menu(DMENU_LIST_ALT, names, caption=dlg_caption)
                if ind is None:
                    return
                item = items[ind]

            uri,targetrange = link_to_target(item)

        else: # items is single item
            uri,targetrange = link_to_target(items)

        targetpath = uri_to_path(uri)
        target_line = max(0, targetrange.start.line-3)
        target_caret = (targetrange.start.character, targetrange.start.line)

        if not os.path.isfile(targetpath):
            # check whether we are in unsaved tab
            fn = os.path.split(targetpath)[1]
            tab_title = ed.get_prop(PROP_TAB_TITLE).lstrip('*')
            if ed.get_filename() == '' and fn == tab_title:
                ed.set_caret(*target_caret)
                ed.set_prop(PROP_LINE_TOP, target_line)
                return
            else:
                print('NOTE: ' + _('{}: {} - file does not exist: {!r}, uri:{!r}').format(
                        LOG_NAME, self.lang_str, targetpath, uri))

        # open file:  in embedded first
        try:
            if reqpos:
                nline = None
                if reqpos.target_pos_caret:
                    nline = reqpos.target_pos_caret[1]
                elif reqpos.carets:
                    nline = reqpos.carets[0][1]

                scroll_to = (0, target_line)
                caption = os.path.basename(targetpath)

                if nline is not None:
                    from cuda_embed_ed import open_file_embedded
                    open_file_embedded(targetpath, nline,  caption=caption,  scroll_to=scroll_to,
                                                                                carets=[target_caret])
        except ImportError:
            file_open(targetpath)
            app_idle(True) # fixes editor not scrolled to caret
            ed.set_caret(*target_caret) # goto specified position start
            ed.set_prop(PROP_LINE_TOP, target_line)

    def request_sighelp(self, eddoc):
        id, pos = self._action_by_name(METHOD_SIG_HELP, eddoc)
        if id is not None:
            self._save_req_pos(id=id, target_pos_caret=pos)

    # GOTOs
    def request_definition_loc(self, eddoc, caret=None):
        id, pos = self._action_by_name(METHOD_DEFINITION, eddoc, caret=caret)
        if id is not None:
            self._save_req_pos(id=id, target_pos_caret=pos)

    def request_references_loc(self, eddoc, caret=None):
        id, pos = self._action_by_name(METHOD_REFERENCES, eddoc, caret=caret)
        if id is not None:
            self._save_req_pos(id=id, target_pos_caret=pos)

    def request_implementation_loc(self, eddoc, caret=None):
        id, pos = self._action_by_name(METHOD_IMPLEMENTATION, eddoc, caret=caret)
        if id is not None:
            self._save_req_pos(id=id, target_pos_caret=pos)

    def request_declaration_loc(self, eddoc, caret=None):
        id, pos = self._action_by_name(METHOD_DECLARATION, eddoc, caret=caret)
        if id is not None:
            self._save_req_pos(id=id, target_pos_caret=pos)

    def request_typedef_loc(self, eddoc, caret=None):
        id, pos = self._action_by_name(METHOD_TYPEDEF, eddoc, caret=caret)
        if id is not None:
            self._save_req_pos(id=id, target_pos_caret=pos)


    def request_format_doc(self, eddoc):
        if self.client.is_initialized:
            opts = self.scfg.method_opts(METHOD_FORMAT_DOC, eddoc)
            if opts is not None:
                self.send_changes(eddoc)

                docid = eddoc.get_docid()
                options = eddoc.get_ed_format_opts()
                id = self.client.formatting(text_document=docid, options=options)
                self._save_req_pos(id=id, target_pos_caret=None) # save current editor handle
                return id

    def request_format_sel(self, eddoc):
        if self.client.is_initialized:
            opts = self.scfg.method_opts(METHOD_FORMAT_DOC, eddoc)
            if opts is not None:
                self.send_changes(eddoc)

                range_ = eddoc.get_selection_range()
                if range_:
                    docid = eddoc.get_docid()
                    options = eddoc.get_ed_format_opts()
                    id = self.client.range_formatting(text_document=docid, range=range_, options=options)
                    self._save_req_pos(id=id, target_pos_caret=None) # save current editor handle


    def update_tree(self, eddoc):
        """ returns True if feature supported
        """
        if self.client.is_initialized:
            opts = self.scfg.method_opts(METHOD_DOC_SYMBOLS, eddoc)
            if opts is not None  and  eddoc.lang is not None: # lang check -- is opened
                self.send_changes(eddoc) # for later: server can give edits on save

                docid = eddoc.get_docid()
                id = self.client.doc_symbol(docid)

                self._save_req_pos(id=id, target_pos_caret=None) # save current editor handle
                self.process_queues()
                return True

    def call_hierarchy_in(self, eddoc):
        self.send_changes(eddoc)

        docpos = eddoc.get_docpos()
        id = self.client.call_hierarchy_in(docpos)


    def workspace_symbol(self, eddoc):
        self.client.workspace_symbol(query='')


    def get_state_pair(self):
        key = self.name
        state = self.plog.get_state()

        return key,state

    def shutdown(self, *args, **vargs):
        pass;       LOG and print('-- lang - shutting down')
        if self.client.is_initialized:
            self.client.shutdown()
        else:
            self._shutting_down = True

    def exit(self):
        if not self._closed:
            self._send_q.put_nowait(None) # stop send_loop()
            self.process_queues()

            if self.sock:
                self.sock.close()

            self._closed = True
            self._timer.stop()


    def _on_progress(self, msg):
        if isinstance(msg, events.WorkDoneProgressCreate):
            self.progresses[msg.token] = None
            msg.reply()

        elif isinstance(msg, events.WorkDoneProgress):
            val = msg.value
            title = None
            if isinstance(msg, events.WorkDoneProgressBegin):
                self.progresses[msg.token] = msg
                title = val.title
                msg_str = f': {val.message}'  if val.message else ''

            elif isinstance(msg, events.WorkDoneProgressReport):
                title = self.progresses[msg.token].value.title
                msg_str = ''
                if val.message:                     msg_str = f': {val.message}'
                elif val.percentage is not None:    msg_str = f' [{val.percentage}%]'

            elif isinstance(msg, events.WorkDoneProgressEnd):
                title = self.progresses.pop(msg.token).value.title # deletes start-message
                msg_str = f': {val.message}'  if val.message else  ' [Done]'

            if title:
                msg_status(f'{LOG_NAME}: {self.lang_str} - {title + msg_str}')

    def _save_req_pos(self, id, target_pos_caret=None):
        """ save request's caret position, and active editor -- to check if proper editor
        """
        h = ed.get_prop(PROP_HANDLE_SELF)
        
        # save word's start position
        x1, y1, _x2, _y2 = ed.get_carets()[0]
        
        ## change x to the beginning of the word
        #word = get_word(x1, y1)
        #if word and len(word[0]) != 0:
            #x1 = x1 - len(word[0])
        
        carets = [(x1,y1,_x2,_y2)]
        
        _cursor = app_proc(PROC_GET_MOUSE_POS, '') # screen coords
        cursor_ed = ed.convert(CONVERT_SCREEN_TO_LOCAL, *_cursor)
        _req = RequestPos(h,  carets=carets,  target_pos_caret=target_pos_caret,  cursor_ed=cursor_ed)
        self.request_positions[id] = _req


    def _validate_config(self):
        """ aborts server start if invalid config
        """
        if not self._server_cmd and not self._tcp_port:
            msg = f'no server-start-command for current OS ({CMD_OS_KEY}) or tcp_port specified'
            raise ValidationError(f'NOTE: {LOG_NAME}: server config error: "{self.name}" - {msg}')

        # check that 'env_paths' is dict of lists
        if self._env_paths:
            if not isinstance(self._env_paths, dict):
                msg = '`env_paths` should be a `dictionary`'
                raise ValidationError(f'NOTE: {LOG_NAME}: server config error: "{self.name}" - {msg}')
            for paths in self._env_paths.values():
                if not isinstance(paths, list):
                    msg = '`env_paths` values should be `lists`'
                    raise ValidationError(f'NOTE: {LOG_NAME}: server config error: "{self.name}" - {msg}')


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

    LINT_NONE = 100
    LINT_BOOKMARK = 101
    LINT_DECOR = 102

    def __init__(self, lintstr=None, underline_style=2, logger=None):
        self.logger=logger
        self.uri_diags = {} # uri -> diag?
        self.dirtys = set() # uri

        self._linttype = None  # gutter icons
        self._highlight_bg = False
        self._highlight_text = False
        self._underline_style = underline_style

        self._load_lint_type(lintstr)

        # icons and bg col
        self._decor_serverity_ims = {} # ed handle -> {severity : im ind}

        self._setup_bookmark_gutter()

    def on_doc_shown(self, eddoc):
        if not self._linttype:
            return

        # if dirty - update
        if eddoc.uri in self.dirtys:
            self.dirtys.remove(eddoc.uri)

            self._apply_diagnostics(eddoc.ed, self.uri_diags[eddoc.uri])

    def set_diagnostics(self, uri, diag_list):
        if not self._linttype:
            return
        if len(diag_list) > 0  or  self.uri_diags.get(uri):
            self.uri_diags[uri] = diag_list
            for ed in get_visible_eds():
                if uri == ed_uri(ed):
                    self._apply_diagnostics(ed, diag_list)
            else: # not visible, update when visible
                self.dirtys.add(uri)

    def _apply_diagnostics(self, ed, diag_list):
        self.logger.clear()
        if self._linttype  or  self._highlight_bg:
            self._clear_old(ed)

            if self._linttype == DiagnosticsMan.LINT_DECOR:
                h_ed = ed.get_prop(PROP_HANDLE_SELF)
                if h_ed not in self._decor_serverity_ims:
                    self._setup_decor_gutter(ed)
                decor_im_map = self._decor_serverity_ims[h_ed]

            ### set new
            # get dict of lines for gutter
            line_diags = self._get_gutter_data(diag_list)

            filename_added = False

            err_ranges = []  # tuple(x,y,len)
            # apply gutter to editor
            for nline,diags in line_diags.items():
                severity_la = lambda d: d.severity or 9
                if self._linttype == DiagnosticsMan.LINT_DECOR:
                    decor_severity = min(severity_la(d) for d in diags) # most severe severity  for decor
                else:
                    diags.sort(key=severity_la) # important first, None - last

                # get msg-lines for bookmark hover
                msg_lines = []
                for d in diags:
                    kind = DIAG_BM_KINDS.get(d.severity, DIAG_DEFAULT_SEVERITY)
                    #TODO fix ugly... (.severity and .code -- can be None)
                    pre,post = ('[',']: ') if (d.severity is not None  or  d.code) else  ('','')
                    mid = ':' if (d.severity is not None  and  d.code) else ''

                    severity_short = d.severity.short_name() if d.severity else ''
                    # "[severity:code] message"
                    code = str(d.code)  if d.code is not None else  ''
                    source = str(d.source)  if d.source is not None else  ''
                    text = ''.join([pre, source, ',',severity_short, mid, code, post, d.message])
                    msg_lines.append(text)

                    if not filename_added:
                        filename_added = True
                        fn = ed.get_filename()
                        self.logger.log_str(f"File: {fn}", type_="Errors", severity=SEVERITY_MAP[d.severity])
                    self.logger.log_str(f"Line {d.range.start.line+1}: {text}", type_="Errors", severity=SEVERITY_MAP[d.severity])

                # gather err ranges
                for d in diags:
                    x0,y0 = d.range.start.character, d.range.start.line
                    x1,y1 = d.range.end.character, d.range.end.line
                    if y0 == y1:   # single line (shortcut for common case)
                        err_ranges.append((x0, y0, x1-x0))
                    else: # multiline
                        for linen in range(y0, y1):
                            linelen = len(ed.get_text_line(linen))
                            mx0 = x0  if linen == y0 else  0
                            mx1 = linelen
                            err_ranges.append((mx0, y0, mx1-mx0))

                        err_ranges.append((0, y1, x1)) # last line


                # set bookmark or decor
                if self._linttype == DiagnosticsMan.LINT_DECOR:
                    if decor_severity == 9:
                        decor_severity = DIAG_DEFAULT_SEVERITY
                    tooltip = chr(1)+text if api_ver >= '1.0.427' else ''
                    ed.decor(DECOR_SET, line=nline, image=decor_im_map[decor_severity], text=tooltip, tag=DIAG_BM_TAG)
                else:
                    text = '\n'.join(msg_lines)
                    ed.bookmark(BOOKMARK_SET, nline=nline, nkind=kind, text=text, tag=DIAG_BM_TAG)
            #end for line_diags

            # underline error text ranges
            if self._highlight_text  and  err_ranges:
                _colors = app_proc(PROC_THEME_UI_DICT_GET, '')
                err_col = _colors['EdMicromapSpell']['color']
                xs,ys,lens = list(zip(*err_ranges))
                self.last_err_ranges = err_ranges

                ed.attr(MARKERS_ADD_MANY,  tag=DIAG_BM_TAG,  x=xs,  y=ys,  len=lens,
                            color_border=err_col,  border_down=self._underline_style)


    def _get_gutter_data(self, diag_list):
        line_diags = defaultdict(list) # line -> list of diagnostics
        for d in diag_list:
            line_diags[d.range.start.line].append(d)
        return line_diags

    def _clear_old(self, ed):
        # gutter
        if self._linttype == DiagnosticsMan.LINT_DECOR:
            ed.decor(DECOR_DELETE_BY_TAG, tag=DIAG_BM_TAG)
        else:
            ed.bookmark(BOOKMARK_DELETE_BY_TAG, 0, tag=DIAG_BM_TAG)

        # text err underline
        if self._highlight_text:
            ed.attr(MARKERS_DELETE_BY_TAG, tag=DIAG_BM_TAG)

    def _load_lint_type(self, lintstr):
        if lintstr:
            self._highlight_text = lintstr and 'c' in lintstr
            if 'B' in lintstr  or  'b' in lintstr:
                self._linttype = DiagnosticsMan.LINT_BOOKMARK
                self._highlight_bg = 'B' in lintstr
            elif 'd' in lintstr:
                self._linttype = DiagnosticsMan.LINT_DECOR
            else:
                self._linttype = DiagnosticsMan.LINT_NONE

    def _setup_bookmark_gutter(self):
        if self._linttype or self._highlight_bg:
            icon_paths = DIAG_BM_IC_PATHS  if self._linttype != DiagnosticsMan.LINT_NONE else  {}
            ncolor = COLOR_DEFAULT  if self._highlight_bg else  COLOR_NONE
            for severity,kind in DIAG_BM_KINDS.items():
                icon_path = icon_paths.get(severity, '')
                ed.bookmark(BOOKMARK_SETUP, 0, nkind=kind, ncolor=ncolor, text=icon_path)

    def _setup_decor_gutter(self, ed):
        icon_paths = DIAG_BM_IC_PATHS
        h_ed = ed.get_prop(PROP_HANDLE_SELF)
        for severity,kind in DIAG_BM_KINDS.items():
            icon_path = icon_paths.get(severity, '')
            _h_im = ed.decor(DECOR_GET_IMAGELIST)
            _ind = imagelist_proc(_h_im, IMAGELIST_ADD, value=icon_path)
            self._decor_serverity_ims.setdefault(h_ed, {})[severity] = _ind



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
METHOD_FORMAT_DOC       = 'textDocument/formatting'
METHOD_FORMAT_SEL       = 'textDocument/rangeFormatting'

# client method(s)
METHOD_WS_FOLDERS = 'workspace/workspaceFolders'


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
CAPABILITY_FORMAT_DOC       = 'textDocument.formatting'
CAPABILITY_FORMAT_SEL       = 'textDocument.rangeFormatting'
CAPABILITY_WORKSPACE_FOLDERS = 'workspace.workspaceFolders'

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
    METHOD_FORMAT_DOC       : 'documentFormattingProvider',
    METHOD_FORMAT_SEL       : 'documentRangeFormattingProvider',

    #METHOD_WS_SYMBOLS       : '',
}

# not started by user - dont print "unsupported"
AUTO_METHODS = {
    METHOD_DID_OPEN,
    METHOD_DID_CLOSE,
    METHOD_DID_SAVE,
    METHOD_DID_CHANGE,

    METHOD_COMPLETION,
    METHOD_SIG_HELP,
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
        is_openclose = True
        if isinstance(docsync, dict):
            is_openclose = docsync.get('openClose', False) is not False

            _save = docsync.get('save', False) # save?: boolean | SaveOptions;

            # SAVE
            if _save is not False:
                _opts = {**_default_opts}
                if isinstance(_save, dict):
                    _opts.update(_save)
                self.capabs.append(Registration(id='0', method=METHOD_DID_SAVE, registerOptions=_opts))

        #  OPEN, CLOSE
        if is_openclose:
            open = Registration(id='0', method=METHOD_DID_OPEN, registerOptions=_default_opts)
            close = Registration(id='0', method=METHOD_DID_CLOSE, registerOptions=_default_opts)
            self.capabs += [open, close]

        # CHANGE
        if isinstance(docsync, dict):
            _default_sync = int(TextDocumentSyncKind.NONE)
            docsynckind = TextDocumentSyncKind(docsync.get('change', _default_sync))
        else:
            docsynckind = TextDocumentSyncKind(docsync)

        _opts = {**_default_opts, 'syncKind': docsynckind}
        self.capabs.append(Registration(id='0', method=METHOD_DID_CHANGE, registerOptions=_opts))


        ### WORKSPACE
        workspace = capabilities.get('workspace')
        if workspace:
            # workspaceFolders
            wsfolders = workspace.get('workspaceFolders', {})
            _opts = {
                #**_default_opts, # -- no need for workspace methods
                'supported': wsfolders.get('supported', False),
                'changeNotifications': wsfolders.get('changeNotifications', False),
            }
            _reg = Registration(id='0', method=METHOD_WS_FOLDERS, registerOptions=_opts)
            self.capabs.append(_reg)


        ### ~other static capabilites
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
        if method_name.startswith('textDocument/'):
            if ed_self is None:
                ed_self = doc.ed
            if langid is None:
                langid = doc.langid

            for registration in self.capabs:
                if registration.method == method_name:
                    if ServerConfig.match_capability(registration, ed_self, langid):
                        return registration.registerOptions

            if method_name not in AUTO_METHODS:
                print(f'NOTE: {LOG_NAME}: {self.lang_str} - unsupported method: {method_name}')

        elif method_name.startswith('workspace/'):
            for registration in self.capabs:
                if registration.method == method_name:
                    return registration.registerOptions

        elif LOG:
            print(f'NOTE: {LOG_NAME}: odd method: {method_name}')


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

    def filter_commands(self, cmds):
        # 'textDocument/didOpen' => 'didopen'
        supported_names = {reg.method.split('/')[-1].lower() for reg in self.capabs}
        res = {**cmds}
        for name in cmds:
            # 'Type definition' => 'typedefinition'
            name_tmp = name.lower().replace(' ', '')
            if name_tmp not in supported_names:
                #print(f'* Unsupported function by server: {name}')
                res[name] = None  # None denotes unsupported command - dimmed in hover dlg
        return res

    def get_configuration(cfg, req):
        """ cfg - user server config
            req - server's request -- ConfigurationItem
        """
        settings = cfg.get('settings', {})
        if req.section:
            return settings.get(req.section, {})
        else:
            return settings


    def prepare_env(env_paths):
        if not env_paths:       return

        env = {**os.environ}
        for name,paths in env_paths.items():
            if paths:
                if env.get(name):
                    env[name] += os.pathsep + os.pathsep.join(paths)
                else:
                    env[name] = os.pathsep.join(paths)
        return env


class CompletionMan:
    auto_append_bracket = True
    
    def __init__(self, carets=None, h_ed=None):
        assert len(carets) == 1, 'no autocomplete for multi-carets'
        assert carets[0][3] == -1, 'no autocomplete for selection'

        self.carets = carets
        self.h_ed = h_ed or ed.get_prop(PROP_HANDLE_SELF)
        
        x,y, _,_ = carets[0]
        self.line_str = ed.get_text_line(y,max_len=1000)[:x]
        #print('self.line_str=',self.line_str)

    def filter_startswith(self, item, word):
        if item.filterText:
            return item.filterText.startswith(word)
        else:
            return item.label.startswith(word)
    
    def show_complete(self, message_id, items, is_incomplete):
        
        if self.h_ed != ed.get_prop(PROP_HANDLE_SELF):       return # wrong editor

        lex = ed.get_prop(PROP_LEXER_FILE, '')    #NOTE probably no need to check for lexer

        if lex is None: return
        #if not is_lexer_allowed(lex): return

        _carets = ed.get_carets()
        x0,y0, _x1,_y1 = _carets[0]
        
        #line_current = ed.get_text_line(y0, max_len=1000).strip()
        word = get_word(x0, y0)
        word1 = word2 = ''
        if word:
            word1, word2 = word
            
            #if self.carets != [(x0-len(word1), y0, _x1, _y1)] and line_current!='':      return # caret moved
            
            #filtered_items = items
            filtered_items = list(filter(lambda i: word1.lower() in i.label.lower(), items))
            
            #filtered_items = list(filter(lambda i: self.filter_startswith(i, word1), items))
            
            filtered_items = sorted(filtered_items,
                                    key=lambda i:
                                        (
                                        i.label == word1,
                                        word1 in i.label,
                                        i.label.lower() == word1.lower(),
                                        i.label.startswith(word1),
                                        i.label.lower().startswith(word1.lower())
                                        ),
                                    reverse=True,
                                    )
            if len(word1) == 0 and len(word2) > 0: # we are at the start of the word
                # update cached caret (so it points to the start of the word)
                self.carets = [(x0,y0,_x1,_y1)]
                
            #filtered_items = list(filter(lambda i: self.filter_startswith(i, word1), items))
        else:
            #if self.carets != _carets and line_current!='':      return # caret moved
            filtered_items = items

        #filtered_items = sorted(items, key=lambda i: i.sortText or i.label)
        #print(">>> items[0]:", items[0])
        
        def add_html_tags(text, item_kind, filter_text):
            if api_ver < '1.0.431':    return text
            #if item_kind in CALLABLE_COMPLETIONS:   text = '<u>'+text+'</u>'
            if filter_text:
                pos = text.find(filter_text) # case-sensitive
                if pos == -1: # if not found try case-insensitive
                    pos = text.lower().find(filter_text.lower())
                if pos >= 0:    text = text[:pos]+'<b>'+text[pos:pos+len(filter_text)]+'</b>'+text[pos+len(filter_text):]
            return '<html>'+text
        
        words = ['{}\t{}\t{}|{}'.format(
                    add_html_tags(item.label, item.kind, word1),
                    item.kind and item.kind.name.lower() or '', message_id, i
                    )
                    for i,item in enumerate(filtered_items)]

        # results are already seem to be sorted by .sortText

        sel = get_first(i for i,item in enumerate(filtered_items)  if item.preselect is True)
        sel = sel or 0

        ed.complete_alt('\n'.join(words), SNIP_ID, len_chars=0, selected=sel)
        
        #if True:
        #if is_incomplete:
            #print("isIncomplete: ", is_incomplete)
            #return None
        #else:
            #print("isIncomplete: ", is_incomplete, ", caching")
        return CachedCompletion(self, message_id, items, filtered_items, self.carets, self.h_ed, self.line_str, is_incomplete)

    #TODO add () and move caret if function?
    def do_complete(self, message_id, snippet_text, items):
        items_msg_id, item_ind = snippet_text.split('|')
        if int(items_msg_id) != message_id:
            return

        item_ind = int(item_ind)
        item = items[item_ind]
            
        _carets = ed.get_carets()
        x0,y0, _x1,_y1 = _carets[0]

        lex = ed.get_prop(PROP_LEXER_FILE, '')
        self._nonwords = appx.get_opt(
            'nonword_chars', '''-+*=/\()[]{}<>"'.,:;~?!@#$%^&|`…''',
            appx.CONFIG_LEV_ALL, ed, lex)
        
        x1 = x2 = x0
        y1 = y2 = y0
        word = self._get_word(x0, y0)
        if word:
            word1, word2 = word
            x1 = x0-len(word1)
            x2 = x0+len(word2)
        
        line_txt = ed.get_text_line(y0)
        is_callable = item.kind  and  item.kind in CALLABLE_COMPLETIONS
        is_bracket_follows = line_txt[x2:].strip()[:1] == '('

        if item.textEdit:
            # ignore `item.textEdit.range` for now because it may be cached (old and wrong)
            #x1,y1,x2,y2 = EditorDoc.range2carets(item.textEdit.range)
            text = item.textEdit.newText
        elif item.insertText:   text = item.insertText
        else:                   text = item.label
        
        has_brackets = all(b in text for b in '()')
        if is_bracket_follows and has_brackets and text[-2:] == '()':
            text = text[:-2]
        
        brackets_inserted = False
        if (
                CompletionMan.auto_append_bracket
                and not has_brackets and is_callable
                and not is_bracket_follows and ('Bash' not in lex)
           ):
            text += '()'
            brackets_inserted = True
        
        if item.insertTextFormat and item.insertTextFormat == InsertTextFormat.SNIPPET:
            snippet = Snippet(text=text.split('\n'))
            ed.delete(x1,y1,x2,y2) # delete range
            snippet.insert(ed)
            #print("NOTE: Cuda_LSP: snippet was inserted:",text)
        else: # not snippet (PLAINTEXT)
            padding = ' '*(x2-len(line_txt)) if len(line_txt) < x2 else ''
            if padding: # to support virtual caret
                ed.insert(x1,y1, padding)
                x2 += len(padding)
            new_caret = ed.replace(x1,y1,x2,y2, text)
            # move caret at ~end of inserted text
            if new_caret:
                if brackets_inserted:
                    ed.set_caret(new_caret[0] - 1,  new_caret[1])
                else:
                    ed.set_caret(*new_caret)

        # additinal edits
        if item.additionalTextEdits:
            for edit in item.additionalTextEdits:
                EditorDoc.apply_edit(ed, edit)
        return True


    def _get_word(self, x, y):
        if not 0<=y<ed.get_line_count():
            return
        s = ed.get_text_line(y)
        if not 0<=x<=len(s):
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

