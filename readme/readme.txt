Plugin for CudaText.
Adds support for Language Server Protocol (LSP) servers.
For each language server needs to be installed separately.

To use a specific server, at least a command to start the server process and a map of lexers
to supported language identifiers needs to be provided. List of language identifiers
can be seen here:
https://microsoft.github.io/language-server-protocol/specifications/specification-current/#-textdocumentitem-

For each LSP server, add config to the folder "settings" (folder of user.json CudaText config).
Config filename must be named lsp_*.json ("lsp_" prefix and ".json" suffix).

"Hover dialog" feature: dialog appears when you stop moving the mouse cursor, over some
identifier, with Ctrl-key pressed (Command-key on macOS).


Example for Python
------------------
Python LSP server can be installed in the Linux by command:
$ pip3 install python-language-server
It creates the script "~/.local/bin/pyls". Basic config would look like this:

  {
    "lexers": {
        "Python": "python",
        "RenamedPythonLexer": "python"
    },
    "cmd_unix": ["~/.local/bin/pyls"]
  }


Server options
--------------
Plugin supports 3 keys for running commands:
- "cmd_windows" for Windows,
- "cmd_macos" for macOS,
- "cmd_unix" for all other OS.

Each cmd-key must be a list of strings, e.g.
  "cmd_windows": ["C:\\Python_folder\\pyls.exe", "--param", "param"],


Server-specific options
-----------------------
Some servers can be additionally configured, this configuration can be placed
a) in the server config file settings/lsp_*.json
b) or in the project config file *.cuda-proj-lsp, near the project file *.cuda-proj
Use command "Plugins / LSP Client / Configure server for current project".

For example, Golang server "gopls" has docs about its options:
https://github.com/golang/tools/blob/master/gopls/doc/settings.md
Options can be written to:

a) settings/lsp_go.json
  ...
  "settings": {
    "gopls": {
        "hoverKind": "NoDocumentation"
    }
  }
  ...

b) project config myname.cuda-proj-lsp
  {
    "gopls": {
      "hoverKind": "NoDocumentation"
    }
  }


Plugin options
--------------
Plugin has the config file, which can be opened in CudaText by:
"Options / Settings-plugins / LSP Client / Config".
The possible options are listed in another text file in the LSP Client folder.


About
-----
Author: Shovel, https://github.com/halfbrained/
License: MIT
