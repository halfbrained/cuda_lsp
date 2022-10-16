import os
import pathlib

import cudatext as ct
import cudax_lib as appx

from .dlg import Hint

USER_DIR = os.path.expanduser('~')

def get_first(gen):
    try:
        #if notnone:
            #for val in gen:
                #if val is not None:
                    #return val
        #else:
        return next(gen)
    except StopIteration:
        pass
    return None

def lex2langid(lex):
    return lex_ids.get(lex)

def langid2lex(langid_):
    for name,langid in lex_ids.items():
        if langid == langid_:
            return name

def is_ed_visible(ed):
    gr_ed = ct.ed_group(ed.get_prop(ct.PROP_INDEX_GROUP))
    return ed == gr_ed

def get_visible_eds():
    for i in range(8):
        ed = ct.ed_group(i)
        if ed:
            yield ed

def get_word(x, y):
    ed = ct.ed
    if not 0<=y<ed.get_line_count():
        return
    s = ed.get_text_line(y)
    if not 0<=x<=len(s):
        return

    x0 = x
    while (x0>0) and _isword(s[x0-1]):
        x0-=1
    text1 = s[x0:x]

    x0 = x
    while (x0<len(s)) and _isword(s[x0]):
        x0+=1
    text2 = s[x:x0]

    return (text1, text2)

def get_nonwords_chars():
    ed = ct.ed
    lex = ed.get_prop(ct.PROP_LEXER_FILE, '')
    _nonwords = appx.get_opt(
        'nonword_chars',
        '''-+*=/\()[]{}<>"'.,:;~?!@#$%^&|`…''',
        appx.CONFIG_LEV_ALL,
        ed,
        lex)
    return _nonwords

def _isword(s):
    nonwords = get_nonwords_chars()
     
    return s not in ' \t'+nonwords


def path_to_uri(path):
    return pathlib.Path(path).as_uri()

def ed_uri(ed):
    fn = ed.get_filename()
    if fn:
        #return 'file://'+ed.get_filename()
        return path_to_uri(fn)
    else:
        return 'file:///'+ed.get_prop(ct.PROP_TAB_TITLE).lstrip('*')

def uri_to_path(uri):

    # https://stackoverflow.com/a/61922504
    # ~heavy import
    from urllib.parse import urlparse, unquote
    from urllib.request import url2pathname

    path = urlparse(uri).path
    return url2pathname(unquote(path))

def collapse_path(path):
    if path  and  (path + os.sep).startswith(USER_DIR + os.sep):
        path = path.replace(USER_DIR, '~', 1)
    return path
    
def normalize_drive_letter(path):    
    parts = path.split("file:///")
    if len(parts) == 2 and parts[0] == '' and len(parts[1]) >= 2 and parts[1][1] == ':':
        path = "file:///" + parts[1][:1].upper() + parts[1][1:]
    return path

def command(f):
    def d(*args, **vargs): #SKIP
        if Hint.is_visible():
            Hint.hide()
        return f(*args, **vargs)
    return d

class ValidationError(Exception):
    pass

def replace_unbracketed(s, target_char, repl, brackets):
    depth = 0
    start_char = None
    end_char = None
    result = ''
    for c in s:
        if c in brackets:
            if depth == 0:
                start_char = c
                end_char = brackets[start_char]
                depth += 1
            elif c == start_char:
                    depth += 1

        elif c == end_char:
            depth -= 1
            if depth == 0:
                start_char = None
                end_char = None

        elif depth == 0  and  c == target_char:
            c = repl

        result += c
    return result


class TimerScheduler:
    """ orchestrates growing timer telta
        `restart()` - restarts timer from `mintime`, and grows timer period by
            accumulating `delta` between calls
        NOTE: weakref because timer keeps references
    """

    def __init__(self, callback, mintime=10, maxtime=250, delta=10):
        """ default: len==24;  t:10, 30, 60, 100, 150, ... 2100, 2310, 2530, 2760
        """
        import weakref

        self._callback_wr   = weakref.ref(callback)

        self._mintime       = mintime
        self._maxtime       = maxtime
        self._delta         = delta

        self._last_period = 0

    def timer_callback(self, tag='', info=''):
        callback = self._callback_wr()

        if callback is not None:
            callback(tag, info)

            if self._last_period < self._maxtime:
                self._last_period = min(self._maxtime, self._last_period + self._delta)
                ct.timer_proc(ct.TIMER_START, self.timer_callback, self._last_period)

        else:
            ct.timer_proc(ct.TIMER_STOP, self.timer_callback, 0)

    def restart(self):
        self._last_period = self._mintime
        ct.timer_proc(ct.TIMER_START, self.timer_callback, self._last_period)

    def stop(self):
        ct.timer_proc(ct.TIMER_STOP, self.timer_callback, 0)


def update_lexmap(upd):
    lex_ids.update(upd)

# https://microsoft.github.io/language-server-protocol/specifications/specification-current/#-textdocumentitem-
lex_ids = {
    'ABAP': 'abap',
    'Batch files': 'bat', # spec: 'Windows Bat'
    'BibTeX': 'bibtex',
    'Clojure': 'clojure',
    'CoffeeScript': 'coffeescript', # spec: 'Coffeescript'
    'C': 'c',
    'C++': 'cpp',
    'C#': 'csharp',
    'CSS': 'css',
    'Diff': 'diff',
    'Dart': 'dart',
    'Dockerfile': 'dockerfile',
    'Elixir': 'elixir',
    'Erlang': 'erlang',
    'F#': 'fsharp',
    #'Git': 'git-commit and git-rebase', #TODO
    'Go': 'go',
    'Groovy': 'groovy',
    'HTML Handlebars': 'handlebars', # spec: 'Handlebars'
    'HTML': 'html',
    'Ini files': 'ini', # spec: 'Ini'
    'Java': 'java',
    'JavaScript': 'javascript',
    #'JavaScript React': 'javascriptreact', # Not in CudaText
    'JSON': 'json',
    'LaTeX': 'latex',
    'LESS': 'less', # spec: 'Less'
    'Lua': 'lua',
    'Makefile': 'makefile',
    'Markdown': 'markdown',
    'Objective-C': 'objective-c',
    #'Objective-C++': 'objective-cpp', # Not in CudaText
    'Perl': 'perl',
    #'Perl 6': 'perl6', # Not in CudaText
    'PHP': 'php',
    'PowerShell': 'powershell', # spec: 'Powershell'
    'Pug': 'jade',
    'Python': 'python',
    'R': 'r',
    'Razor': 'razor', # spec: 'Razor (cshtml)'
    'Ruby': 'ruby',
    'Rust': 'rust',
    #'SCSS': 'scss (syntax using curly brackets), sass (indented syntax)', #TODO
    'Scala': 'scala',
    #'ShaderLab': 'shaderlab', # not in CudaText
    'Bash script': 'shellscript', # spec: 'Shell Script (Bash)'
    'SQL': 'sql',
    'Swift': 'swift',
    'TypeScript': 'typescript',
    #'TypeScript React': 'typescriptreact', # Not in CudaText
    #'TeX': 'tex', # Not in CudaText
    #'Visual Basic': 'vb', # Not in CudaText
    'XML': 'xml',
    'XSLT': 'xsl', # spec: 'XSL'
    'YAML': 'yaml',
}
