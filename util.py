import pathlib
from urllib.parse import unquote, urlparse

import cudatext as ct

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
    return lang_ids.get(lex)

def langid2name(langid_):
    for name,langid in lang_ids.items():
        if langid == langid_:
            return name

def is_ed_visible(ed):
    h = ed.get_prop(ct.PROP_HANDLE_SELF)
    eds = (ct.ed_group(i) for i in range(8))
    return any(h == gred.get_prop(ct.PROP_HANDLE_SELF)  for gred in eds  if gred is not None)

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

# https://microsoft.github.io/language-server-protocol/specifications/specification-current/#-textdocumentitem-
lang_ids = {
    'ABAP': 'abap',
    'Windows Bat': 'bat',
    'Batch files': 'bat',
    'BibTeX': 'bibtex',
    'Clojure': 'clojure',
    'Coffeescript': 'coffeescript',
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
    'Handlebars': 'handlebars',
    'HTML': 'html',
    'Ini': 'ini',
    'Java': 'java',
    'JavaScript': 'javascript',
    'JavaScript React': 'javascriptreact',
    'JSON': 'json',
    'LaTeX': 'latex',
    'Less': 'less',
    'Lua': 'lua',
    'Makefile': 'makefile',
    'Markdown': 'markdown',
    'Objective-C': 'objective-c',
    'Objective-C++': 'objective-cpp',
    'Perl': 'perl',
    'Perl 6': 'perl6',
    'PHP': 'php',
    'Powershell': 'powershell',
    'Pug': 'jade',
    'Python': 'python',
    'R': 'r',
    'Razor (cshtml)': 'razor',
    'Ruby': 'ruby',
    'Rust': 'rust',
    #'SCSS': 'scss (syntax using curly brackets), sass (indented syntax)', #TODO
    'Scala': 'scala',
    'ShaderLab': 'shaderlab',
    'Shell Script (Bash)': 'shellscript',
    'Bash script': 'shellscript',
    'SQL': 'sql',
    'Swift': 'swift',
    'TypeScript': 'typescript',
    'TypeScript React': 'typescriptreact',
    'TeX': 'tex',
    'Visual Basic': 'vb',
    'XML': 'xml',
    'XSL': 'xsl',
    'YAML': 'yaml',
}