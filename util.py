import os
import pathlib
from urllib.parse import unquote, urlparse

import cudatext as ct

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


def path_to_uri(path):
    return pathlib.Path(path).as_uri()

def ed_uri(ed):
    fn = ed.get_filename()
    if fn:
        #return 'file://'+ed.get_filename()
        return path_to_uri(fn)
    else:
        return 'untitled://'+ed.get_prop(ct.PROP_TAB_TITLE)

def uri_to_path(uri):
    return unquote(urlparse(uri).path)

def collapse_path(path):
    if (path + os.sep).startswith(USER_DIR + os.sep):
        path = path.replace(USER_DIR, '~', 1)
    return path

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
